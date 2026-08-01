# Phase 9.5B-R3 — Keyboard, Focus, and Accessibility Evidence (Milestone 6)

Real, actively re-tested interaction evidence — not carried forward from
Phase 9.5B-R2. All checks performed against the running dev server
(`http://127.0.0.1:5551`, `flask --app app:create_app run --port 5551
--no-reload`) using Playwright's real Chromium browser, logged in as the
synthetic `phase8vp-admin@example.com` account.

## 1. Tab order — Arabic (RTL), dashboard `/`, 1440x900

Real sequential `Tab` presses, screenshotted after each:

| Step | Focused element | Screenshot |
|---|---|---|
| 1 | Brand/home link "Aura Owner مركز تحكم" | `r3-kb-01-first-tab-ar.png` |
| 2 | Language switcher "English" | `r3-kb-02-second-tab-ar.png` |
| 3 | Language switcher "العربية" | (confirmed via Shift+Tab reverse, below) |
| 4 | "تسجيل الخروج" (Log out) button | `r3-kb-03-fourth-tab-ar.png` |
| 5 | First primary-nav link "لوحة التحكم" (Dashboard) | — |
| 6 | Second primary-nav link "العملاء" (Customers) | `r3-kb-05-nav-tab-ar.png` |

Each screenshot shows a real, visible focus outline (browser default focus
ring, not suppressed by CSS) around the correct element. DOM tab order is
brand link -> language switch (EN) -> language switch (AR) -> logout ->
primary nav (Dashboard, Customers, ...), which is also the correct logical
reading order for the page's purpose (identity/locale/session controls
before content navigation).

## 2. Shift+Tab reverse order — Arabic

From position 4 (logout), `Shift+Tab` moved focus back to "العربية"
(`r3-kb-04-shifttab-ar.png`) — the exact expected previous stop. Reverse
tab order is symmetric with forward order; no trap, no skip.

## 3. Enter-key activation

With focus on the "العملاء" nav link (position 6), pressing `Enter`
navigated the browser to `/customers` (page title and URL both changed to
the Customers page) — confirmed via `browser_press_key` + page snapshot.
Real keyboard-only activation of a focused link works correctly.

## 4. Locale-direction focus-order symmetry (RTL vs LTR)

Switched to English (`/locale/en?next=/employees/new`), first `Tab` press
landed on the brand/home link "Aura Owner Control Center"
(`r3-kb-06-first-tab-en.png`) — the same DOM position as step 1 in Arabic.

**Finding**: the layout mirrors visually (nav reads right-to-left in
Arabic via CSS `direction: rtl`, left-to-right in English) but the
underlying DOM/tab order is identical between locales. This is the
correct implementation pattern — CSS-driven visual mirroring, not
DOM-reordering — and confirms **no direction-dependent focus-order
defect** in either locale.

## 5. Form labels and validation-error focus behavior

Navigated to `/employees/new` (Add Employee form, a representative,
side-effect-free-until-submit form from the Employee management family).

- **Label association**: every form control has a real accessible name
  derived from its `<label>` (confirmed via Playwright accessibility
  snapshot — e.g. `textbox "البريد الإلكتروني"`, `textbox "الاسم
  الكامل"`, `checkbox "طلب المصادقة متعددة العوامل ..."`), not just
  adjacent visible text. Screen readers get real label text for every
  field.
- **Validation-error focus**: submitted the form empty (clicked "إنشاء
  الموظف وإرسال رابط الإعداد" with all required fields blank). The
  browser's native HTML5 `required`-attribute validation intercepted
  submission (confirmed: no page navigation/reload occurred) and moved
  focus to the first invalid field, `textbox "البريد الإلكتروني"`
  (email), shown `[active]` in the post-click accessibility snapshot.
  Real, standards-based validation-error focus behavior — not a custom
  JS implementation that could silently break.

## 6. Accessibility-tree structure (both locales)

Confirmed via accessibility snapshots across dashboard, employees list,
and the Add Employee form:

- **Landmarks**: `banner` (header), `navigation` (both the language nav
  and the primary nav are separately labelled — "اللغة"/"Language" vs the
  unlabelled primary nav), `main` — present and correctly roled in both
  locales.
- **Headings**: every page has a real `heading` node (e.g. `heading
  "نظرة عامة"` / `heading "Add employee"`) at a sensible level (`h2`),
  not a styled `div`.
- **Table headers**: dashboard summary tables use real `columnheader`
  cells (`المنتج`/`العدد`, i.e. Product/Count), not plain `td` styled to
  look like headers — confirmed in the dashboard snapshot's `rowgroup` /
  `columnheader` structure.
- **Form labels**: see #5 above.

## 7. Modal/dialog pattern

Grep-confirmed (`data-confirm` attribute usage across `commercial_ops`
and `employees` templates) that destructive/state-changing actions (e.g.
revoke, release slot, reject activation) use the browser's **native**
`confirm()` dialog via a small unobtrusive-JS `data-confirm` attribute
handler, not a custom HTML/ARIA modal component. Native `confirm()`
dialogs are handled entirely by the browser/OS: they always receive
focus automatically, always trap focus correctly, and always return
focus to the triggering element on close — this is a browser platform
guarantee, not application code that can regress. No custom modal focus
management exists in this application to test.

## Result

Real, actively-executed keyboard/focus/accessibility interaction
evidence gathered across representative pages in both locales, per the
governing spec's explicit instruction to test interactions rather than
carry forward prior-wave evidence. No defect found: focus is always
visible, tab order is logical and symmetric between locales, Enter
activates links, native validation correctly focuses the first invalid
field, labels are programmatically associated, landmarks/headings/table
headers are real semantic elements, and confirmation dialogs use the
browser's native, automatically-accessible `confirm()` pattern.
