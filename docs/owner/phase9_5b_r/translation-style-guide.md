# Phase 9.5B-R — Translation Style Guide

## English (source locale)

Clear, concise, professional, non-technical where user-facing. Sentence case for labels ("Employment
start date", not "Employment Start Date") except where the existing pre-9.5B-R convention already used
title case in a heading (`<h2>`/`<h3>`) — kept consistent with those, not retroactively changed
(Non-Negotiable Principle 3: this phase changes presentation-language, not the existing English wording's
established casing conventions where unrelated to translation).

## Arabic

Modern Standard Arabic (الفصحى), formal register appropriate for enterprise business software — never
dialectal/colloquial Arabic, never overly literal word-for-word translation when it would read awkwardly.
Consistent terminology throughout (`english-arabic-glossary.md` is the single source of truth — a term is
never translated two different ways in two different screens).

## Real rule found and applied during this phase: never split a sentence across template fragments

`auth/mfa_recovery_codes.html`'s original English had a sentence broken across three separate
`{{ _(...) }}` calls around an inline `<strong>` tag: `_('...will')` + `<strong>` + `_('never be shown
again')` + `</strong>` + `_('after you leave this page.')`. This is a genuine translation-quality bug, not
just cosmetic — Arabic sentence structure does not preserve English word order, so concatenating three
independently-translated fragments produces grammatically broken Arabic (or, at best, unnaturally stilted
phrasing) even when each fragment is individually translated correctly. **Fixed** by combining into one
full, single translatable sentence (`current-owner-string-inventory.md`/`current-owner-string-inventory.md`
records this as a real fix, not merely a style preference) — the rule this style guide commits to: a
`{{ _(...) }}` boundary must never split what is grammatically one sentence, even at the cost of dropping
inline emphasis markup.

## Placeholders

Named, never positional (`%(email)s`, never `%s`) — required for correct Arabic word order, since Arabic
sentence structure frequently reorders where a placeholder falls relative to English. Every placeholder
name in `owner/translations/*/LC_MESSAGES/messages.po` is descriptive (`min_length`, `email`, `page`,
`total_pages`, `total`, `ts`), never `%(1)s`-style.

## Numerals

Western (ASCII) digits throughout, both locales — see `locale-formatting-contract.md`'s Arabic-digit
policy decision.

## What is never translated (glossary-adjacent, not glossary itself)

Product name "Aura Owner" stays Latin-script in both locales (a proper noun/brand, per Non-Negotiable
Principle 4's "keep stable internal values" spirit extended to branding — matches how "Aura Owner" appears
untranslated in the Arabic catalog's own `msgstr` for that exact msgid).
