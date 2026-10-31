"""Flask CLI commands: Super Admin bootstrap, RBAC seeding, release-manifest
import, Phase 8 commercial-operations jobs."""
from __future__ import annotations

import getpass
import json
from datetime import date, timedelta

import click
from flask import Flask
from sqlalchemy import select

from app.audit.services import record as audit_record
from app.catalog.services import import_release_manifest, seed_canonical_catalog, seed_canonical_plans
from app.extensions import db_session
from app.licensing_service import signing as signing_service
from app.licensing_service.offline_policy import seed_default_offline_policy
from app.models.staff import MfaCredential, Role, StaffUser
from app.operational_reports.periods import jordan_today
from app.security.passwords import PasswordPolicyError, hash_password
from app.staff.services import seed_rbac as seed_rbac_service


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-rbac")
    def seed_rbac():
        """Seed the canonical permission and role catalog (idempotent)."""
        result = seed_rbac_service()
        click.echo(f"Seeded {result['permissions']} permissions and {result['roles']} roles.")

    @app.cli.command("seed-catalog")
    def seed_catalog():
        """Seed canonical products/platforms/release channels/entitlement
        definitions/DRAFT add-ons (idempotent, no fake customer data)."""
        result = seed_canonical_catalog()
        click.echo(json.dumps(result))

    @app.cli.command("seed-plans")
    def seed_plans_cmd():
        """Seed ONE minimal, immediately-sellable placeholder plan per
        canonical product (idempotent). Run 'flask seed-catalog' first --
        without a plan, `issue_license_direct` has nothing to issue against
        and a fresh Owner cannot cut its first licence key.

        PLACEHOLDER PRICING ONLY: $99.00 flat, ONE_TIME, per product. This is
        not a real commercial price -- set real prices from the Owner UI
        (catalog -> plan -> add price) before selling anything for real."""
        try:
            result = seed_canonical_plans()
        except ValueError as exc:
            raise click.ClickException(str(exc))
        click.echo(json.dumps(result))

    @app.cli.command("seed-packages")
    @click.option("--retire-other-retail-plans", is_flag=True, default=False,
                  help="Retire every other sellable Aura Retail plan (a plan an open sale still needs is kept).")
    def seed_packages_cmd(retire_other_retail_plans: bool):
        """Put the owner's three Aura Retail packages (2026-10-24) and the
        50 JOD extra device pair into the price list (idempotent). Creates
        what is missing, never changes what exists: a package or price that
        differs is REPORTED under "differs". See app/catalog/package_seed.py."""
        from app.catalog.package_seed import seed_packages

        try:
            result = seed_packages(retire_other_retail_plans=retire_other_retail_plans)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        click.echo(json.dumps(result, indent=2))
        if result["differs"]:
            click.echo("WARNING: some packages or prices differ from the owner's list and were left as they are.")
        if result["other_sellable_retail_plans"]:
            click.echo("NOTE: other Aura Retail plans are still on the sale screen: "
                       + ", ".join(result["other_sellable_retail_plans"])
                       + " (run again with --retire-other-retail-plans to retire them).")
        if result["kept_for_open_sales"]:
            click.echo("NOTE: kept on sale because an open sale needs them: " + ", ".join(result["kept_for_open_sales"]))
        if result["single_extra_device_on_sale"]:
            click.echo("NOTE: the single EXTRA_DEVICE add-on is on sale; the quote screen can still sell one device "
                       "at a time. Retire it on the catalog screen if no open quote uses it.")

    @app.cli.command("create-superadmin")
    @click.option("--email", prompt=True)
    @click.option("--display-name", prompt="Display name")
    @click.option("--non-interactive", is_flag=True, default=False, help="Read password from OWNER_BOOTSTRAP_PASSWORD env var instead of a prompt.")
    def create_superadmin(email: str, display_name: str, non_interactive: bool):
        """Create the first Super Admin. Refuses if any Super Admin already exists.
        Never prints the password. Records a bootstrap security/audit event."""
        existing = db_session.execute(select(StaffUser).where(StaffUser.is_super_admin.is_(True))).scalars().first()
        if existing is not None:
            raise click.ClickException("A Super Admin already exists -- refusing to bootstrap a second one via this command.")

        if non_interactive:
            import os

            password = os.environ.get("OWNER_BOOTSTRAP_PASSWORD", "")
            if not password:
                raise click.ClickException("OWNER_BOOTSTRAP_PASSWORD is not set.")
        else:
            password = getpass.getpass("Password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                raise click.ClickException("Passwords did not match.")

        try:
            password_hash = hash_password(password)
        except PasswordPolicyError as exc:
            raise click.ClickException(str(exc))

        staff = StaffUser(
            email=email.strip().lower(),
            display_name=display_name,
            password_hash=password_hash,
            is_super_admin=True,
            mfa_required=True,
        )
        db_session.add(staff)
        db_session.flush()

        role = db_session.execute(select(Role).where(Role.code == "SUPER_ADMIN")).scalars().first()
        if role is not None:
            from app.models.staff import StaffRoleAssignment

            db_session.add(StaffRoleAssignment(staff_user_id=staff.id, role_id=role.id))
        db_session.commit()

        audit_record(
            actor_staff_user_id=staff.id,
            actor_role_snapshot="SUPER_ADMIN",
            action_code="SUPERADMIN_BOOTSTRAPPED",
            entity_type="staff_user",
            entity_public_id=str(staff.id),
        )
        click.echo(f"Super Admin created: {staff.email} ({staff.id}). MFA enrollment is required at first login.")

    @app.cli.command("rekey-mfa")
    @click.option("--dry-run", is_flag=True, default=False, help="Report what would change; write nothing.")
    def rekey_mfa_cmd(dry_run: bool):
        """Re-encrypt every TOTP secret from the legacy OWNER_SECRET_KEY-derived
        key onto OWNER_MFA_ENCRYPTION_KEY (review D SEC-02). Idempotent: rows
        already on the dedicated key are left alone. Run this BEFORE rotating
        OWNER_SECRET_KEY, with both keys set. Rows that open under neither key
        are reported, never modified."""
        from flask import current_app

        from app.security.mfa import decrypt_totp_secret, reencrypt_if_legacy

        mfa_key = current_app.config.get("MFA_ENCRYPTION_KEY") or None
        if not mfa_key:
            raise click.ClickException("OWNER_MFA_ENCRYPTION_KEY is not set -- nothing to re-encrypt onto.")
        app_key = current_app.config["SECRET_KEY"]
        rekeyed = already_current = unreadable = 0
        for cred in db_session.execute(select(MfaCredential)).scalars().all():
            upgraded = reencrypt_if_legacy(cred.totp_secret_encrypted, app_key, mfa_key)
            if upgraded is not None:
                rekeyed += 1
                if not dry_run:
                    cred.totp_secret_encrypted = upgraded
            elif decrypt_totp_secret(cred.totp_secret_encrypted, app_key, mfa_key) is not None:
                already_current += 1
            else:
                unreadable += 1
        if not dry_run:
            db_session.commit()
            audit_record(
                actor_staff_user_id=None,
                actor_role_snapshot=None,
                action_code="MFA_SECRETS_REKEYED",
                entity_type="mfa_credential",
                entity_public_id="all",
                reason=f"rekeyed={rekeyed} already_current={already_current} unreadable={unreadable}",
            )
        click.echo(
            f"{'DRY RUN: would re-encrypt' if dry_run else 'Re-encrypted'} {rekeyed}; "
            f"already current {already_current}; unreadable under either key {unreadable}."
        )

    @app.cli.command("import-release-manifest")
    @click.argument("manifest_path", type=click.Path(exists=True))
    def import_release_manifest_cmd(manifest_path: str):
        """Import product/version release metadata from a JSON manifest.
        Never uploads or stores customer data; rejects duplicate conflicting
        release records; never silently overwrites an existing release."""
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        result = import_release_manifest(manifest, actor_staff_user_id=None)
        click.echo(json.dumps(result, indent=2, default=str))

    @app.cli.command("seed-offline-policy")
    def seed_offline_policy_cmd():
        """Seed the default offline-grace policy (idempotent, safe non-unlimited defaults)."""
        policy = seed_default_offline_policy()
        click.echo(f"Default offline policy ready: {policy.policy_code} (v{policy.policy_version})")

    @app.cli.command("seed-website-intake")
    def seed_website_intake_cmd():
        """Create the "Website intake" system account that website demo
        requests (POST /api/intake/website-lead) are recorded as (idempotent;
        re-asserts that it cannot log in). Safe to re-run on every deploy."""
        from flask import current_app

        from app.leads.intake_routes import effective_intake_secret
        from app.leads.website_intake import seed_website_intake_actor

        # Invisible unless opted in: with no (or an unusable) intake secret the
        # feature is off, so create nothing -- not even the system account.
        if not effective_intake_secret(current_app.config):
            click.echo(json.dumps({"skipped": "website intake off"}))
            return
        click.echo(json.dumps(seed_website_intake_actor()))

    @app.cli.command("bootstrap-dev")
    def bootstrap_dev_cmd():
        """Runs every idempotent seed step a fresh Owner deployment needs
        (RBAC, catalog, plans, offline policy), generates + activates a
        signing key if none is active yet, then runs 'commercial preflight'
        and reports the result. The one command owner/README.md's no-Docker
        quick start now points to, instead of five separate steps an
        operator can forget one of.

        2026-09-22: a real Owner -> licence -> till -> sale end-to-end run
        found 'flask seed-plans' had never been run anywhere in this repo --
        not in the README, not in docker-compose.yml, not in any deploy doc
        -- so a fresh Owner could not issue a single licence, with no error
        until someone actually tried to issue one. This command exists so
        that gap (and its siblings this same run found: no active signing
        key, an unseeded RBAC table) is caught and closed in one run rather
        than rediscovered by hand again. It cannot set OWNER_EXTERNAL_API_
        ENABLED or OWNER_LICENSE_PEPPER for you -- both are read once at
        process boot (app/config.py), so a value fixed after this command
        starts requires a restart, not a rerun; the preflight report at the
        end names exactly which of those, if any, still need it.

        Deliberately does NOT create a Super Admin -- 'create-superadmin'
        stays its own explicit, audited, password-bearing step, the same
        reason 'seed-plans' was kept a separate command from 'seed-catalog'
        (see seed_canonical_plans()'s own docstring)."""
        from app.commercial_ops.preflight import run_preflight

        rbac_result = seed_rbac_service()
        click.echo(f"[1/5] RBAC: {rbac_result['permissions']} permissions, {rbac_result['roles']} roles.")

        catalog_result = seed_canonical_catalog()
        click.echo(f"[2/5] Catalog: {json.dumps(catalog_result)}")

        try:
            plans_result = seed_canonical_plans()
        except ValueError as exc:
            # Should be unreachable -- seed_canonical_catalog() just ran
            # above -- but never silently swallow a real failure here.
            raise click.ClickException(f"seed-plans failed immediately after seed-catalog ran: {exc}")
        click.echo(f"[3/5] Plans: {json.dumps(plans_result)}")

        policy = seed_default_offline_policy()
        click.echo(f"[4/5] Offline policy: {policy.policy_code} (v{policy.policy_version})")

        existing_key = signing_service.get_active_signing_key()
        if existing_key is not None:
            click.echo(f"[5/5] Signing key: already active ({existing_key.key_id}) -- left untouched.")
        else:
            row = signing_service.generate_signing_key(app.config["SIGNING_KEY_DIRECTORY"])
            signing_service.activate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], row.key_id)
            click.echo(f"[5/5] Signing key: generated and activated {row.key_id}.")

        click.echo("")
        click.echo("Running 'flask commercial preflight' for the full readiness picture...")
        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        click.echo(json.dumps(result.as_dict(), indent=2))
        if not result.ok:
            raise click.ClickException(
                "bootstrap-dev finished its seed steps but preflight still FAILS -- see checks above. Two "
                "common remaining causes this command cannot fix for you: OWNER_EXTERNAL_API_ENABLED is not "
                "\"true\" (a running process must be restarted after setting it, not just rerun), or "
                "OWNER_LICENSE_PEPPER is unset."
            )
        click.echo("")
        click.echo("Preflight OK. Next: 'flask create-superadmin --email you@example.com --display-name \"Your Name\"'.")

    @app.cli.group("licensing")
    def licensing_group():
        """Server signing-key management for the Phase 6 Licensing & Activation Service."""

    @licensing_group.command("generate-signing-key")
    def generate_signing_key_cmd():
        """Generates a new Ed25519 signing key pair in DRAFT status. Never
        prints private-key material -- only the new key ID."""
        row = signing_service.generate_signing_key(app.config["SIGNING_KEY_DIRECTORY"])
        click.echo(f"Generated signing key {row.key_id} (status=DRAFT). Activate it with 'flask licensing activate-signing-key {row.key_id}'.")

    @licensing_group.command("activate-signing-key")
    @click.argument("key_id")
    def activate_signing_key_cmd(key_id: str):
        """Activates a DRAFT/RETIRED key as the current signing key, retiring whichever key was previously active."""
        row = signing_service.activate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], key_id)
        click.echo(f"Activated signing key {row.key_id}.")

    @licensing_group.command("rotate-signing-key")
    @click.option("--reason", default="scheduled_rotation")
    def rotate_signing_key_cmd(reason: str):
        """Generates a fresh signing key and activates it in one step, retiring the previous active key (which remains verifiable)."""
        row = signing_service.rotate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], reason)
        click.echo(f"Rotated to new signing key {row.key_id}. Previous active key retired (still verifiable).")

    @licensing_group.command("export-public-keys")
    def export_public_keys_cmd():
        """Prints the public key set as JSON -- the same shape served by GET /api/licensing/v1/signing-keys."""
        click.echo(json.dumps(signing_service.export_public_keys(), indent=2))

    @licensing_group.command("verify-signing-key-health")
    def verify_signing_key_health_cmd():
        """Runs a real sign/verify round-trip against the active key and reports OK/FAILED."""
        result = signing_service.verify_signing_key_health(app.config["SIGNING_KEY_DIRECTORY"])
        click.echo(json.dumps(result))
        if result["status"] != "OK":
            raise click.ClickException(result["detail"])

    @licensing_group.command("transfer-registry-id")
    @click.option("--registry-device-id", required=True, help="The shop-authority registry device id to move.")
    @click.option("--to", "to_installation_id", required=True, help="Installation id that becomes the id's holder.")
    @click.option("--actor", required=True, help="Email of the staff user performing (and accountable for) the transfer.")
    @click.option("--reason", required=True, help="Why this installation is the real computer for the id.")
    def transfer_registry_id_cmd(registry_device_id: str, to_installation_id: str, actor: str, reason: str):
        """The only way a registry device id changes installation or is handed
        to one by staff (AUDIT-033 / review V3o R4, V4o G3). MOVE: the id's
        current holder is on the same licence chain as --to. ASSIGN: nobody
        holds the id (e.g. a computer the history guard refused). The actor
        must be an ACTIVE staff account; the audit row records who, their
        role, why, from (null for an assign) and to."""
        import uuid as _uuid

        from app.licensing_service.registry_pin import RegistryTransferError, transfer_registry_device_id

        staff = db_session.execute(select(StaffUser).where(StaffUser.email == actor)).scalars().first()
        if staff is None:
            raise click.ClickException(f"No staff user with email {actor!r}.")
        if not staff.is_active or staff.disabled_at is not None:
            raise click.ClickException(f"Staff user {actor!r} is disabled and cannot be the accountable actor.")
        try:
            target_id = _uuid.UUID(to_installation_id)
        except ValueError:
            raise click.ClickException("--to must be an installation id (a UUID).")
        try:
            transfer_registry_device_id(
                db_session, registry_device_id=registry_device_id, to_installation_id=target_id,
                actor_staff_user_id=staff.id, reason=reason,
            )
        except RegistryTransferError as exc:
            raise click.ClickException(f"Transfer refused: {exc.code}")
        click.echo(f"Registry id {registry_device_id} now pinned to installation {target_id}.")

    @app.cli.group("commercial")
    def commercial_group():
        """Phase 8 commercial-operations jobs (Parts G/I/J/R)."""

    @commercial_group.command("expiry-scan")
    @click.option("--apply", "apply_", is_flag=True, default=False, help="Actually write notifications/transitions. Default is dry-run (report only).")
    @click.option("--as-of", default=None, help="ISO date to evaluate against (default: today).")
    def expiry_scan_cmd(apply_: bool, as_of: str | None):
        """Warning notifications + date-driven PAST_DUE/EXPIRED transitions
        (Parts G/H/I). Report-only unless --apply is given -- this also
        covers what the governing spec calls "notification-scan": the same
        walk over subscriptions generates both the warning notifications
        and the status transitions in one pass, since they share the same
        per-subscription policy resolution and date arithmetic."""
        from app.commercial_ops.expiry_scan import run_expiry_scan

        as_of_date = date.fromisoformat(as_of) if as_of else None
        result = run_expiry_scan(as_of=as_of_date, dry_run=not apply_)
        click.echo(
            json.dumps(
                {
                    "as_of": result.as_of.isoformat(),
                    "dry_run": result.dry_run,
                    "scanned_count": result.scanned_count,
                    "notifications_created": result.notifications_created,
                    "notifications_deduped": result.notifications_deduped,
                    "transitioned_to_past_due": result.transitioned_to_past_due,
                    "transitioned_to_expired": result.transitioned_to_expired,
                    "findings": [
                        {"subscription_id": f.subscription_id, "action": f.action, "detail": f.detail}
                        for f in result.findings
                    ]
                    if result.dry_run
                    else [],
                },
                indent=2,
            )
        )

    @commercial_group.command("reconcile")
    @click.option("--apply", "apply_", is_flag=True, default=False, help="Actually write notifications. Default is dry-run (report only).")
    @click.option("--as-of", default=None, help="ISO date to evaluate the state-inconsistency check against (default: today).")
    def reconcile_cmd(apply_: bool, as_of: str | None):
        """Cross-record consistency and stalled-workflow detection (Part Q).
        Report-only unless --apply is given. NEVER mutates any commercial
        record itself -- only ever surfaces a notification for a human to
        act on."""
        from app.commercial_ops.reconciliation import run_reconciliation

        as_of_date = date.fromisoformat(as_of) if as_of else None
        result = run_reconciliation(as_of=as_of_date, dry_run=not apply_)
        click.echo(
            json.dumps(
                {
                    "as_of": result.as_of.isoformat(),
                    "dry_run": result.dry_run,
                    "licenses_checked": result.licenses_checked,
                    "renewals_checked": result.renewals_checked,
                    "pending_activations_checked": result.pending_activations_checked,
                    "notifications_created": result.notifications_created,
                    "notifications_deduped": result.notifications_deduped,
                    "findings": [
                        {"check": f.check, "entity_type": f.entity_type, "entity_id": f.entity_id, "detail": f.detail}
                        for f in result.findings
                    ]
                    if result.dry_run
                    else [],
                },
                indent=2,
            )
        )

    @commercial_group.command("apply-licence-stops")
    @click.option("--apply", "apply_", is_flag=True, default=False, help="Actually stop the licences. Default is dry-run (count only).")
    def apply_licence_stops_cmd(apply_: bool):
        """Step 0 (2026-10-24): stop every licence whose refund's 2 days are
        over (licensing/scheduled_stops.py). The product's check-in and
        activation already apply a licence's own stop before reading it; this
        job brings the Owner screens in line for the rest. Safe to run as
        often as wanted: each stop is applied once."""
        from app.licensing.scheduled_stops import apply_due_licence_stops, count_due_licence_stops

        if apply_:
            # No scheduler-owner gate (unlike reports): each stop is applied
            # once under a row lock, so two hosts running it at once is safe.
            click.echo(json.dumps({"dry_run": False, "stops_applied": apply_due_licence_stops()}, indent=2))
        else:
            click.echo(json.dumps({"dry_run": True, "stops_due": count_due_licence_stops()}, indent=2))

    @app.cli.group("commissions")
    def commissions_group():
        """Step 0 commission ladder: put sellers on the owner's monthly ladder."""

    @commissions_group.command("seed-ladder-plan")
    @click.option("--actor-email", required=True, help="The Super Admin recording this.")
    def seed_ladder_plan_cmd(actor_email: str):
        """Creates the two ladder plans if missing: SALES_LADDER (Package 1,
        rule MONTHLY_LADDER) and SALES_LADDER_MONTHLY_PKGS (Packages 2 + 3,
        rule MONTHLY_PKG_LADDER), both JOD. Never changes an existing one."""
        from app.commissions.ladder_setup import seed_ladder_plans

        actor = _staff_by_email(actor_email, require_super_admin=True)
        results = seed_ladder_plans(actor_staff_user_id=actor.id)
        click.echo(json.dumps([{"plan_code": plan.plan_code, "created": created} for plan, created in results],
                              indent=2))

    @commissions_group.command("assign-ladder")
    @click.argument("seller_email")
    @click.option("--actor-email", required=True, help="The Super Admin recording this.")
    @click.option("--from", "effective_from", default=None, help="ISO date the seller moves to the ladder (default: today).")
    def assign_ladder_cmd(seller_email: str, actor_email: str, effective_from: str | None):
        """Moves one seller onto BOTH ladder plans from a date (closing
        their current non-ladder plan, like every plan assignment). Sales
        qualifying from that date count on the ladder of their package
        family; earlier ones keep the rule they were paid under."""
        from app.commissions.ladder_setup import assign_seller_to_ladder

        actor = _staff_by_email(actor_email, require_super_admin=True)
        seller = _staff_by_email(seller_email)
        start = date.fromisoformat(effective_from) if effective_from else jordan_today()
        try:
            assignment = assign_seller_to_ladder(seller.id, effective_from=start, actor_staff_user_id=actor.id)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        click.echo(json.dumps({"seller": seller_email, "effective_from": assignment.effective_from.isoformat()}, indent=2))

    def _staff_by_email(email: str, *, require_super_admin: bool = False):
        from app.models.staff import StaffUser

        from sqlalchemy import func

        staff = db_session.query(StaffUser).filter(func.lower(StaffUser.email) == email.strip().lower()).first()
        if staff is None:
            raise click.ClickException(f"No staff user {email}.")
        if require_super_admin and not getattr(staff, "is_super_admin", False):
            raise click.ClickException(f"{email} is not a Super Admin.")
        return staff

    @commercial_group.command("preflight")
    def commercial_preflight_cmd():
        """Phase 8V-P2 Part C: deterministic trust-anchor + permission-seed
        environment check. Read-only, no secrets printed. Exits nonzero on
        any blocking mismatch -- run this before any real activation/
        renewal validation session, and especially before building a
        product installer that will embed the current trust_anchor.json."""
        from app.commercial_ops.preflight import run_preflight

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        click.echo(json.dumps(result.as_dict(), indent=2))
        if not result.ok:
            raise click.ClickException("Preflight FAILED -- see checks above.")

    @commercial_group.command("device-limit-scan")
    @click.option("--apply", "apply_", is_flag=True, default=False, help="Actually write notifications. Default is dry-run (report only).")
    @click.option("--as-of", default=None, help="ISO date to evaluate against (default: today).")
    def device_limit_scan_cmd(apply_: bool, as_of: str | None):
        """Over-limit device-slot detection (Part P). Report-only unless
        --apply is given. NEVER deactivates or replaces any installation
        itself -- only ever creates an internal notification for a human to
        act on via release_device_slot()/replace_device_slot() or a
        device-allowance renewal."""
        from app.commercial_ops.device_slot_ops import scan_over_limit_licenses

        as_of_date = date.fromisoformat(as_of) if as_of else None
        result = scan_over_limit_licenses(as_of=as_of_date, dry_run=not apply_)
        click.echo(
            json.dumps(
                {
                    "as_of": result.as_of.isoformat(),
                    "dry_run": result.dry_run,
                    "scanned_count": result.scanned_count,
                    "notifications_created": result.notifications_created,
                    "notifications_deduped": result.notifications_deduped,
                    "findings": [
                        {
                            "license_id": f.license_id, "active_count": f.active_count,
                            "effective_limit": f.effective_limit, "dedup_key": f.dedup_key,
                        }
                        for f in result.findings
                    ]
                    if result.dry_run
                    else [],
                },
                indent=2,
            )
        )

    @commercial_group.command("housekeeping")
    @click.option("--apply", "apply_", is_flag=True, default=False, help="Actually delete/expire. Default is dry-run (counts only).")
    def housekeeping_cmd(apply_: bool):
        """Cleanup that had a function but no caller (found 2026-10-10): expire
        stale SENT quotes, and purge expired replay nonces, expired idempotency
        records and old rate-limit counters. Each existing function runs with
        its own default retention; rate-limit counters have none, so anything
        older than a day is purged (the longest window is 5 minutes). Dry-run
        reports what --apply would touch and calls none of the deleting paths."""
        from datetime import datetime, timezone

        from app import privacy_retention
        from app.commercial_sales import quotes
        from app.licensing_service import idempotency, ratelimit, replay
        from app.models.commercial_sales import Quote
        from app.models.licensing_service import ExternalIdempotencyRecord, RateLimitCounter, SecurityNonceRecord
        from app.sync import bootstrap_routes

        now = datetime.now(timezone.utc)
        counter_cutoff = now - timedelta(days=1)

        if apply_:
            result = {
                "dry_run": False,
                "quotes_expired": quotes.expire_stale_quotes(),
                "nonces_purged": replay.purge_expired_nonces(),
                "idempotency_records_purged": idempotency.purge_expired(),
                "rate_limit_counters_purged": ratelimit.purge_old_counters(older_than=counter_cutoff),
                # SEC-09 (privacy): see app/privacy_retention.py.
                "email_bodies_cleared": privacy_retention.clear_old_email_bodies(),
                "login_attempts_purged": privacy_retention.purge_old_login_attempts(),
                # Step 2 CA1: customer website accounts (see privacy_retention.py).
                "customer_login_attempts_purged": privacy_retention.purge_customer_login_attempts(),
                "customer_sessions_and_tokens_purged": privacy_retention.purge_expired_customer_sessions_and_tokens(),
                "customer_link_mail_bodies_cleared": privacy_retention.clear_customer_link_mail_bodies(),
                # SYNC-BOOT Phase 3: expire unfinished bootstrap requests and delete
                # every encrypted blob nobody may read again.
                "sync_bootstrap": bootstrap_routes.sweep(apply=True),
            }
        else:
            def _count(model, *conditions) -> int:
                return db_session.query(model).filter(*conditions).count()

            result = {
                "dry_run": True,
                "quotes_expired": _count(
                    Quote, Quote.status == "SENT", Quote.valid_until.is_not(None), Quote.valid_until < jordan_today()
                ),
                "nonces_purged": _count(SecurityNonceRecord, SecurityNonceRecord.expires_at < now),
                "idempotency_records_purged": _count(ExternalIdempotencyRecord, ExternalIdempotencyRecord.expires_at < now),
                "rate_limit_counters_purged": _count(RateLimitCounter, RateLimitCounter.window_start < counter_cutoff),
                "email_bodies_cleared": privacy_retention.count_email_bodies_to_clear(),
                "login_attempts_purged": privacy_retention.count_login_attempts_to_purge(),
                "customer_login_attempts_purged": privacy_retention.count_customer_login_attempts_to_purge(),
                "customer_sessions_and_tokens_purged": privacy_retention.count_expired_customer_sessions_and_tokens(),
                "customer_link_mail_bodies_cleared": privacy_retention.count_customer_link_mail_bodies_to_clear(),
                "sync_bootstrap": bootstrap_routes.sweep(apply=False),
            }
        click.echo(json.dumps(result, indent=2))

    @app.cli.group("leads")
    def leads_group():
        """Lead data upkeep."""

    @leads_group.command("purge-website")
    @click.option(
        "--older-than-days", "older_than_days", type=int, default=None,
        help="Retention in days (default 730 = 24 months, the privacy-page promise).",
    )
    @click.option("--apply", "apply_", is_flag=True, default=False, help="Actually delete. Default is dry-run (counts only).")
    def purge_website_leads_cmd(older_than_days: int | None, apply_: bool):
        """The website privacy page promises demo requests are deleted after 24
        months, including the copy the website intake puts in the CRM. This
        deletes unconverted WEBSITE leads (and their notes/contacts/history)
        older than the cutoff; a lead that became a customer or a quote is kept.
        The nightly scheduled-ops job runs it with --apply. Dry-run by default."""
        from app.extensions import db_session
        from app.leads.website_retention import get_retention_months, months_to_days, purge_old_website_leads

        # An explicit --older-than-days always wins (manual runs, tests). Without
        # it the number of months the owner set in the Control Center is used, so
        # the nightly job follows that choice with no redeploy.
        if older_than_days is None:
            months = get_retention_months(db_session)
            days = months_to_days(months)
            source = "setting"
        else:
            months = None
            days = older_than_days
            source = "flag"
        try:
            result = purge_old_website_leads(older_than_days=days, apply=apply_)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        result["retention_source"] = source
        result["retention_months"] = months
        click.echo(json.dumps(result, indent=2))

    @app.cli.group("sync")
    def sync_group():
        """Phase 5 prerequisite jobs for the multi-device sync relay
        (docs/launch-readiness/phase5-prerequisites.md). Meant to be invoked
        by a systemd timer (see deploy/systemd/), same convention as the
        `reports generate-scheduled` group below -- never in the request
        path (app/sync/routes.py's push/pull handlers never call any of
        this)."""

    @sync_group.command("prune-events")
    @click.option("--apply", "apply_", is_flag=True, default=False, help="Actually delete pruned events. Default is dry-run (report only).")
    @click.option("--batch-size", default=None, type=int, help="Override the delete batch size (mainly useful for tests/tuning).")
    def prune_events_cmd(apply_: bool, batch_size: int | None):
        """Prunes owner_sync_events below each license's slowest-active-
        device watermark (Phase 5 prerequisite #2), then checks and logs
        the row-count and quarantine-rate alarms -- run on every invocation
        regardless of --apply, since a stuck/failing prune is exactly the
        condition the row-count alarm exists to catch.

        Refuses to run unless OWNER_SCHEDULER_ROLE is explicitly "owner",
        exactly like `reports generate-scheduled` below -- app/scheduling.py's
        own docstring makes this the contract for ANY command "meant to be
        triggered by an external scheduler rather than a human", and this
        group's docstring above already claims that convention. It matters
        more here than for any other scheduled job in this file, not less:
        every other one is idempotent-by-construction (advisory lock +
        unique constraint + dedup keys), so a second accidentally-enabled
        host merely wastes a query pass. This one issues real DELETEs
        against the sync ledger. A second host firing concurrently races
        the watermark computation against the first host's in-flight
        batch commits -- and unlike a duplicated report snapshot, a row
        deleted from owner_sync_events has no salvage: the device that
        still needed it resyncs into a hole with no error, which is the
        exact failure mode pruning.py's module docstring says there is no
        recovery from. Gating the whole command (not just the --apply
        path) matches the reports precedent, and costs nothing: the
        alarms below are advisory logging, not a signal anyone consumes
        from a non-scheduler process."""
        from app.scheduling import SchedulerNotOwnerError, require_scheduler_owner
        from app.sync.pruning import check_quarantine_rate_alarm, check_row_count_alarms, prune_owner_sync_events

        try:
            require_scheduler_owner(app.config)
        except SchedulerNotOwnerError as exc:
            raise click.ClickException(str(exc)) from exc

        kwargs = {"dry_run": not apply_}
        if batch_size:
            kwargs["batch_size"] = batch_size
        report = prune_owner_sync_events(**kwargs)
        click.echo(
            json.dumps(
                {
                    "dry_run": report.dry_run,
                    "per_license_deleted": report.per_license_deleted,
                    "total_deleted": report.total_deleted,
                },
                indent=2,
            )
        )

        for alarm in check_row_count_alarms():
            app.logger.warning(
                "owner_sync_events row-count alarm: license=%s row_count=%s threshold=%s",
                alarm.license_id, alarm.row_count, alarm.threshold,
            )
        for alarm in check_quarantine_rate_alarm():
            app.logger.warning(
                "sync quarantine pending-rate alarm: license=%s pending_count=%s threshold=%s",
                alarm.license_id, alarm.pending_count, alarm.threshold,
            )

    @sync_group.command("mark-pruned")
    @click.option("--license", "license_id", required=True, help="Licence UUID.")
    @click.option("--through", default=None, type=int, help="Highest seq known to be gone. Default: lowest stored seq for the licence - 1.")
    @click.option("--dry-run", is_flag=True, default=False, help="Print what would be recorded; write nothing.")
    def mark_pruned_cmd(license_id: str, through: int | None, dry_run: bool):
        """Records, for ONE licence, that its history is pruned through a seq.
        Use after deploying SYNC-BOOT Phase 1 on a relay that already pruned
        for real: the migration claims nothing, so a new device on such a
        licence would otherwise receive a partial stream. Always --dry-run
        first and read the number. Audited."""
        import uuid as _uuid
        from app.sync.pruning import mark_pruned

        try:
            lic = _uuid.UUID(license_id)
            result = mark_pruned(lic, through, dry_run=dry_run)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        if not dry_run:
            audit_record(
                actor_staff_user_id=None,
                actor_role_snapshot=None,
                action_code="SYNC_LICENSE_MARKED_PRUNED",
                entity_type="license",
                entity_public_id=str(lic),
                reason=f"pruned_through_seq raised to at least {result['through']} by operator CLI",
                after_state={"pruned_through_seq": result["through"]},
            )
        click.echo(json.dumps(result, indent=2))

    @app.cli.group("reports")
    def reports_group():
        """Phase 9R M5 -- scheduled report-snapshot generation. Meant to be
        invoked by a systemd timer (see deploy/systemd/), never by more than
        one designated process."""

    @reports_group.command("generate-scheduled")
    @click.option(
        "--period-type", type=click.Choice(["daily", "weekly", "monthly"]), required=True,
        help="Which completed period (relative to --as-of) to generate snapshots for.",
    )
    @click.option("--currency", default=None, help="Required for the operational-summary report types.")
    @click.option("--as-of", default=None, help="ISO date to compute the completed period against (default: today).")
    def generate_scheduled_reports_cmd(period_type: str, currency: str | None, as_of: str | None):
        """Generates the report snapshot(s) for the most recently completed
        daily/weekly/monthly period. Refuses to run unless OWNER_SCHEDULER_ROLE
        is explicitly "owner" (app/config.py, Phase 9R M2) -- the same
        systemd unit file must never be allowed to fire from two hosts (or
        from every Gunicorn worker) and silently double-generate. The
        underlying generate_snapshot() call is additionally idempotent
        (Postgres advisory lock + unique constraint,
        app/operational_reports/scheduler.py) as defense in depth, not as a
        substitute for this check -- a second, accidentally-enabled
        scheduler host should never even attempt the call, not merely fail
        to duplicate data if it does."""
        from app.operational_reports import scheduler as report_scheduler
        from app.scheduling import SchedulerNotOwnerError, require_scheduler_owner

        try:
            require_scheduler_owner(app.config)
        except SchedulerNotOwnerError as exc:
            raise click.ClickException(str(exc)) from exc

        as_of_date = date.fromisoformat(as_of) if as_of else jordan_today()
        generated = []

        if period_type == "daily":
            period_start = period_end = as_of_date - timedelta(days=1)
            if not currency:
                raise click.ClickException("--currency is required for period-type=daily (DAILY_OPERATIONAL_SUMMARY)")
            generated.append(
                report_scheduler.generate_snapshot(
                    report_type="DAILY_OPERATIONAL_SUMMARY", period_start=period_start, period_end=period_end,
                    currency=currency, generated_by="SCHEDULER",
                )
            )
            generated.append(
                report_scheduler.generate_snapshot(
                    report_type="DAILY_CASH_CLOSING_EXCEPTIONS", period_start=period_start, period_end=period_end,
                    currency=None, generated_by="SCHEDULER",
                )
            )
        elif period_type == "weekly":
            # Most recently completed Monday-Sunday week strictly before as_of.
            last_sunday = as_of_date - timedelta(days=as_of_date.isoweekday())
            period_start = last_sunday - timedelta(days=6)
            period_end = last_sunday
            if not currency:
                raise click.ClickException("--currency is required for period-type=weekly")
            generated.append(
                report_scheduler.generate_snapshot(
                    report_type="WEEKLY_OPERATIONAL_SUMMARY", period_start=period_start, period_end=period_end,
                    currency=currency, generated_by="SCHEDULER",
                )
            )
        else:  # monthly
            first_of_this_month = as_of_date.replace(day=1)
            period_end = first_of_this_month - timedelta(days=1)
            period_start = period_end.replace(day=1)
            if not currency:
                raise click.ClickException("--currency is required for period-type=monthly")
            generated.append(
                report_scheduler.generate_snapshot(
                    report_type="MONTHLY_OPERATIONAL_SUMMARY", period_start=period_start, period_end=period_end,
                    currency=currency, generated_by="SCHEDULER",
                )
            )

        click.echo(
            json.dumps(
                [
                    {
                        "report_type": s.report_type, "period_start": s.period_start.isoformat(),
                        "period_end": s.period_end.isoformat(), "snapshot_version": s.snapshot_version,
                    }
                    for s in generated
                ],
                indent=2,
            )
        )
