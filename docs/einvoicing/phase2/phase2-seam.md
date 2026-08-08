# Phase 2 seam — what's left, and what must NOT be guessed

Phase 1 shipped a complete, tested pipeline against `MockProvider`. Phase 2
wires the real ISTD connection once the business actually has portal
credentials and the official integration documentation. This is
everything Phase 2 needs to do, and nothing more.

## Prerequisite (blocks starting Phase 2 at all)

Real ISTD sandbox credentials **and** the official ISTD integration
package (field-level schema, code lists, the JSON encryption envelope,
endpoint paths), obtained by registering the business on the JoFotara
portal. None of this is publicly documented — it is issued to a taxpayer
directly. Nothing in `providers/direct_istd.py` may guess at it; see that
file's own docstring and `phase1-threat-model.md`'s corresponding threat
entry.

## The five things to actually build

1. **Fill in `providers/direct_istd.py`** — `submit_invoice()` and
   `check_status()`, against the real endpoints, real auth flow
   (`client_id`/`client_secret` → token), and the real response shape
   mapped onto `SubmissionResult`'s existing `Outcome` values. No other
   file in `commercial_runtime/einvoicing/` changes for this step — that's
   the point of the provider seam (`einvoice-provider-contract.md`).
2. **Add an ISTD-specific UBL profile branch to `ubl.py`** — real required
   element subset, cardinalities, code lists — behind
   `PROFILE_ID = "ISTD_JO_1_0"`. `GENERIC_UBL_2_1` (the Phase 1 default)
   stays as the tested fallback/default; do not remove it.
3. **Populate `istd-field-mapping.md`** (this directory) from the real
   integration docs — the actual field-by-field mapping this repo does not
   yet have.
4. **Flip the setting** — `provider='direct_istd'` and `istd_base_url` set
   per install via the existing `POST /api/einvoicing/settings` route. No
   code change, no migration, no redeploy of the shared module required
   for this step alone.
5. **Optional, not blocking:** `AndroidKeystoreSecretBox` (mirroring
   `DeviceIdentity.kt`'s hardware-backed key wrapping) plus a Kotlin
   credential-entry screen, to remove the Phase 1 Android-UI gap recorded
   in `../phase1/phase1-residual-risk-register.md`. Optional:
   `clinic_invoice_number_mode = sequential` as its own separately-gated
   migration — see `../phase1/invoice-numbering-audit.md`'s follow-up
   section; this is a real pre-existing Clinic numbering issue, unrelated
   to e-invoicing correctness (the ISTD-facing `einvoice_no` is already
   gapless regardless).

## Why the seam holds

`MockProvider` and `DirectISTDProvider` satisfy the same ABC. `outbox.py`,
`worker.py`, `routes.py`, and the UI all consume only `SubmissionResult` —
none of them know or care which concrete provider produced it. The
provider is selected by a string in a settings row. Swapping providers is
a configuration change, not a code change, for every layer except
`providers/direct_istd.py` and `ubl.py`'s new profile branch.
