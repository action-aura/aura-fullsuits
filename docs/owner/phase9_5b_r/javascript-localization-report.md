# Phase 9.5B-R Milestone 14 — JavaScript Localization Report

## Real scope: 2 strings, not a general JS translation problem

Owner has zero `<script>` tags anywhere (`current-owner-string-inventory.md`'s real grep confirms this).
The only client-side JS-executed user-visible text in the gated set was 2 inline
`onsubmit="return confirm('...')"` strings in `employees/detail.html` (suspend/terminate confirmations).

## Real security fix made this phase

The original inline pattern interpolated a translated string directly into a single-quoted JavaScript
string literal inside an HTML attribute: `onsubmit="return confirm('{{ _('...') }}');"`. This is a real
risk the governing spec's own Milestone 14 principles warn about directly ("no translation key supplied as
arbitrary executable input... safe JSON escaping... no XSS") — if any current or future translation of
that string ever contained an apostrophe, it would break out of the JS string literal (at minimum a broken
confirm dialog; in the worst case, with a compromised/malicious translation source, script injection).
Caught and fixed before merge, not discovered later.

**Fix**: moved to `data-confirm="{{ _('...') }}"` (a plain HTML attribute — Jinja's normal autoescape
handles this safely, no JS-string-literal quoting involved at all) plus one small, external, CSP-compliant
script (`owner/app/static/js/confirm.js`) that reads `data-confirm` on submit and calls
`window.confirm()`. This is exactly the governing spec's own recommended bounded strategy ("translated
data attributes... centralized safe helper").

## CSP compatibility

Owner's CSP is `script-src 'self'` (no `'unsafe-inline'`) — `confirm.js` is served from `/static/js/
confirm.js`, same-origin, so it loads under the existing policy with zero CSP changes required. An inline
`<script>` block would have been blocked outright; this was verified by checking `owner/app/security/
headers.py`'s real, unchanged CSP string before choosing this approach.

## No secrets, no translation catalog in JS

`confirm.js` carries zero translated text of its own — every string it ever displays was already
localized server-side and delivered via a `data-*` attribute. The server remains the only source of
translated content (Non-Negotiable Principle 1).

## Real, tested proof

`test_phase9_5b_r_bidi_safety.py`/`test_phase9_5b_r_template_rendering.py` confirms both `data-confirm`
attributes render with the correct localized text in both locales, and that no inline `onsubmit=` JS
string remains anywhere in `employees/detail.html` (a regression guard against the fix being reverted).
