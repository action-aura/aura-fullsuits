"""License domain services (Part N/O). Full plaintext key exists only in the
return value of issue_license() -- never persisted, never logged, never re-derivable."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.licensing import License, LicenseKeyIssuanceEvent, LicenseStatusHistory
from app.security.license_keys import KEY_FORMAT_VERSION, generate_license_key, hash_license_secret

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"ISSUED"},
    "ISSUED": {"ACTIVE", "SUSPENDED", "REVOKED"},
    "ACTIVE": {"SUSPENDED", "EXPIRED", "REVOKED"},
    "SUSPENDED": {"ACTIVE", "REVOKED", "EXPIRED"},
    "EXPIRED": {"REPLACED"},
    "REVOKED": set(),
    "REPLACED": set(),
}


class InvalidLicenseTransitionError(ValueError):
    pass


def create_license(fields: dict, actor_staff_user_id) -> License:
    license_row = License(status="DRAFT", **fields)
    db_session.add(license_row)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LICENSE_CREATED",
        entity_type="license",
        entity_public_id=str(license_row.id),
        after_state={"status": license_row.status, "product_id": str(license_row.product_id)},
    )
    return license_row


def issue_license_key(
    license_row: License, pepper: str, idempotency_key: str, actor_staff_user_id
) -> tuple[License, str | None]:
    """Returns (license, full_key_or_None). full_key is None when this call is a
    replay of an already-processed idempotency_key (Part O: 'prevent duplicate
    issuance from repeated requests... use idempotency')."""
    existing_event = db_session.execute(
        select(LicenseKeyIssuanceEvent).where(LicenseKeyIssuanceEvent.idempotency_key == idempotency_key)
    ).scalars().first()
    if existing_event is not None:
        return license_row, None

    if license_row.status not in ("DRAFT",):
        raise InvalidLicenseTransitionError(f"Cannot issue a key for a license in status {license_row.status}.")

    full_key, prefix, masked_suffix = generate_license_key_for_product(license_row)
    license_row.key_prefix = prefix
    license_row.key_suffix_masked = masked_suffix
    license_row.key_secret_hmac = hash_license_secret(full_key, pepper)
    license_row.key_format_version = KEY_FORMAT_VERSION
    license_row.status = "ISSUED"
    license_row.issued_at = utcnow()
    license_row.issuing_staff_user_id = actor_staff_user_id

    db_session.add(
        LicenseStatusHistory(
            license_id=license_row.id, from_status="DRAFT", to_status="ISSUED", changed_by_staff_user_id=actor_staff_user_id
        )
    )
    db_session.add(
        LicenseKeyIssuanceEvent(
            license_id=license_row.id,
            issued_by_staff_user_id=actor_staff_user_id,
            idempotency_key=idempotency_key,
            key_prefix=prefix,
            key_format_version=KEY_FORMAT_VERSION,
        )
    )
    db_session.commit()

    # Audited WITHOUT the secret -- only the masked/prefix metadata (Part O: "log
    # issuance without recording the secret"). audit.services.redact() would also
    # strip any accidental "key_secret"/"full_key" field, but we never pass one.
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LICENSE_KEY_ISSUED",
        entity_type="license",
        entity_public_id=str(license_row.id),
        after_state={"key_prefix": prefix, "key_suffix_masked": masked_suffix, "status": "ISSUED"},
    )
    return license_row, full_key


def generate_license_key_for_product(license_row: License):
    product_code = license_row.product.product_code
    return generate_license_key(product_code)


def transition_license(license_row: License, to_status: str, actor_staff_user_id, reason: str | None = None) -> None:
    allowed = VALID_TRANSITIONS.get(license_row.status, set())
    if to_status not in allowed:
        raise InvalidLicenseTransitionError(f"Cannot transition license from {license_row.status} to {to_status}.")
    from_status = license_row.status
    license_row.status = to_status
    if to_status == "REVOKED":
        license_row.revocation_reason = reason
    db_session.add(
        LicenseStatusHistory(
            license_id=license_row.id, from_status=from_status, to_status=to_status,
            changed_by_staff_user_id=actor_staff_user_id, reason=reason,
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LICENSE_STATUS_CHANGED",
        entity_type="license",
        entity_public_id=str(license_row.id),
        before_state={"status": from_status},
        after_state={"status": to_status},
        reason=reason,
    )


def replace_license(old_license: License, actor_staff_user_id) -> License:
    if old_license.status not in ("ACTIVE", "ISSUED", "SUSPENDED", "EXPIRED"):
        raise InvalidLicenseTransitionError(f"Cannot replace a license in status {old_license.status}.")
    new_license = License(
        customer_id=old_license.customer_id,
        subscription_id=old_license.subscription_id,
        product_id=old_license.product_id,
        plan_id=old_license.plan_id,
        allowed_platforms=old_license.allowed_platforms,
        allowed_release_channel_id=old_license.allowed_release_channel_id,
        device_limit=old_license.device_limit,
        valid_from=old_license.valid_from,
        valid_until=old_license.valid_until,
        status="DRAFT",
    )
    db_session.add(new_license)
    db_session.flush()
    old_license.replaced_by_license_id = new_license.id
    target_status = "REPLACED" if old_license.status == "EXPIRED" else "REVOKED"
    transition_license(old_license, target_status, actor_staff_user_id, reason="Replaced by a new license.")
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LICENSE_REPLACED",
        entity_type="license",
        entity_public_id=str(old_license.id),
        after_state={"replaced_by_license_id": str(new_license.id)},
    )
    return new_license
