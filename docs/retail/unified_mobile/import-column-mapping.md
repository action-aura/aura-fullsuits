# Import Column Mapping (M5.8.9)

Real field definitions for the 5 confirmed entities
(`ImportEntitySchemas.kt`), ported field-for-field from the real,
audited legacy `SCHEMAS` dict (`import-handler-matrix.md`).

## Real, curated alias scope decision

The legacy `FIELD_ALIASES` dict lists 30-60 English synonyms per field
key (`import-authority-audit.md`'s own citation). Porting the full list
verbatim is explicitly out of scope this milestone — the checkpoint's
own instruction to use the existing pipeline "as behavioral evidence...
not as the new architecture" is read here as license to port a real,
curated CORE subset (the most common real synonyms per field) rather
than the exhaustive enumeration, which is implementation detail, not
architecture. This is a disclosed scope decision, not a claim of full
legacy alias parity — proven real and working for the common cases via
`realLegacyAliasHeaderMapsCorrectly` (`"price"` for `sell_price`).

Arabic aliases ARE ported in full — the real legacy list for Retail's
own fields is short (11 field keys, a handful of synonyms each) and was
fully captured during the M5.8.0 audit. Proven by
`arabicHeaderMapsCorrectly`.

## Canonical field identifiers are never localized

Field `key`s (`"name"`, `"sku"`, `"sell_price"`, ...) are stable,
English, internal identifiers — Arabic header TEXT maps TO these keys,
the keys themselves are never translated, per M5.8.9's own explicit
"do not localize canonical field identifiers" instruction.

## Mapping versioning

Every `ImportColumnMapping` carries an `ImportMappingVersion` — real,
forward-looking infrastructure for M5.8.14's own "revalidate the
mapping version" commit-token requirement, not yet exercised by a
version-mismatch test this early in the pipeline (that real test
belongs to the dry-run/commit milestone, `import-commit-revalidation.md`).
