# ISTD field mapping — TODO, Phase 2

Not populated. The real ISTD field-level schema (element names,
cardinalities, code lists, the JSON encryption envelope, endpoint paths)
is not publicly available — it is issued to a taxpayer only after
registering on the JoFotara portal. See `phase2-seam.md`.

Fill this in from the official ISTD integration package once obtained.
Until then, `providers/direct_istd.py` raises `NotImplementedError` rather
than guessing, and this file stays empty rather than containing fabricated
field names.

## Update 2026-10-13

A document profile built from PUBLIC second-hand sources (not the official
package) now exists: see `../istd-profile.md`. It does not replace the
official integration package; it is what to check against it. The portal-issued
details (endpoint, auth headers, response codes) are still unconfirmed here.
