# ISTD field mapping — TODO, Phase 2

Not populated. The real ISTD field-level schema (element names,
cardinalities, code lists, the JSON encryption envelope, endpoint paths)
is not publicly available — it is issued to a taxpayer only after
registering on the JoFotara portal. See `phase2-seam.md`.

Fill this in from the official ISTD integration package once obtained.
Until then, `providers/direct_istd.py` raises `NotImplementedError` rather
than guessing, and this file stays empty rather than containing fabricated
field names.
