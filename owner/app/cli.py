"""Flask CLI commands: Super Admin bootstrap, RBAC seeding, release-manifest
import, Phase 8 commercial-operations jobs."""
from __future__ import annotations

import getpass
import json
from datetime import date

import click
from flask import Flask
from sqlalchemy import select

from app.audit.services import record as audit_record
from app.catalog.services import import_release_manifest, seed_canonical_catalog
from app.extensions import db_session
from app.licensing_service import signing as signing_service
from app.licensing_service.offline_policy import seed_default_offline_policy
from app.models.staff import Permission, Role, RolePermission, StaffUser
from app.security.passwords import PasswordPolicyError, hash_password
from app.staff.seed_data import PERMISSIONS, ROLES


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-rbac")
    def seed_rbac():
        """Seed the canonical permission and role catalog (idempotent)."""
        existing_perms = {p.code: p for p in db_session.execute(select(Permission)).scalars().all()}
        for code, category, description in PERMISSIONS:
            if code not in existing_perms:
                existing_perms[code] = Permission(code=code, category=category, description=description)
                db_session.add(existing_perms[code])
        db_session.flush()

        existing_roles = {r.code: r for r in db_session.execute(select(Role)).scalars().all()}
        for code, definition in ROLES.items():
            role = existing_roles.get(code)
            if role is None:
                role = Role(code=code, name=definition["name"], description=definition["description"], is_system_role=True)
                db_session.add(role)
                db_session.flush()
                existing_roles[code] = role

            wanted_codes = (
                set(existing_perms.keys()) if definition["permissions"] == "*" else set(definition["permissions"])
            )
            have_codes = {
                rp.permission.code
                for rp in db_session.execute(select(RolePermission).where(RolePermission.role_id == role.id)).scalars()
            }
            for missing_code in wanted_codes - have_codes:
                db_session.add(RolePermission(role_id=role.id, permission_id=existing_perms[missing_code].id))
        db_session.commit()
        click.echo(f"Seeded {len(existing_perms)} permissions and {len(existing_roles)} roles.")

    @app.cli.command("seed-catalog")
    def seed_catalog():
        """Seed canonical products/platforms/release channels/entitlement
        definitions/DRAFT add-ons (idempotent, no fake customer data)."""
        result = seed_canonical_catalog()
        click.echo(json.dumps(result))

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
