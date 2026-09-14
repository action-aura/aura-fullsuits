"""The operator's side of hub mode: "Connect a device", and who is connected.

This blueprint is registered on the PRODUCT'S OWN LOOPBACK APP -- the one
already serving the POS UI on 127.0.0.1 -- and never on the LAN-facing relay
app built by `listener.py`. That split is the whole point and is worth being
blunt about, because putting these routes on the wrong app would be a serious
hole rather than an untidiness:

    `routes.py`       is served to the LAN. It speaks the sync wire contract
                      to paired devices and nothing else.
    `admin_routes.py` (this file) is served on loopback only. It MINTS
                      pairing codes and REVOKES devices.

A pairing code is the credential that lets a new device join the shop (see
`pairing.py`: TLS authenticates the server to the client, not the client to
the server, so the code is the actual authentication on `/pair`). An endpoint
that hands those out must therefore be reachable only by someone already
logged in as an admin on the till itself -- never by anything on the wifi. If
these routes ever end up on the relay app, anyone who can reach the hub's port
can mint themselves a pairing code and join the shop.

ANDROID IMPORT SAFETY, the same constraint `device_routes.py`'s docstring
spells out: this module must stay safe to import EAGERLY under Chaquopy, so
it imports nothing that pulls in `cryptography` at module scope.
`tls_identity` and `listener` both do, so they are imported INSIDE the
functions that need them. A module-scope import here would reintroduce the
ModuleNotFoundError('cryptography') regression that constraint exists to
prevent.
"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, jsonify, session

from commercial_runtime.identity.mt_auth import mt_login_required

_log = logging.getLogger(__name__)


def _admin_only():
    """Same predicate the identity routes use (`session['mt_role'] == 'admin'`),
    repeated rather than abstracted because it is two lines and hiding it
    behind a helper elsewhere would make it harder, not easier, to audit which
    routes are admin-gated.

    Deliberately NOT delegated to a capability code: minting a pairing code is
    not "configure something", it is "let a new device into this shop", which
    is the owner's decision by definition. `ROLE_MANAGER` excludes
    `CAP_EMPLOYEES` for the same class of reason (AUDIT-032, self-approval)."""
    if session.get('mt_role') != 'admin':
        return jsonify({'success': False, 'error': 'Admin only.'}), 403
    return None


def make_site_relay_admin_blueprint(*, hub_provider,
                                    blueprint_name='site_relay_admin',
                                    url_prefix='/api/site-relay'):
    """`hub_provider` is a zero-arg callable returning the running hub's
    handle, or None when this install is not a hub.

    A callable rather than the handle itself because hub mode starts during
    `init_app()`, potentially AFTER this blueprint is registered -- capturing
    the value at registration time would pin it to None forever and the routes
    would report "not a hub" on a machine that is one. That is precisely the
    kind of ordering bug that looks fine in review and is dead on the machine.

    The handle is expected to expose: `pairing_codes`, `pin`, `port`,
    `get_conn`, and `addresses()`.
    """
    bp = Blueprint(blueprint_name, __name__, url_prefix=url_prefix)

    def _hub_or_error():
        hub = hub_provider()
        if hub is None:
            # 200 with enabled:false, not 404. The UI asks this on every
            # Settings load, including on the overwhelming majority of
            # installs that are not hubs; a 404 there is a console error on a
            # perfectly healthy till and trains people to ignore console
            # errors.
            return None, (jsonify({'success': True, 'enabled': False}), 200)
        return hub, None

    @bp.route('/status', methods=['GET'])
    @mt_login_required
    def status():
        """What an operator needs to answer "is the LAN working, and who is on
        it?" without reading a log file."""
        hub, not_a_hub = _hub_or_error()
        if not_a_hub:
            return not_a_hub

        from . import store  # local: keeps module import cheap and Android-safe

        conn = hub.get_conn()
        try:
            rows = conn.execute(
                "SELECT installation_id, label, paired_at, revoked_at "
                "FROM site_paired_devices ORDER BY paired_at"
            ).fetchall()
            devices = [{
                'installation_id': r['installation_id'],
                'label': r['label'],
                'paired_at': r['paired_at'],
                'revoked': r['revoked_at'] is not None,
            } for r in rows]
        finally:
            conn.close()

        return jsonify({
            'success': True,
            'enabled': True,
            'port': hub.port,
            # The SPKI pin is a public key fingerprint, not a secret -- it is
            # printed on the pairing QR for anyone to scan. Showing it here
            # lets an operator read it to support over the phone.
            'spki_pin': hub.pin,
            'addresses': hub.addresses(),
            'devices': devices,
        })

    @bp.route('/pair-code', methods=['POST'])
    @mt_login_required
    def issue_pair_code():
        """Mint one short-lived, single-use pairing code and return the payload
        that becomes the QR. This is the "Connect a device" button.

        ADMIN ONLY -- see `_admin_only`. This is the most sensitive route in
        the file: its output admits a new device to the shop."""
        denied = _admin_only()
        if denied:
            return denied

        hub, not_a_hub = _hub_or_error()
        if not_a_hub:
            return not_a_hub

        from . import pairing

        code = hub.pairing_codes.issue()
        payload = pairing.pairing_payload(
            base_url=hub.primary_url(),
            spki_pin=hub.pin,
            hub_installation_id=hub.installation_id,
            hub_device_public_key=hub.device_public_key,
            pairing_code=code,
        )
        # The QR is rendered SERVER-SIDE rather than in the browser, because
        # this product ships no frontend QR library and must not acquire one:
        # a shop's till is routinely offline, so a CDN is not an option, and
        # vendoring an encoder to do what a Python dependency we already ship
        # does is pure duplication.
        #
        # `render_qr_svg_data_uri` lives under einvoicing/ only because that is
        # what first needed it -- it is a pure payload-to-data-URI function
        # with no e-invoicing semantics, and importing it enables nothing
        # (e-invoicing stays off unless opted in). Reused rather than copied,
        # because a second QR encoder in this repo would be a second thing to
        # keep correct. Imported lazily for the Android import-safety rule in
        # this module's docstring.
        from commercial_runtime.einvoicing.qr import render_qr_svg_data_uri

        qr_text = json.dumps(payload, separators=(',', ':'), sort_keys=True)

        # Never logged, not even at debug: this value IS the credential, and
        # log files get pasted into support tickets.
        _log.info("Site relay: a pairing code was issued by an admin. "
                  "It is single-use and expires in %d seconds.",
                  pairing.PAIRING_CODE_TTL_SECONDS)
        return jsonify({'success': True, 'payload': payload,
                        'qr_text': qr_text,
                        'qr_svg': render_qr_svg_data_uri(qr_text, scale=5),
                        'expires_in_seconds': pairing.PAIRING_CODE_TTL_SECONDS})

    @bp.route('/devices/<installation_id>/revoke', methods=['POST'])
    @mt_login_required
    def revoke(installation_id):
        """Cut a device off this hub immediately, without waiting for Owner.

        Design §5's "Local mitigation for the local threat": the urgent case
        is a fired employee's tablet standing in the restaurant right now, and
        the Owner roster refresh that would carry a suspension needs internet
        the shop may not have for days. This takes effect on the device's very
        next request."""
        denied = _admin_only()
        if denied:
            return denied

        hub, not_a_hub = _hub_or_error()
        if not_a_hub:
            return not_a_hub

        from . import store

        conn = hub.get_conn()
        try:
            if store.lookup_paired_device(conn, installation_id) is None:
                return jsonify({'success': False,
                                'error': 'That device is not paired to this hub.'}), 404
            store.revoke_device(conn, installation_id)
            conn.commit()
        finally:
            conn.close()

        _log.info("Site relay: device %s revoked locally by an admin.", installation_id)
        return jsonify({'success': True, 'installation_id': installation_id, 'revoked': True})

    return bp
