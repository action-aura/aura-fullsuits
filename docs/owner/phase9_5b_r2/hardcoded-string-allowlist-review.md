# Phase 9.5B-R2 — Hardcoded-String Allowlist Review

Full allowlist (`ALLOWLISTED_TEXT` in `test_phase9_5b_r_hardcoded_strings.py`),
7 items, each individually justified:

| Entry | Justification |
|---|---|
| `Aura Owner` | Brand name — never translated in any locale (Non-Negotiable Principle, retained from Phase 9.5B-R) |
| `-` | Punctuation-only placeholder for an empty value |
| `&nbsp;` | Non-breaking space, not prose (Phase 9.5B-R) |
| `&middot;` | Decorative separator character between two already-translated items, not prose (new this wave) |
| `&rarr;` | Decorative arrow between two already-translated date/status values (e.g. "start → end"), not a navigational icon, not prose (new this wave) |
| `flask import-release-manifest` | Literal CLI command name shown to an operator, not prose (new this wave) |
| `flask seed-offline-policy` | Literal CLI command name shown to an operator, not prose (new this wave) |

## Review discipline applied

Every candidate addition was checked against the rule "never silence a
visible English sentence by adding it to the allowlist" — all 7 entries are
either single decorative characters/entities or exact, unmodifiable CLI
command names, never a real English sentence or phrase. The allowlist
stayed within the regression-guarded `<=10` cap
(`test_scanner_allowlist_is_reviewed_and_bounded`) without needing to
stretch that limit.
