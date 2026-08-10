# Phase 9R — Secret and Key Management (M3)

## Inventory

| Secret | Current mechanism | Rotation support | Environment isolation |
|---|---|---|---|
| Flask/session secret (`OWNER_SECRET_KEY`) | Env var, validated by `BaseConfig.validate()` (M2: presence, non-default, ≥32 chars) | Manual — generate new value, redeploy, invalidates all active sessions (Flask signs cookies with it; no overlap window exists for this one, and none is needed — a forced re-login is an acceptable rotation cost, unlike signing-key rotation which must not invalidate offline leases) | Yes — required distinct per environment (M2 dev/test/staging default check) |
| MFA encryption key | Derived from `OWNER_SECRET_KEY` (`app/security/mfa.py`), not a separate secret | Same as `OWNER_SECRET_KEY` — rotating it re-derives the MFA key, which invalidates in-flight TOTP setup flows only (not stored MFA state, which lives encrypted server-side and is re-encrypted, not orphaned — see the existing MFA module) | Same as `OWNER_SECRET_KEY` |
| Database credentials (`OWNER_DATABASE_URL`) | Env var | Manual — coordinated with PostgreSQL role password rotation (M4); requires a brief restart | Yes — required distinct per environment (M2 dev/test-database-name check) |
| License-signing keys | **Full lifecycle system**, `owner/app/licensing_service/signing.py`: DRAFT→ACTIVE→RETIRED, independent REVOKED state, overlap window, compromised-key response | **Already implemented and tested** — see `signed-license-lease-contract.md` (M9) for the full audit | Per-environment signing-key directory (`OWNER_SIGNING_KEY_DIRECTORY`, distinct staging/production/test paths already in `config.py`) |
| Release-signing references | Reuses the same signing-key infrastructure (M10 release manifests are signed the same way as license assertions — same `signing.py`, same key) | Same as license-signing keys | Same |
| Backup credentials | **New for Phase 9R** — object-storage credential scoped to the backup destination only (M12) | Manual, coordinated with the storage provider's own credential rotation | Distinct per environment; never the same credential as the app's own release-artifact storage credential where the provider supports separate scoping |
| Object-storage credentials (release artifacts) | **New for Phase 9R** (M11) | Manual | Distinct per environment |
| Deployment credentials | **New for Phase 9R** (M16) — scoped SSH key or platform API token for CI/CD, never a human's interactive credential | Manual, on suspected compromise or personnel change | One per environment; never shared between staging and production |
| Monitoring webhook credentials | **New for Phase 9R** (M14) | Manual | Distinct per environment |
| SMTP credentials | Not currently used — no feature in the Phase 9.5E baseline sends outbound email (confirmed in `infrastructure-availability-audit.md`) | N/A until a feature needs it | N/A |

## Where secrets are never allowed to appear

Enforced today, verified by the M0 secret scan (`dependency-fix-evidence.md`
and the original `detect-secrets` baseline from every prior phase) and
carried forward unchanged as a Phase 9R requirement:

- Git history (enforced by `detect-secrets` pre-commit baseline)
- Docker image layers (N/A yet — no container build exists; the requirement
  is recorded here so it applies the moment M16 introduces one)
- Docker Compose source files (same)
- Documentation (this document and every other Phase 9R doc contain zero
  real secret values — every example is a placeholder)
- Shell history (operational discipline — recorded in `operational-runbooks.md`, M24)
- Frontend JavaScript / templates (structurally impossible for the secrets
  in this inventory — none of them are ever passed to a template context)
- Database rows, unless a specific field is deliberately designed encrypted
  (MFA secrets already are, via the `OWNER_SECRET_KEY`-derived key; nothing
  else in this inventory is stored in the database at all — signing private
  keys live on disk in `OWNER_SIGNING_KEY_DIRECTORY`, never in a table)
- Normal application logs — enforced by the redaction requirements in
  `observability-and-alerting.md` (M14)

## Storage mechanism for staging/production

No secrets-manager integration exists yet (would need a specific provider
choice, deferred to `external-dependency-register.md` #9/#11). Until one is
selected, the documented interim mechanism is host-level: an
environment file readable only by the service's own OS user (M4/M5's
dedicated `aura-owner-web` identity), outside any web-served or
git-tracked directory, loaded by `systemd`'s `EnvironmentFile=` directive
(never passed on a process command line, which would be visible via `ps`).
This is a real, adequate mechanism for a single-operator pilot — a
dedicated secrets manager (Vault, cloud-provider secret store, etc.) is a
reasonable later upgrade once team size or compliance requirements justify
its operational overhead, not a Phase 9R blocker.

## Signing-key rotation does not invalidate legitimate offline leases

Directly verified in `signed-license-lease-contract.md`: `verify_assertion()`
looks up the signing key by the `signing_key_id` embedded in each
assertion's envelope, and accepts any non-`REVOKED` key regardless of
whether it's currently `ACTIVE` or has been `RETIRED` by a later rotation.
An offline client holding a lease signed by a since-retired key continues
to verify successfully until that lease's own `expires_at` — rotation only
changes which key *new* leases are signed with, never invalidates leases
already issued. This is the M3 requirement satisfied by existing code, with
real passing tests cited in `signed-license-lease-contract.md`.

## Compromised-key procedure

`revoke_signing_key()` moves a key straight to `REVOKED`, and
`verify_assertion()` rejects a `REVOKED` key's signature unconditionally
(before even attempting cryptographic verification — `assertions.py:122`).
The operational side of this (who decides, how fast, what gets
communicated to affected customers) is a runbook, not code — see
"suspected key compromise" in `operational-runbooks.md` (M24).

## Backup and recovery of signing-key material

Deliberately **not** part of the general automatic backup sweep — see the
"Signing-key recovery — the one deliberate exception" section of
`backup-policy.md` (M12). Signing keys are high-value enough to warrant a
narrower-access, manually-triggered backup path distinct from the general
daily database/attachment backup.
