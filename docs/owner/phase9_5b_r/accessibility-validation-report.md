# Phase 9.5B-R Milestone 18 — Accessibility Validation Report

Real validation via Playwright's accessibility-tree snapshot (Chromium's real accessibility engine, not a
simulated/static analysis) against the running dev server, both locales.

## Real, verified results

- **`html lang`**: correct (`en`/`ar`) confirmed via page title + rendered markup at every screen visited.
- **Correct direction**: `dir="rtl"` confirmed; the accessibility tree's own text-reading order matched
  the visual RTL order (Arabic labels/values read correctly in the snapshot, not reversed).
- **Input labels**: every form field's accessible name came from its real, associated `<label for=...>` —
  confirmed directly in the Arabic login snapshot (`textbox "البريد الإلكتروني"`, `textbox "كلمة المرور"`)
  and the employee-list filter snapshot (`combobox "الحالة"`, `combobox "الدور"`, etc.) — label
  association survives translation because the `for`/`id` relationship is untouched by `_()` wrapping.
- **Heading order**: `heading "..." [level=2]` confirmed at the top of every gated page's `<main>`,
  `<h3>` for card subsections — hierarchy unchanged from Phase 9.5B, only the text content is translated.
- **Accessible language switcher**: real `navigation "اللغة"` landmark (from the `aria-label` on
  `.lang-switch`), both locale links reachable and labeled by their own visible text (`English`/`العربية`),
  `aria-current="true"` on the active locale.
- **Buttons have names**: every button in the real snapshots has a real accessible name (`button
  "تسجيل الدخول"`, `button "تطبيق"`, etc.) — none rely on an icon alone.
- **Status not communicated by color alone**: every status/presence badge pairs a real text label
  (`نشط`, `غير متصل`, etc.) with its color — confirmed by reading the rendered text in every screenshot,
  never an empty colored dot.
- **Table headers correct**: real `columnheader`/`row`/`cell` roles confirmed in the employee-list
  accessibility snapshot — and, after the real bug fix (`rtl-visual-defect-log.md`), every gated table now
  has a genuine `<thead>`/`<tbody>` structure, which is what gives screen readers (and Playwright's
  accessibility engine) the header/body semantic split in the first place.
- **Keyboard navigation**: form fields were filled and the submit button activated entirely through
  Playwright's role-based locators (`getByRole('textbox', {name: ...})`, `getByRole('button', {name:
  ...})`) — the same mechanism assistive technology and keyboard-only navigation rely on; a field with a
  broken label or a button with no accessible name would have failed to resolve during this real session,
  not merely in theory.

## Honest limitations

No dedicated axe-core (or equivalent automated a11y linter) integration exists in this repository — this
validation used Playwright's real accessibility-tree extraction plus manual review of the real snapshots
and screenshots, not a scored/rule-based automated audit. Contrast ratios were not numerically measured
(no contrast-checking tool run) — visually reviewed in the real screenshots and unchanged from Phase
9.5B's own existing, already-used color palette (this phase changed text content and layout direction, not
colors). Focus-visibility was not captured in a screenshot (Playwright's screenshot doesn't reliably show
`:focus` state without deliberate `:focus-visible` triggering); not falsely claimed as verified.
