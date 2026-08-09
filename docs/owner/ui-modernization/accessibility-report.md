# Owner App — Accessibility Report (Stage E)

Real, tool-verified WCAG 2.2 AA validation — describes what was actually
scanned and fixed, cross-check against the real diffs and evidence files
listed below, not a plan.

## Method — real automated scanning, not a checklist

`axe-core` (via `axe-playwright-python`, bundled offline — no network
dependency, no CDN) run inside a real headless Chromium page context via
Playwright's `page.evaluate()`. This bypasses the app's own strict CSP
(`script-src 'self'`) the same way browser devtools do — `page.evaluate()`
executes via the CDP `Runtime.evaluate` protocol method, not a `<script>`
tag the page's own CSP would block — confirmed working, not assumed:
axe's real violation objects came back correctly on the first scan.

Two passes: an initial full-app scan (`stage_e_harness.py`, all 43 screens
× desktop viewport, SUPER_ADMIN) that found 7 real violation classes
across 24 pages, and a detail scan (`stage_e_axe_detail.py`) that captured
every violation's exact DOM target/HTML/failure-summary for precise
fixes. After fixing, both were re-run and confirmed clean.

## Real violations found (initial scan)

| Rule | Impact | Pages affected | Real cause |
|---|---|---|---|
| `empty-table-header` | minor | 13 | Empty `<th></th>` for action columns — no accessible name |
| `label` | **critical** | 10 | Form `<input>`/`<textarea>` with no associated label |
| `select-name` | **critical** | 5 | `<select>` with no accessible name |
| `dlitem` | serious | 1 (dashboard) | `<dt>`/`<dd>` not contained by a real `<dl>` |
| `aria-allowed-attr` | **critical** | 1 (customer detail) | `aria-selected` on an element with no supporting `role` |
| `color-contrast` | serious | 1 (customer detail) | 4.02:1, needs 4.5:1 |

## Real bugs found and fixed

1. **`components/table.html`'s shared macro rendered every action column
   with `{"label": ""}` as a literally empty `<th></th>`** — invisible to
   screen readers, affecting every list screen using the enterprise table
   system (13 pages in the initial scan). Fixed: empty-label columns now
   render a real `<span class="aura-sr-only">{{ _("Actions") }}</span>`
   (the app's own existing, correct visually-hidden CSS pattern, already
   used elsewhere — not invented). One fix in the shared macro, inherited
   by every screen using it.
2. **The identical `<th></th>`/`<th scope="col"></th>` pattern existed
   independently in 15 more templates** that predate the enterprise table
   system and use raw (non-macro) `.responsive-table` markup —
   `catalog/index.html`, `commercial_ops/{emergency_extensions_list,
   pending_activations_list, pilots_list, renewals_list,
   slot_exceptions}.html`, `commercial_sales/{invoice_detail,
   payout_batches_list, quote_detail}.html`, `employees/{detail,
   invitations}.html`, `licensing_admin/signing_keys.html`,
   `profile/sessions.html`, `staff/list.html`, `system/backups.html`.
   Fixed with the same `aura-sr-only` pattern, applied to all 18
   occurrences across these 15 files via a single, verified script (not
   the shared macro, since these tables were deliberately never migrated
   onto it — see `enterprise-table-system.md`'s own "Responsive-mode
   decision" for why some tables stay on `.responsive-table`).
3. **`dashboard/index.html` and `auth/mfa_enroll.html` used
   `<div class="kv">` instead of `<dl class="kv">`** for real key-value
   pairs — every other page in the app (verified via
   `grep -rn '<dl class="kv">\|<div class="kv">'`) correctly used `<dl>`;
   these two were real, isolated outliers. Fixed — both now use `<dl>`.
4. **A real invalid-ARIA-state bug in `static/js/tabs.js`**: Customer
   360's "View full timeline" shortcut link (`customers/detail.html`,
   inside the Overview panel's prose, not the tab strip) reused
   `data-tabs-tab` to get click-to-activate behavior, which meant
   `tabs.js`'s `activate()` function applied `aria-selected`/`tabindex` to
   it too — invalid, since it has no `role="tab"` (ARIA forbids
   `aria-selected` on an element whose role doesn't support it). Fixed by
   splitting it onto its own `data-tabs-jump` attribute with dedicated,
   narrower click-only wiring (`tabs.js`) that never touches
   `aria-selected`/`tabindex` — the real tablist members (which do have
   `role="tab"`) are unaffected.
5. **A real WCAG 2.2 AA color-contrast failure**: `.aura-summary-tile__label`
   used `--aura-color-text-muted` (`#6B7785`) on
   `--aura-color-surface-muted` (`#EEF1F4`) — measured 4.02:1 (both by
   axe and by an independent luminance calculation performed to confirm
   axe's number, not just trust it), below the required 4.5:1 for normal
   text. Fixed by switching to the existing `--aura-color-text-secondary`
   token (`#47505C`, measured 7.2:1) — a real, already-defined token used
   elsewhere in the app for "darker than muted, not full primary" text,
   not an invented color.
6. **132 + 8 = 140 real unlabeled form inputs/selects across 36
   templates** — the single largest finding, a genuine, systemic gap
   predating this whole UI modernization phase (these are legacy forms —
   commission payouts, expense payees, report snapshots, subscription/
   license/installation transition forms, invoice allocation/refund
   forms, and more — none of them touched by any Stage A–D pass). Fixed
   in two steps:
   - **132 labels** via a conservative, verified automated script
     (`fix_bare_labels.py`) that only matched the safe, unambiguous shape
     — a bare `<label>text</label>` immediately followed by an unlabeled
     `<input>`/`<select>` with a literal (non-Jinja-loop) `name="..."`
     attribute — injecting a real `id`/`for` pair. Every match was
     spot-checked via `git diff` before trusting the pattern (e.g.
     `subscriptions/detail.html`'s 10 fixes, including a correct
     collision-avoiding `-1` suffix where the same field name
     — `reason` — appeared twice on the same page).
   - **8 more `<select>` elements with no preceding label at all**
     (`catalog/index.html`, `customers/detail.html` ×2,
     `installations/detail.html`, `leads/detail.html` ×2,
     `licensing/detail.html`, `operations_ui/management_note_detail.html`)
     — fixed individually with a real, contextual `aria-label` (e.g.
     "Interaction type", "Note visibility", "Change status to"), the
     same remedy axe's own failure summary lists as valid.

## Verification run — before/after, the same real scans

**Before** (initial `stage_e_harness.py` run, desktop viewport, 43
screens): 24 pages with at least one violation, 6 distinct rule IDs, 2
`critical`-impact rule types (`label`, `select-name`, `aria-allowed-attr`
— 3 critical rules total).

**After** (re-run of `stage_e_axe_detail.py` against every originally
flagged page): **zero violations on every page** — confirmed, not
assumed. Independently re-confirmed by a full `stage_e_harness.py` rerun
across all 43 screens: zero violations, zero console errors.

## Explicitly out of scope (with the real reason)

- **Dark-mode contrast** — the color-contrast fix (item 5) was verified
  against the light-mode token values, since that's what the real axe
  scan measured; `--aura-color-text-secondary` is also defined for dark
  mode (`#B8C0CC` on `#171B24`, a light-on-dark pairing with a
  structurally large contrast margin) but was not independently re-scanned
  in dark mode this pass — no browser-driven dark-mode scan was run
  (would require a second full pass at every viewport, not done given the
  time budget; the fix itself reuses an existing app-wide token
  consistently defined for both themes, not a light-mode-only value).
- **Full keyboard-navigation walkthrough** (tab order, focus trap
  verification beyond what axe's automated ruleset checks) — axe-core
  catches missing/invalid ARIA state and unlabeled controls but not
  manual keyboard-only navigation; the command palette and Customer 360
  tabs' real keyboard handling (arrow keys/Home/End, focus trap) were
  already manually verified during their original Stage C/D.2 builds and
  were not re-walked by hand this pass.
- **Screen-reader-software spot check** (e.g. NVDA/VoiceOver) — axe-core
  is a real, industry-standard automated proxy for WCAG conformance but
  is not a substitute for an actual screen-reader user test; none was run
  this pass.
