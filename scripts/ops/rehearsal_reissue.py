"""Re-issue a licence on the rehearsal Owner and free the phone to enrol fresh.

Why this exists. The Mi Note 10's installation is ACTIVE on aura_owner_rehearsal
with its device key bound, but activating (or retrying) requires the licence
key's plaintext -- which the phone deliberately never stores -- validated
against OWNER_LICENSE_PEPPER, which was recorded nowhere. Rather than depend on
either being remembered, this issues a NEW licence under a pepper we choose,
and clears the phone's stale server-side registration so the device can
register again as brand-new:

  * the old ANDROID installation is released (DEACTIVATED) and its device key
    REVOKED -- the exact staff actions licensing_admin exposes;
  * BUT a device key fingerprint is globally UNIQUE and a revoked row keeps
    it, so the phone cannot re-register the SAME keypair. The companion script
    therefore clears the app's data (`adb shell pm clear`), which makes Kotlin
    generate a fresh keypair and re-seed trust_store.json from the bundled
    anchor -- both of which are what we want anyway.

Run from owner/ with the Owner venv and OWNER_DATABASE_URL pointing at the
rehearsal DB; the pepper is passed in and printed back so the two scripts can
never disagree about it. Idempotent per --tag: re-running with the same tag
returns the same issuance (full_key None on replay -- so keep the printed key).

    python ../scripts/ops/rehearsal_reissue.py --pepper <p> [--tag YYYYMMDD]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.getcwd())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pepper", required=True)
    parser.add_argument("--tag", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--plan-code", default="REHEARSAL-RETAIL")
    parser.add_argument("--customer-name", default="Rehearsal phone shop")
    parser.add_argument(
        "--keep-device", action="store_true",
        help="Do NOT release/revoke the phone's registration. Instead RE-PARENT its existing ACTIVE "
             "ANDROID installation (and device key) onto the newly issued licence, so the phone keeps its "
             "keypair AND its local data: activation then hits Owner's idempotent same-device path and "
             "returns the signed assertion without a fresh enrolment. Non-destructive; preferred when the "
             "phone still holds a healthy device key -- which it does after the 2026-09-04 trust fix.",
    )
    args = parser.parse_args()

    from sqlalchemy import select

    from app import create_app
    from app.commercial_ops.device_slot_ops import release_device_slot
    from app.extensions import db_session
    from app.licensing.issuance import issue_license_direct
    from app.licensing_service import device_identity
    from app.models.catalog import Plan, Platform
    from app.models.installations import Installation
    from app.models.staff import StaffUser

    app = create_app()
    with app.app_context():
        actor = db_session.execute(
            select(StaffUser).where(StaffUser.is_super_admin.is_(True)).order_by(StaffUser.created_at)
        ).scalars().first()
        if actor is None:
            print("NO SUPERADMIN in this DB -- run `flask create-superadmin` first (see owner_bootstrap.sh)")
            return 2

        plan = db_session.execute(select(Plan).where(Plan.plan_code == args.plan_code)).scalars().first()
        if plan is None:
            print(f"plan {args.plan_code!r} not found -- run `flask seed-plans`")
            return 2

        android = db_session.execute(select(Platform).where(Platform.platform_code == "ANDROID")).scalars().first()
        stale = []
        if android is not None:
            stale = db_session.execute(
                select(Installation).where(Installation.platform_id == android.id, Installation.status == "ACTIVE")
            ).scalars().all()
        if not args.keep_device:
            for installation in stale:
                key = device_identity.get_active_device_key(installation.id)
                if key is not None:
                    device_identity.revoke_device_key(key, actor.id)
                release_device_slot(installation, reason=f"rehearsal re-issue {args.tag}: phone re-enrols fresh",
                                    actor_staff_user_id=actor.id)
                print(f"released ANDROID installation {installation.id} (key revoked: {key is not None})")

        result = issue_license_direct(
            new_customer_legal_name=args.customer_name,
            plan_id=plan.id,
            extra_devices=0,
            idempotency_key=f"rehearsal-phone-{args.tag}",
            actor_staff_user_id=actor.id,
            license_pepper=args.pepper,
        )

        if args.keep_device:
            # Move the phone's live registration under the new licence. Owner's
            # activation then takes the "same device retrying on THIS licence"
            # branch (activation.py: existing_device_key ACTIVE and
            # installation.license_id == locked_license.id) and reuses the
            # installation instead of counting a new slot -- no revocation, no
            # fingerprint UNIQUE collision, no wipe on the phone.
            for installation in stale:
                installation.license_id = result["license_id"]
                installation.subscription_id = result["subscription_id"]
                installation.customer_id = result["customer_id"]
                print(f"re-parented ANDROID installation {installation.id} onto licence {result['license_id']}")
        db_session.commit()

    print("PEPPER_USED:", args.pepper)
    print("LICENSE_ID:", result["license_id"])
    if result.get("full_key"):
        print("LICENSE_KEY:", result["full_key"])
    else:
        print("LICENSE_KEY: (replay of tag -- key was printed on the first run; use a new --tag to issue another)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
