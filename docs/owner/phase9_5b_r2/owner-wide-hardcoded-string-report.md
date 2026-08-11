# Phase 9.5B-R2 — Owner-Wide Hardcoded-String Report

## Scanner expansion

`GATED_DIRS` in `test_phase9_5b_r_hardcoded_strings.py` expanded from 4
directories (`layout`, `auth`, `employees`, `profile`) to all 15 real
template directories (adds `dashboard`, `audit`, `catalog`, `customers`,
`installations`, `licensing`, `licensing_admin`, `staff`, `subscriptions`,
`system`, `commercial_ops`).

## Real result

**Zero violations** across all 67 templates after translation — confirmed
by `test_no_hardcoded_english_text_node_outside_jinja_in_gated_templates`
passing against the fully expanded scope, and by
`test_all_real_template_directories_are_now_gated` confirming the scanner
covers every directory that actually contains a template (excluding the two
confirmed-empty scaffold directories `licensing_service/` and `settings/`).

## Real false positives found and resolved during expansion

The first run against the expanded scope found 15 "violations" — all real
false positives, not missed translations: HTML entities (`&middot;`,
`&rarr;`, decorative punctuation) and two literal CLI command names
(`flask import-release-manifest`, `flask seed-offline-policy`) intentionally
left untranslated inside `<code>` blocks. Added to the scanner's reviewed
allowlist (now 7 items, still well under the `<=10` regression-guarded cap).
No real missed-translation was found or silently allowlisted — the
allowlist growth is fully justified in `hardcoded-string-allowlist-review.md`.
