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

        as_of_date = date.fromisoformat(as_of) if as_of else date.today()
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
