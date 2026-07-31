"""Phase 8V-P2, Part C: deterministic environment preflight.

Two real, disclosed environment gaps blocked all physical/real validation in
the Phase 8V-P session before anyone noticed: a stale `trust_anchor.json`
referencing a signing key that no longer existed, and an under-seeded
`owner_permissions` table missing ten Phase 8 permission codes (see
docs/owner/phase8vp/environment-readiness-report.md). Neither was caught by
any existing automated check -- both were found by hand, mid-session, only
because a real activation/route call failed. This module exists so the next
person (or the next session) gets a single, fast, `flask commercial
preflight` command that would have caught both on day one, instead of
re-discovering them by hand again.

Every check here is read-only and side-effect-free. Nothing in this module
ever writes a signing key, permission, or role -- it only reports what is
inconsistent so a human (or `seed-rbac`/`licensing activate-signing-key`) can
fix it deliberately.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from app.extensions import db_session
from app.licensing_service import signing as signing_service
from app.models.staff import Permission, Role, RolePermission, StaffUser
from app.staff.seed_data import PERMISSIONS, ROLES

# The trust anchor is a gitignored, build-time-only artifact produced by
# scripts/generate_trust_anchor.py (see that script and
# docs/owner/phase8vp/environment-readiness-report.md). It does not exist on
# a fresh clone or CI checkout -- its absence is reported as a WARNING, not a
# blocking FAIL, since a from-scratch environment legitimately has no
# products built yet to embed it in.
_DEFAULT_TRUST_ANCHOR_RELATIVE_PATH = (
    Path(__file__).resolve().parents[3] / "commercial_runtime" / "licensing_contracts" / "trust_anchor.json"
)


@dataclass
class PreflightCheck:
    name: str
    status: str  # "OK" | "WARNING" | "FAIL"
    detail: str


@dataclass
class PreflightResult:
    ok: bool
    checks: list[PreflightCheck] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in self.checks],
        }


def _trust_anchor_path() -> Path:
    override = os.environ.get("COMMERCIAL_TRUST_ANCHOR_PATH")
    return Path(override) if override else _DEFAULT_TRUST_ANCHOR_RELATIVE_PATH


def _check_active_signing_key(checks: list[PreflightCheck]) -> bool:
    key = signing_service.get_active_signing_key()
    if key is None:
        checks.append(PreflightCheck(
            "active_signing_key_exists", "FAIL",
            "No ACTIVE signing key in owner_signing_keys. Every real activation/check-in will fail with "
            "SIGNING_KEY_UNAVAILABLE. Fix: 'flask licensing generate-signing-key' then "
            "'flask licensing activate-signing-key <key_id>'.",
        ))
        return False
    checks.append(PreflightCheck("active_signing_key_exists", "OK", f"Active key: {key.key_id}"))
    return True


def _check_signing_key_health(checks: list[PreflightCheck], key_directory: str) -> bool:
    result = signing_service.verify_signing_key_health(key_directory)
    if result["status"] != "OK":
        checks.append(PreflightCheck("signing_key_sign_verify_roundtrip", "FAIL", result.get("detail", "unknown failure")))
        return False
    checks.append(PreflightCheck("signing_key_sign_verify_roundtrip", "OK", "Real sign/verify round-trip succeeded."))
    return True


def _check_trust_anchor(checks: list[PreflightCheck]) -> bool:
    key = signing_service.get_active_signing_key()
    if key is None:
        checks.append(PreflightCheck("trust_anchor_matches_active_key", "FAIL", "Cannot check -- no active signing key."))
        return False

    anchor_path = _trust_anchor_path()
    if not anchor_path.exists():
        checks.append(PreflightCheck(
            "trust_anchor_matches_active_key", "WARNING",
            f"No trust_anchor.json found at {anchor_path} (gitignored, build-time-only artifact -- "
            "expected on a fresh checkout with no products built yet). Regenerate before building any "
            "product installer: 'python scripts/generate_trust_anchor.py --owner-url <url> --out "
            "commercial_runtime/licensing_contracts/trust_anchor.json'.",
        ))
        return True  # not a blocking condition on its own

    try:
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(PreflightCheck("trust_anchor_matches_active_key", "FAIL", f"trust_anchor.json unreadable: {exc}"))
        return False

    anchor_key_ids = {k.get("key_id") for k in anchor.get("keys", [])}
    if key.key_id not in anchor_key_ids:
        checks.append(PreflightCheck(
            "trust_anchor_matches_active_key", "FAIL",
            f"trust_anchor.json does not contain the active signing key ({key.key_id}); it references "
            f"{sorted(anchor_key_ids) or '(none)'} instead. Every real product build using this stale file "
            "will reject real activations with UNKNOWN_SIGNING_KEY. Regenerate it against this running "
            "Owner: 'python scripts/generate_trust_anchor.py --owner-url <url> --out "
            "commercial_runtime/licensing_contracts/trust_anchor.json'. This is exactly the gap that "
            "blocked the Phase 8V-P session until found by hand.",
        ))
        return False

    checks.append(PreflightCheck("trust_anchor_matches_active_key", "OK", f"trust_anchor.json recognizes {key.key_id}."))
    return True


def _check_permission_seed(checks: list[PreflightCheck]) -> bool:
    db_codes = {p.code for p in db_session.execute(select(Permission)).scalars().all()}
    seed_codes = {code for code, _category, _description in PERMISSIONS}
    missing = seed_codes - db_codes
    ok = True
    if missing:
        ok = False
        checks.append(PreflightCheck(
            "all_permissions_seeded", "FAIL",
            f"{len(missing)} permission code(s) defined in code but missing from the database: "
            f"{sorted(missing)}. Any route guarded by one of these 403s for every staff user, including "
            "a genuine is_super_admin account. Fix: 'flask seed-rbac'.",
        ))
    else:
        checks.append(PreflightCheck("all_permissions_seeded", "OK", f"All {len(seed_codes)} permission codes present."))

    codes_seen = [code for code, _c, _d in PERMISSIONS]
    duplicates = {c for c in codes_seen if codes_seen.count(c) > 1}
    if duplicates:
        ok = False
        checks.append(PreflightCheck("no_duplicate_permission_codes", "FAIL", f"Duplicate codes in PERMISSIONS: {sorted(duplicates)}"))
    else:
        checks.append(PreflightCheck("no_duplicate_permission_codes", "OK", "No duplicate permission codes."))
    return ok


def _check_role_permission_assignments(checks: list[PreflightCheck]) -> bool:
    all_perm_codes = {p.code for p in db_session.execute(select(Permission)).scalars().all()}
    ok = True
    for role_code, definition in ROLES.items():
        role = db_session.execute(select(Role).where(Role.code == role_code)).scalars().first()
        if role is None:
            ok = False
            checks.append(PreflightCheck(f"role_seeded:{role_code}", "FAIL", f"Role {role_code} is defined in code but missing from the database."))
            continue
        wanted = all_perm_codes if definition["permissions"] == "*" else set(definition["permissions"])
        have = {
            rp.permission.code
            for rp in db_session.execute(select(RolePermission).where(RolePermission.role_id == role.id)).scalars()
        }
        missing = wanted - have
        if missing:
            ok = False
            checks.append(PreflightCheck(
                f"role_permissions_synced:{role_code}", "FAIL",
                f"Role {role_code} is missing {len(missing)} permission(s) present in code: {sorted(missing)}. Fix: 'flask seed-rbac'.",
            ))
    if ok:
        checks.append(PreflightCheck("role_permissions_synced", "OK", f"All {len(ROLES)} roles have every permission code defined in code."))
    return ok


# Phase 8V-P9: real, plausible misspellings/aliases of the one canonical env
# var (OWNER_LICENSE_PEPPER) an operator (or a hastily-written validation
# script -- see docs/owner/phase8vp9/license-pepper-preflight-final.md for the
# real incident this closes) could set by mistake, silently producing a
# pepper Owner never actually uses. Checked as environment variables, not
# config attributes, since the whole point is to catch a value that never
# made it into app.config at all.
_KNOWN_PEPPER_ALIASES = (
    "LICENSE_PEPPER",
    "LICENSE_KEY_PEPPER",
    "OWNER_LICENSE_KEY_PEPPER",
    "OWNER_PEPPER",
    "PEPPER",
)


def _check_license_pepper(checks: list[PreflightCheck]) -> bool:
    """Real self-test, not just a presence check: hashes and verifies a
    synthetic constant using the exact pepper source
    (current_app.config["LICENSE_PEPPER"]) that both issuance
    (licensing/routes.py) and verification (licensing_service/activation.py,
    via the config dict built from this same attribute) actually read --
    confirmed by source to be the single real source for both, so a
    round-trip against it is a genuine, direct proof issuance and
    verification agree, not an assumption. Never logs or returns the pepper
    value itself, and never logs a real/synthetic license key."""
    from flask import current_app

    from app.security.license_keys import hash_license_secret, verify_license_key

    pepper = current_app.config.get("LICENSE_PEPPER", "")
    ok = True

    if not pepper:
        ok = False
        checks.append(PreflightCheck(
            "license_pepper_configured", "FAIL",
            "LICENSE_PEPPER (env: OWNER_LICENSE_PEPPER) is empty or unset. Every license-key issuance "
            "and every activation's HMAC verification will fail. Fix: set OWNER_LICENSE_PEPPER to a "
            "real secret value before starting Owner.",
        ))
    elif "insecure" in pepper or pepper == "dev-only-insecure-pepper-do-not-use-in-production":
        checks.append(PreflightCheck(
            "license_pepper_configured", "WARNING",
            "LICENSE_PEPPER is set to the known development placeholder value. Fine for local/dev "
            "validation; must never be true in a real production environment (validate_external_api_"
            "production() already refuses production startup with this value -- this is a dev-mode "
            "reminder, not a new production gate).",
        ))
    else:
        checks.append(PreflightCheck("license_pepper_configured", "OK", "LICENSE_PEPPER is set (value not logged)."))

    if ok and pepper:
        try:
            synthetic_key = "AURA-PREFLIGHT-SELFTEST-0000-0000-0000-0000-0000"
            stored = hash_license_secret(synthetic_key, pepper)
            if not verify_license_key(synthetic_key, pepper, stored):
                ok = False
                checks.append(PreflightCheck(
                    "license_pepper_self_test_roundtrip", "FAIL",
                    "A real hash/verify round-trip using the configured pepper did not match itself -- "
                    "this should be structurally impossible and indicates a real bug in "
                    "hash_license_secret()/verify_license_key(), not a configuration problem.",
                ))
            else:
                checks.append(PreflightCheck(
                    "license_pepper_self_test_roundtrip", "OK",
                    "Real HMAC hash/verify round-trip against a synthetic key succeeded.",
                ))
        except ValueError as exc:
            ok = False
            checks.append(PreflightCheck("license_pepper_self_test_roundtrip", "FAIL", str(exc)))

    aliases_set = [name for name in _KNOWN_PEPPER_ALIASES if os.environ.get(name)]
    if aliases_set:
        checks.append(PreflightCheck(
            "license_pepper_no_stray_aliases", "WARNING",
            f"Environment variable(s) {sorted(aliases_set)} are set but are NOT the canonical "
            "OWNER_LICENSE_PEPPER -- Owner never reads them. If one of these was meant to configure "
            "the pepper, it has silently had no effect. This is exactly the real mistake found during "
            "Phase 8V-P7 physical validation (a validation script used the wrong config key name and "
            "silently fell back to an unintended value). Not blocking on its own -- the value above "
            "already confirmed LICENSE_PEPPER itself is set and self-consistent -- but always worth a "
            "human's attention.",
        ))
    else:
        checks.append(PreflightCheck("license_pepper_no_stray_aliases", "OK", "No known misspelled pepper env-var aliases are set."))

    return ok


def _check_super_admin_mfa(checks: list[PreflightCheck]) -> None:
    # Informational only -- a synthetic/dev Super Admin without MFA is
    # expected in some local test setups (see
    # docs/owner/phase8vp/PHASE8VP-PHYSICAL-COMMERCIAL-CLOSURE-HANDOVER.md's
    # own admin/approver pair), so this never fails preflight.
    rows = db_session.execute(select(StaffUser).where(StaffUser.is_super_admin.is_(True))).scalars().all()
    without_mfa = [r.email for r in rows if not r.mfa_required]
    if without_mfa:
        checks.append(PreflightCheck(
            "super_admin_mfa_required", "WARNING",
            f"{len(without_mfa)} Super Admin account(s) have mfa_required=False: {without_mfa}. Fine for "
            "local synthetic test accounts; must not be true for any real production Super Admin.",
        ))
    else:
        checks.append(PreflightCheck("super_admin_mfa_required", "OK", "Every Super Admin account requires MFA."))


def run_preflight(*, key_directory: str) -> PreflightResult:
    checks: list[PreflightCheck] = []
    blocking_ok = True

    blocking_ok &= _check_active_signing_key(checks)
    if blocking_ok:
        blocking_ok &= _check_signing_key_health(checks, key_directory)
        blocking_ok &= _check_trust_anchor(checks)
    blocking_ok &= _check_permission_seed(checks)
    blocking_ok &= _check_role_permission_assignments(checks)
    blocking_ok &= _check_license_pepper(checks)
    _check_super_admin_mfa(checks)  # informational only, never blocking

    return PreflightResult(ok=bool(blocking_ok), checks=checks)
