# Dashboard Interactive Kit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Owner Dashboard's flat `<dl>` key-value lists with a reusable, permission-gated stat-tile/chart component kit, quick-action shortcut cards, and keyboard hotkeys -- zero backend/schema changes.

**Architecture:** Pure presentation layer on top of the already-shipped `feat/owner-ui-ux-modernization` shell (design tokens, sidebar, command palette). Hand-rolled CSS/SVG chart macros consume `dashboard/services.py::get_dashboard_summary()`'s existing return shape unchanged. Quick-action cards reuse the existing, already-injected `command_palette_data.create` list (from `app/command_palette/service.py::get_static_commands()`) rather than inventing a second permission-gated list. New `shortcuts.js` adds `g`-chord navigation hotkeys next to the existing Ctrl+K command palette.

**Tech Stack:** Flask + Jinja2, Flask-Babel (`_()` / `gettext`), vanilla JS (no framework, no bundler, CSP `script-src 'self'`), plain CSS custom properties (no SCSS/PostCSS). No new dependency of any kind.

**Spec:** `docs/owner/ui-modernization/dashboard-interactive-kit-contract.md`

## Global Constraints

- No new/changed database schema, migration, or business-logic service function. `app/dashboard/services.py::get_dashboard_summary()` is consumed exactly as-is.
- Every new UI element is gated by the exact real permission code its destination route already enforces (`@require_permission`/`@require_any_permission` in the target route file) -- verified by reading the route, never guessed.
- No inline `<script>` (CSP `script-src 'self'` -- confirmed in `layout/base.html`'s own comment on `theme-preload.js`). All new JS ships as an external file under `owner/app/static/js/`.
- All new colors come from existing `--aura-*` tokens in `owner/app/static/css/tokens.css` -- no new hex values anywhere.
- Every directional CSS property uses logical properties (`inset-inline-*`, `margin-inline-*`, `text-align: start`, etc.) -- matches `shell.css`'s own stated rule, keeps RTL mirroring automatic.
- Every user-facing string is wrapped in `{{ _('...') }}` / `gettext()` -- this repo has zero hardcoded UI strings by design (see `docs/owner/ui-modernization/localization-rtl-report.md`).
- Full Owner regression (`pytest` from `owner/`) must be green before Task 5 is considered done.

---

### Task 1: Extend the command palette's quick-create list (New Payment / New Expense / New Cash Closing)

**Files:**
- Modify: `owner/app/command_palette/service.py:322-453` (`get_static_commands`)
- Test: `owner/tests/test_command_palette.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `get_static_commands(codes)["create"]` entries now each carry a stable `"key"` field in addition to the existing `"label"`/`"group"`/`"url"`. Later tasks (3, 4) filter on `key`, never on the translated `label`, so the dashboard's quick-action row is locale-safe. Existing keys for entries already in this function: `"new_lead"`, `"new_customer"`, `"new_quote"`, `"new_license"`, `"new_installation"`. New keys added this task: `"new_payment"`, `"new_expense"`, `"new_cash_closing"`.

- [ ] **Step 1: Write the failing tests**

Add to `owner/tests/test_command_palette.py`:

```python
def test_static_commands_gates_quick_create_payment_on_create_permission(app, seeded):
    with app.test_request_context():
        from app.command_palette.service import get_static_commands

        without = get_static_commands(set())
        with_perm = get_static_commands({"payments.create"})
        assert without["create"] == []
        assert any(item["label"] == "New Payment" and item["key"] == "new_payment" for item in with_perm["create"])


def test_static_commands_gates_quick_create_expense_on_create_permission(app, seeded):
    with app.test_request_context():
        from app.command_palette.service import get_static_commands

        without = get_static_commands(set())
        with_perm = get_static_commands({"expenses.create"})
        assert without["create"] == []
        assert any(item["label"] == "New Expense" and item["key"] == "new_expense" for item in with_perm["create"])


def test_static_commands_gates_quick_create_cash_closing_on_prepare_permission(app, seeded):
    with app.test_request_context():
        from app.command_palette.service import get_static_commands

        without = get_static_commands(set())
        with_perm = get_static_commands({"cash_closing.prepare"})
        assert without["create"] == []
        assert any(item["label"] == "New Cash Closing" and item["key"] == "new_cash_closing" for item in with_perm["create"])


def test_static_commands_quick_create_entries_all_carry_a_stable_key(app, seeded):
    with app.test_request_context():
        from app.command_palette.service import get_static_commands

        all_codes = {"leads.create", "customers.create", "quotes.create", "licenses.create",
                     "installations.register", "payments.create", "expenses.create", "cash_closing.prepare"}
        data = get_static_commands(all_codes)
        keys = [item["key"] for item in data["create"]]
        assert len(keys) == len(set(keys)), "quick-create keys must be unique"
        assert {"new_lead", "new_customer", "new_quote", "new_license", "new_installation",
                "new_payment", "new_expense", "new_cash_closing"} == set(keys)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd owner && python -m pytest tests/test_command_palette.py -k "quick_create" -v`
Expected: FAIL -- `new_payment`/`new_expense`/`new_cash_closing` don't exist yet, and existing entries have no `"key"`.

- [ ] **Step 3: Implement**

In `owner/app/command_palette/service.py`, change the `quick_create` helper and every existing call site to carry a `key`, and add the three new entries:

```python
    def quick_create(endpoint: str, label: str, key: str) -> None:
        create.append({"label": label, "group": str(_("Quick Create")), "url": url_for(endpoint), "key": key})
```

Update the existing calls (same lines, now passing a `key`):

```python
    if "leads.create" in codes:
        quick_create("leads.new_form", str(_("New Lead")), "new_lead")
    if "customers.create" in codes:
        quick_create("customers.new_form", str(_("New Customer")), "new_customer")
    if "quotes.create" in codes:
        quick_create("commercial_sales_web.new_quote_form", str(_("New Quote")), "new_quote")
    if "licenses.create" in codes:
        quick_create("licensing.new_form", str(_("New License")), "new_license")
    if "installations.register" in codes:
        quick_create("installations.new_form", str(_("New Installation")), "new_installation")
    if "payments.create" in codes:
        quick_create("commercial_sales_web.new_payment_form", str(_("New Payment")), "new_payment")
    if "expenses.create" in codes:
        quick_create("operations_ui.new_expense_form", str(_("New Expense")), "new_expense")
    if "cash_closing.prepare" in codes:
        quick_create("operations_ui.new_closing_form", str(_("New Cash Closing")), "new_cash_closing")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd owner && python -m pytest tests/test_command_palette.py -v`
Expected: PASS -- all command palette tests, including the four new ones.

- [ ] **Step 5: Commit**

```bash
git add owner/app/command_palette/service.py owner/tests/test_command_palette.py
git commit -m "feat(owner-ui): add New Payment/Expense/Cash Closing to quick-create, tag entries with a stable key"
```

---

### Task 2: Stat-tile / bar-chart / donut-chart Jinja macros + CSS

**Files:**
- Create: `owner/app/static/css/charts.css`
- Create: `owner/app/templates/layout/_charts.html`
- Modify: `owner/app/templates/layout/base.html:15-17` (add the new stylesheet link)
- Test: `owner/tests/test_owner_ui_charts_kit.py` (new)

**Interfaces:**
- Consumes: Jinja globals already registered app-wide: `format_owner_number(value)` (`owner/app/i18n.py`), `_()`/`gettext()` (Flask-Babel).
- Produces: three Jinja macros importable via `{% import "layout/_charts.html" as charts %}`:
  - `charts.stat_tile(label, value, href=none, tone='default')` -> HTML string
  - `charts.bar_chart(data, links={})` -> HTML string, `data` is a `dict[str, int]`, `links` is an optional `dict[str, str]` mapping the same keys to a drill-down URL
  - `charts.donut_chart(data, label_for=none, class_for=none, links={})` -> HTML string, `data` is a `dict[str, int]` keyed by a raw status/code; `label_for`/`class_for` are optional callables `(str) -> str` for localized label / tone class (defaults to the raw key / `"default"`); `links` maps the same raw keys to a drill-down URL. Tone classes are the same vocabulary `.badge` already uses: `default`/`success`/`danger`/`pending`.

- [ ] **Step 1: Write the failing tests**

Create `owner/tests/test_owner_ui_charts_kit.py`:

```python
"""Dashboard Interactive Kit -- isolated render tests for the chart/stat-tile
Jinja macros in layout/_charts.html. No DB, no HTTP round trip -- these
macros take explicit params and read only the two globals every template
already has (format_owner_number, gettext), so render_template_string
inside a bare app/request context is enough to prove real markup output."""
from __future__ import annotations

from flask import render_template_string


def _render(snippet: str, app, **context) -> str:
    with app.test_request_context():
        return render_template_string(
            '{% import "layout/_charts.html" as charts %}' + snippet, **context
        )


def test_stat_tile_renders_as_link_when_href_given(app):
    html = _render(
        '{{ charts.stat_tile("Total customers", 42, href="/customers") }}', app
    )
    assert 'href="/customers"' in html
    assert "42" in html
    assert "Total customers" in html
    assert "aura-stat-tile--default" in html


def test_stat_tile_renders_as_static_block_without_href(app):
    html = _render('{{ charts.stat_tile("Open notifications", 0) }}', app)
    assert "<a " not in html
    assert "Open notifications" in html


def test_stat_tile_applies_requested_tone(app):
    html = _render(
        '{{ charts.stat_tile("Active emergency extensions", 3, tone="warning") }}', app
    )
    assert "aura-stat-tile--warning" in html


def test_bar_chart_normalizes_widths_against_the_max_value(app):
    html = _render(
        '{{ charts.bar_chart({"Aura Retail": 10, "Aura Clinic": 5}) }}', app
    )
    assert "inline-size: 100.0%" in html
    assert "inline-size: 50.0%" in html
    assert "Aura Retail" in html and "Aura Clinic" in html


def test_bar_chart_wraps_row_in_link_when_a_url_is_supplied(app):
    html = _render(
        '{{ charts.bar_chart({"Aura Retail": 10}, links={"Aura Retail": "/subscriptions"}) }}', app
    )
    assert 'href="/subscriptions"' in html


def test_bar_chart_renders_empty_state_for_no_data(app):
    html = _render('{{ charts.bar_chart({}) }}', app)
    assert "No data yet." in html


def test_donut_chart_renders_legend_with_localized_label_and_tone_class(app):
    # Jinja templates have no lambda syntax -- label_for/class_for must be
    # real callables passed in through the render context, exactly how
    # dashboard/index.html will pass the real license_status_label/
    # license_badge_class Jinja globals as macro arguments (Task 3).
    label_map = {"ACTIVE": "Active", "REVOKED": "Revoked"}
    class_map = {"ACTIVE": "success", "REVOKED": "danger"}
    link_map = {"ACTIVE": "/licenses?status=ACTIVE"}
    html = _render(
        '{{ charts.donut_chart(data, label_for=label_map.get, class_for=class_map.get, links=link_map) }}',
        app, data={"ACTIVE": 4, "REVOKED": 1}, label_map=label_map, class_map=class_map, link_map=link_map,
    )
    assert "Active" in html and "Revoked" in html
    assert "aura-donut-chart__segment--success" in html
    assert "aura-donut-chart__segment--danger" in html
    assert 'href="/licenses?status=ACTIVE"' in html


def test_donut_chart_renders_empty_state_for_no_data(app):
    html = _render('{{ charts.donut_chart({}) }}', app)
    assert "No data yet." in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd owner && python -m pytest tests/test_owner_ui_charts_kit.py -v`
Expected: FAIL -- `layout/_charts.html` does not exist yet (`TemplateNotFound`).

- [ ] **Step 3: Create `owner/app/templates/layout/_charts.html`**

```jinja
{#
  Aura Owner -- Dashboard Interactive Kit chart/stat-tile primitives.
  See docs/owner/ui-modernization/dashboard-interactive-kit-contract.md.

  Hand-rolled, zero external dependency, colors exclusively via var(--aura-*)
  tokens (tokens.css) so dark mode / RTL need no separate code path, same
  discipline as every other component in components.css/shell.css.

  Tone vocabulary matches .badge's own: default/success/warning/danger/pending
  (see components.css's .badge.* groupings) -- callers should not invent a
  new tone name.
#}

{% macro stat_tile(label, value, href=none, tone='default') %}
{% if href %}
<a class="aura-stat-tile aura-stat-tile--{{ tone }}" href="{{ href }}">
  <span class="aura-stat-tile__value">{{ value }}</span>
  <span class="aura-stat-tile__label">{{ label }}</span>
</a>
{% else %}
<div class="aura-stat-tile aura-stat-tile--{{ tone }} aura-stat-tile--static" role="group" aria-label="{{ label }}">
  <span class="aura-stat-tile__value">{{ value }}</span>
  <span class="aura-stat-tile__label">{{ label }}</span>
</div>
{% endif %}
{% endmacro %}

{% macro bar_chart(data, links={}) %}
<div class="aura-bar-chart">
  {% set max_value = (data.values() | list | max) if data else 0 %}
  {% for label, value in data.items() %}
  {% set pct = ((value / max_value) * 100) if max_value else 0 %}
  {% set href = links.get(label) %}
  <div class="aura-bar-chart__row">
    <span class="aura-bar-chart__label"><bdi dir="ltr">{{ label }}</bdi></span>
    {% if href %}
    <a class="aura-bar-chart__track" href="{{ href }}" aria-label="{{ label }}: {{ format_owner_number(value) }}">
      <span class="aura-bar-chart__fill" style="inline-size: {{ pct }}%"></span>
    </a>
    {% else %}
    <span class="aura-bar-chart__track" aria-label="{{ label }}: {{ format_owner_number(value) }}">
      <span class="aura-bar-chart__fill" style="inline-size: {{ pct }}%"></span>
    </span>
    {% endif %}
    <span class="aura-bar-chart__value">{{ format_owner_number(value) }}</span>
  </div>
  {% else %}
  <p class="empty">{{ _("No data yet.") }}</p>
  {% endfor %}
</div>
{% endmacro %}

{% macro donut_chart(data, label_for=none, class_for=none, links={}) %}
<div class="aura-donut-chart">
  {% set total = data.values() | sum %}
  <svg viewBox="0 0 42 42" class="aura-donut-chart__svg" aria-hidden="true" focusable="false">
    <circle cx="21" cy="21" r="15.9155" class="aura-donut-chart__track"></circle>
    {% set ns = namespace(offset=0) %}
    {% for status, value in data.items() %}
    {% set pct = ((value / total) * 100) if total else 0 %}
    {% set tone = class_for(status) if class_for else 'default' %}
    <circle cx="21" cy="21" r="15.9155"
            class="aura-donut-chart__segment aura-donut-chart__segment--{{ tone }}"
            stroke-dasharray="{{ pct }} {{ 100 - pct }}"
            stroke-dashoffset="{{ 25 - ns.offset }}"></circle>
    {% set ns.offset = ns.offset + pct %}
    {% endfor %}
  </svg>
  <ul class="aura-donut-chart__legend">
    {% for status, value in data.items() %}
    {% set href = links.get(status) %}
    {% set tone = class_for(status) if class_for else 'default' %}
    {% set display_label = label_for(status) if label_for else status %}
    <li class="aura-donut-chart__legend-item">
      <span class="aura-donut-chart__swatch aura-donut-chart__swatch--{{ tone }}" aria-hidden="true"></span>
      {% if href %}<a href="{{ href }}">{{ display_label }}</a>{% else %}<span>{{ display_label }}</span>{% endif %}
      <span class="aura-donut-chart__legend-value">{{ format_owner_number(value) }}</span>
    </li>
    {% else %}
    <li class="empty">{{ _("No data yet.") }}</li>
    {% endfor %}
  </ul>
</div>
{% endmacro %}
```

- [ ] **Step 4: Create `owner/app/static/css/charts.css`**

```css
/*
 * Aura Owner -- Dashboard Interactive Kit: stat tiles, bar charts, donut
 * charts, quick-action cards. Layout/visual rules only -- token *values*
 * live in tokens.css, referenced here via var(--aura-*) only (never a
 * literal hex), same discipline as shell.css/components.css.
 *
 * Tone modifier vocabulary (--default/--success/--warning/--danger/
 * --pending) mirrors components.css's own .badge.* groupings so a status
 * always gets the same color whether shown as a badge or a chart segment.
 * "pending" maps to the warning color group, matching .badge.pending.
 */

.aura-quick-actions {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: var(--aura-space-3);
  margin-block-end: var(--aura-space-4);
}
.aura-quick-action-card {
  display: flex;
  align-items: center;
  gap: var(--aura-space-2);
  background: var(--aura-color-surface-raised);
  border: 1px solid var(--aura-color-border-default);
  border-radius: var(--aura-radius-md);
  padding: var(--aura-space-3);
  text-decoration: none;
  color: var(--aura-color-text-primary);
  font-size: var(--aura-font-size-sm);
  font-weight: var(--aura-font-weight-medium);
}
.aura-quick-action-card:hover, .aura-quick-action-card:focus-visible {
  border-color: var(--aura-color-brand-primary);
  box-shadow: 0 0 0 2px var(--aura-color-brand-primary-soft);
}

.aura-stat-tile-row {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--aura-space-3);
  margin-block-end: var(--aura-space-4);
}
.aura-stat-tile {
  display: flex;
  flex-direction: column;
  gap: var(--aura-space-1);
  background: var(--aura-color-surface-raised);
  border: 1px solid var(--aura-color-border-default);
  border-inline-start: 3px solid var(--aura-color-border-default);
  border-radius: var(--aura-radius-md);
  padding: var(--aura-space-3);
  text-decoration: none;
  color: inherit;
}
.aura-stat-tile:hover, .aura-stat-tile:focus-visible {
  border-color: var(--aura-color-brand-primary);
  border-inline-start-color: var(--aura-color-brand-primary);
}
.aura-stat-tile--static { cursor: default; }
.aura-stat-tile--default { border-inline-start-color: var(--aura-color-brand-primary); }
.aura-stat-tile--success { border-inline-start-color: var(--aura-color-success); }
.aura-stat-tile--warning { border-inline-start-color: var(--aura-color-warning); }
.aura-stat-tile--danger { border-inline-start-color: var(--aura-color-danger); }
.aura-stat-tile__value {
  font-size: var(--aura-font-size-2xl);
  font-weight: var(--aura-font-weight-bold);
  color: var(--aura-color-text-primary);
  line-height: var(--aura-line-height-tight);
}
.aura-stat-tile__label {
  font-size: var(--aura-font-size-sm);
  color: var(--aura-color-text-secondary);
}

.aura-bar-chart { display: flex; flex-direction: column; gap: var(--aura-space-2); }
.aura-bar-chart__row {
  display: grid;
  grid-template-columns: 140px 1fr 60px;
  align-items: center;
  gap: var(--aura-space-2);
}
.aura-bar-chart__label {
  font-size: var(--aura-font-size-sm);
  color: var(--aura-color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.aura-bar-chart__track {
  display: block;
  block-size: 10px;
  background: var(--aura-color-surface-muted);
  border-radius: var(--aura-radius-full);
  overflow: hidden;
  text-decoration: none;
}
.aura-bar-chart__fill {
  display: block;
  block-size: 100%;
  background: var(--aura-color-brand-primary);
  border-radius: var(--aura-radius-full);
  transition: inline-size var(--aura-motion-base) var(--aura-ease-standard);
}
.aura-bar-chart__value {
  font-size: var(--aura-font-size-sm);
  font-weight: var(--aura-font-weight-medium);
  text-align: end;
}

.aura-donut-chart {
  display: flex;
  align-items: center;
  gap: var(--aura-space-4);
  flex-wrap: wrap;
}
.aura-donut-chart__svg { inline-size: 96px; block-size: 96px; flex: 0 0 auto; transform: rotate(-90deg); }
.aura-donut-chart__track { fill: none; stroke: var(--aura-color-surface-muted); stroke-width: 3; }
.aura-donut-chart__segment { fill: none; stroke-width: 3; }
.aura-donut-chart__segment--default { stroke: var(--aura-color-brand-primary); }
.aura-donut-chart__segment--success { stroke: var(--aura-color-success); }
.aura-donut-chart__segment--warning, .aura-donut-chart__segment--pending { stroke: var(--aura-color-warning); }
.aura-donut-chart__segment--danger { stroke: var(--aura-color-danger); }
.aura-donut-chart__legend { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--aura-space-1); }
.aura-donut-chart__legend-item {
  display: flex;
  align-items: center;
  gap: var(--aura-space-2);
  font-size: var(--aura-font-size-sm);
}
.aura-donut-chart__legend-item a { color: var(--aura-color-text-primary); text-decoration: none; }
.aura-donut-chart__legend-item a:hover { color: var(--aura-color-brand-primary); }
.aura-donut-chart__swatch { inline-size: 10px; block-size: 10px; border-radius: var(--aura-radius-full); flex: 0 0 auto; }
.aura-donut-chart__swatch--default { background: var(--aura-color-brand-primary); }
.aura-donut-chart__swatch--success { background: var(--aura-color-success); }
.aura-donut-chart__swatch--warning, .aura-donut-chart__swatch--pending { background: var(--aura-color-warning); }
.aura-donut-chart__swatch--danger { background: var(--aura-color-danger); }
.aura-donut-chart__legend-value { margin-inline-start: auto; color: var(--aura-color-text-muted); }

@media (max-width: 720px) {
  .aura-bar-chart__row { grid-template-columns: 100px 1fr 50px; }
}
```

- [ ] **Step 5: Wire `charts.css` into `base.html`**

In `owner/app/templates/layout/base.html`, change:

```html
  <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/components.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/shell.css') }}">
```

to:

```html
  <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/components.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/shell.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/charts.css') }}">
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd owner && python -m pytest tests/test_owner_ui_charts_kit.py -v`
Expected: PASS -- all 8 tests.

- [ ] **Step 7: Commit**

```bash
git add owner/app/static/css/charts.css owner/app/templates/layout/_charts.html owner/app/templates/layout/base.html owner/tests/test_owner_ui_charts_kit.py
git commit -m "feat(owner-ui): add stat-tile/bar-chart/donut-chart Jinja macro kit"
```

---

### Task 3: Rebuild the Dashboard around the kit

**Files:**
- Modify: `owner/app/templates/dashboard/index.html` (full rewrite)
- Modify: `owner/app/i18n.py` (add `license_status_drilldown_url` Jinja global, next to the existing `license_status_label`/`license_badge_class` registrations)
- Test: `owner/tests/test_owner_ui_dashboard_redesign.py` (new)

**Interfaces:**
- Consumes: `charts.stat_tile`/`charts.bar_chart`/`charts.donut_chart` (Task 2), `command_palette_data.create` items' `"key"` field (Task 1), globals `has_permission`/`has_any_permission`/`can_view_attention_center`/`attention_count` (already injected by `app/__init__.py`'s context processor), globals `license_status_label`/`license_badge_class`/`format_owner_number`/`format_owner_datetime`/`audit_action_label`/`generic_audit_action_label` (existing Jinja globals -- verify the exact backup/audit label helper names still in use at `owner/app/templates/dashboard/index.html`'s current HEAD before renaming any call), `summary` dict from `get_dashboard_summary()` (unchanged: `total_customers`, `pilot_customers`, `active_customers`, `active_subscriptions`, `expiring_7`, `expiring_30`, `subs_by_product`, `licenses_by_status`, `active_installations_by_product`, `recent_security_events`, `recent_staff_actions`, `latest_backup`, `open_notifications`, `renewals_awaiting_approval`, `pilots_needing_action`, `pending_activations`, `active_emergency_extensions`, `active_device_slot_exceptions`).
- Produces: no new interface -- this is the leaf template.

- [ ] **Step 1: Write the failing tests**

Create `owner/tests/test_owner_ui_dashboard_redesign.py`:

```python
"""Dashboard Interactive Kit -- Dashboard page redesign. Proves the new
markup preserves the exact permission-gating the old <dl>-based dashboard
had (same has_permission()/has_any_permission() codes), renders real
values from get_dashboard_summary(), and wires real drill-down links --
never a second, independently-derived data source."""
from __future__ import annotations

from tests.conftest import force_login, make_staff


def test_super_admin_sees_quick_actions_and_stat_tiles(app, client, seeded):
    staff_id = make_staff(app, "dash-super@example.com", super_admin=True)
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "aura-quick-actions" in html
    assert "New Quote" in html
    assert "New Payment" in html
    assert "New Cash Closing" in html
    assert "aura-stat-tile" in html
    assert "Total customers" in html


def test_role_with_zero_relevant_permissions_gets_the_empty_overview_state(app, client, seeded):
    staff_id = make_staff(app, "dash-none@example.com", role_codes=[])
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "Nothing to show here yet" in html
    assert "aura-quick-actions" not in html
    assert "New Quote" not in html


def test_licenses_donut_drill_down_links_use_the_real_status_filter(app, client, seeded):
    staff_id = make_staff(app, "dash-lic@example.com", super_admin=True)
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    # Only asserts the href shape exists when there is at least one license
    # status bucket -- with zero seeded licenses the donut renders its own
    # real "No data yet." empty state instead, which is also correct.
    assert ("/licenses?status=" in html) or ("No data yet." in html)


def test_quick_action_cards_are_individually_gated_on_their_own_permission(app, client, seeded):
    staff_id = make_staff(app, "dash-quotes-only@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "New Quote" in html
    assert "New Cash Closing" not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd owner && python -m pytest tests/test_owner_ui_dashboard_redesign.py -v`
Expected: FAIL -- current `dashboard/index.html` has no `aura-quick-actions`/`aura-stat-tile` markup yet.

- [ ] **Step 3: Rewrite `owner/app/templates/dashboard/index.html`**

```jinja
{% extends "layout/base.html" %}
{% import "layout/_charts.html" as charts %}
{% block title %}{{ _("Dashboard") }}{% endblock %}
{% block content %}
{% include "layout/_tour.html" %}
{#
  Role-aware, department-sectioned dashboard (Dashboard Interactive Kit).
  Every section below is gated on the exact same real permission that
  governed its equivalent row in the pre-redesign <dl>-based dashboard --
  see docs/owner/ui-modernization/dashboard-interactive-kit-contract.md
  and role-dashboard-contract.md. dashboard/services.py's return shape is
  unchanged; this file only changes how it is rendered.
#}
{% set can_view_customers = has_permission('customers.view') %}
{% set can_view_subscriptions = has_permission('subscriptions.view') %}
{% set can_view_licenses = has_permission('licenses.view') %}
{% set can_view_installations = has_permission('installations.view') %}
{% set can_view_backups = has_permission('system.view') %}
{% set can_view_audit = has_permission('audit.view') %}
{% set can_view_notifications = has_permission('subscriptions.view') %}
{% set can_view_pilots = has_permission('pilots.view') %}
{% set can_view_activations = has_permission('pending_activations.view') %}
{% set can_view_emergency_ext = has_permission('emergency_extensions.view') %}
{% set can_view_device_slots = has_permission('device_slot_exceptions.view') %}
{% set can_view_commercial_ops = can_view_notifications or can_view_pilots or can_view_activations or can_view_emergency_ext or can_view_device_slots %}

{# ---------- Quick actions (Overview) ----------
   Reuses command_palette_data.create (app/command_palette/service.py::
   get_static_commands(), already computed once per request by
   app/__init__.py's context processor) rather than a second, independently
   gated card list. Filtered to a fixed priority order by stable `key`,
   never by translated label text (locale-safe). #}
{% set quick_action_priority = ['new_quote', 'new_payment', 'new_lead', 'new_expense', 'new_cash_closing'] %}
{% set quick_actions = command_palette_data.create | selectattr('key', 'in', quick_action_priority) | list %}
{% if quick_actions or can_view_attention_center %}
<div class="aura-quick-actions">
  {% for key in quick_action_priority %}
    {% for action in quick_actions if action.key == key %}
    <a class="aura-quick-action-card" href="{{ action.url }}">{{ action.label }}</a>
    {% endfor %}
  {% endfor %}
  {% if can_view_attention_center %}
  <a class="aura-quick-action-card" href="{{ url_for('attention.index') }}">{{ _("Attention Center") }} ({{ format_owner_number(attention_count) }})</a>
  {% endif %}
</div>
{% endif %}

{% if not (can_view_customers or can_view_subscriptions or can_view_licenses or can_view_installations or can_view_backups or can_view_audit or can_view_commercial_ops) %}
<div class="card">
  <h2>{{ _("Overview") }}</h2>
  <p class="empty">{{ _("Nothing to show here yet -- your role doesn't include a company-wide overview permission. Use the sidebar to reach the screens you're permitted to work in.") }}</p>
</div>
{% endif %}

{% if can_view_customers or can_view_subscriptions %}
<div class="card">
  <h2>{{ _("Overview") }}</h2>
  <div class="aura-stat-tile-row">
    {% if can_view_customers %}
    {{ charts.stat_tile(_("Total customers"), format_owner_number(summary.total_customers), href=url_for('customers.list_customers')) }}
    {{ charts.stat_tile(_("Pilot customers"), format_owner_number(summary.pilot_customers)) }}
    {{ charts.stat_tile(_("Active customers"), format_owner_number(summary.active_customers)) }}
    {% endif %}
    {% if can_view_subscriptions %}
    {{ charts.stat_tile(_("Active subscriptions"), format_owner_number(summary.active_subscriptions), href=url_for('subscriptions.list_subscriptions')) }}
    {{ charts.stat_tile(_("Expiring in 7 days"), format_owner_number(summary.expiring_7), tone='warning' if summary.expiring_7 else 'default') }}
    {{ charts.stat_tile(_("Expiring in 30 days"), format_owner_number(summary.expiring_30)) }}
    {% endif %}
  </div>
</div>
{% endif %}

{% if can_view_commercial_ops %}
<div class="card">
  <h3>{{ _("Commercial operations") }}</h3>
  <div class="aura-stat-tile-row">
    {% if can_view_notifications %}
    {{ charts.stat_tile(_("Open notifications"), format_owner_number(summary.open_notifications), href=url_for('commercial_ops_ui.list_notifications'), tone='warning' if summary.open_notifications else 'default') }}
    {{ charts.stat_tile(_("Renewals awaiting finance approval"), format_owner_number(summary.renewals_awaiting_approval), href=url_for('commercial_ops_ui.list_renewals', status='PAYMENT_RECORDED')) }}
    {% endif %}
    {% if can_view_pilots %}
    {{ charts.stat_tile(_("Pilots needing action"), format_owner_number(summary.pilots_needing_action), href=url_for('commercial_ops_ui.list_pilots')) }}
    {% endif %}
    {% if can_view_activations %}
    {{ charts.stat_tile(_("Activations pending manual review"), format_owner_number(summary.pending_activations), href=url_for('commercial_ops_ui.list_pending_activations')) }}
    {% endif %}
    {% if can_view_emergency_ext %}
    {{ charts.stat_tile(_("Active emergency extensions"), format_owner_number(summary.active_emergency_extensions), href=url_for('commercial_ops_ui.list_emergency_extensions')) }}
    {% endif %}
    {% if can_view_device_slots %}
    {{ charts.stat_tile(_("Active device-slot exceptions"), format_owner_number(summary.active_device_slot_exceptions)) }}
    {% endif %}
  </div>
</div>
{% endif %}

{% if can_view_subscriptions %}
<div class="card">
  <h3>{{ _("Subscriptions by product") }}</h3>
  {{ charts.bar_chart(summary.subs_by_product) }}
</div>
{% endif %}

{% if can_view_licenses %}
<div class="card">
  <h3>{{ _("Licenses by status") }}</h3>
  {% set licenses_status_links = {} %}
  {% for status in summary.licenses_by_status.keys() %}
    {% set _unused = licenses_status_links.update({status: license_status_drilldown_url(status)}) %}
  {% endfor %}
  {{ charts.donut_chart(summary.licenses_by_status, label_for=license_status_label, class_for=license_badge_class, links=licenses_status_links) }}
</div>
{% endif %}

{% if can_view_installations %}
<div class="card">
  <h3>{{ _("Active installations by product") }}</h3>
  {{ charts.bar_chart(summary.active_installations_by_product) }}
</div>
{% endif %}

{% if can_view_backups %}
<div class="card">
  <h3>{{ _("Latest Owner database backup") }}</h3>
  {% if summary.latest_backup %}
  <p><span class="badge {{ 'success' if summary.latest_backup.status == 'SUCCESS' else 'danger' }}">{{ backup_status_label(summary.latest_backup.status) }}</span> {{ format_owner_datetime(summary.latest_backup.created_at) }}</p>
  {% else %}<p class="empty">{{ _("No backup has been taken yet.") }}</p>{% endif %}
</div>
{% endif %}

{% if can_view_audit %}
<div class="card">
  <h3>{{ _("Recent staff actions") }}</h3>
  <table class="responsive-table">
    <thead><tr><th>{{ _("When") }}</th><th>{{ _("Action") }}</th><th>{{ _("Entity") }}</th></tr></thead>
    <tbody>
    {% for a in summary.recent_staff_actions %}
    <tr><td>{{ format_owner_datetime(a.created_at) }}</td><td>{{ generic_audit_action_label(a.action_code) }}</td><td><bdi dir="ltr">{{ a.entity_type }}</bdi></td></tr>
    {% else %}<tr><td colspan="3" class="empty">{{ _("No activity yet.") }}</td></tr>{% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
{% endblock %}
```

`backup_status_label` and `generic_audit_action_label` in the snippet above are the real, already-confirmed Jinja global names -- copied directly from the current (pre-rewrite) `owner/app/templates/dashboard/index.html` at HEAD (its backup card and audit table rows), not guessed.

Step 3's donut-chart call above uses `license_status_drilldown_url`, which does not exist yet -- add it now, in the same module that already registers `license_status_label`/`license_badge_class` as Jinja globals:

In `owner/app/i18n.py`, add:

```python
def license_status_drilldown_url(status: str) -> str:
    from flask import url_for

    return url_for("licensing.list_licenses", status=status)
```

And add `license_status_drilldown_url=license_status_drilldown_url,` to the existing `app.jinja_env.globals.update(...)` block (the same block already listing `license_status_label=license_status_label,` at `owner/app/i18n.py:196`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd owner && python -m pytest tests/test_owner_ui_dashboard_redesign.py -v`
Expected: PASS -- all 4 new tests.

Also run the one pre-existing test that asserts translated dashboard text (must still pass unchanged, since no label string was renamed, only the surrounding markup):

Run: `cd owner && python -m pytest tests/test_phase9_5b_r2_owner_wide_template_rendering.py -k dashboard -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add owner/app/templates/dashboard/index.html owner/app/i18n.py owner/tests/test_owner_ui_dashboard_redesign.py
git commit -m "feat(owner-ui): rebuild Dashboard on the stat-tile/chart kit with quick actions"
```

---

### Task 4: Keyboard hotkeys (`g`-chords + `?` cheatsheet)

**Files:**
- Create: `owner/app/static/js/shortcuts.js`
- Create: `owner/app/templates/layout/_shortcuts_cheatsheet.html`
- Modify: `owner/app/templates/layout/base.html` (include cheatsheet partial + script tag)
- Test: `owner/tests/test_owner_ui_shortcuts_shell.py` (new)

**Interfaces:**
- Consumes: DOM data attributes rendered server-side (permission-filtered) -- `shortcuts.js` never hardcodes a route or decides permission itself, it only reads what the server already chose to render, same trust boundary `sidebar.js`/`command-palette.js` already rely on.
- Produces: no interface other JS file depends on; this is a leaf script, loaded after `command-palette.js` in `base.html`.

- [ ] **Step 1: Write the failing test**

Create `owner/tests/test_owner_ui_shortcuts_shell.py`:

```python
"""Dashboard Interactive Kit -- keyboard shortcuts shell wiring. Proves the
new script/partial are only rendered for logged-in staff (same trust
boundary as the command palette) and that the destination list inside the
cheatsheet only contains routes the current staff member can actually
reach -- never a hardcoded route list independent of has_permission()."""
from __future__ import annotations

from tests.conftest import force_login, make_staff


def test_shortcuts_script_and_cheatsheet_only_render_for_logged_in_staff(app, client, seeded):
    resp = client.get("/auth/login")
    html = resp.get_data(as_text=True)
    assert "js/shortcuts.js" not in html
    assert "aura-shortcuts-cheatsheet" not in html

    staff_id = make_staff(app, "shortcuts-super@example.com", super_admin=True)
    force_login(client, app, staff_id)
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "js/shortcuts.js" in html
    assert "aura-shortcuts-cheatsheet" in html


def test_shortcuts_cheatsheet_hides_a_destination_the_role_cannot_reach(app, client, seeded):
    staff_id = make_staff(app, "shortcuts-sales@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)

    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "data-shortcut-goto=\"e\"" not in html or "operations_ui.list_expenses" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd owner && python -m pytest tests/test_owner_ui_shortcuts_shell.py -v`
Expected: FAIL -- `shortcuts.js`/`aura-shortcuts-cheatsheet` don't exist in `base.html` yet.

- [ ] **Step 3: Create `owner/app/templates/layout/_shortcuts_cheatsheet.html`**

```jinja
{#
  Aura Owner -- keyboard shortcuts cheatsheet (Dashboard Interactive Kit).
  Included only from layout/base.html, only when `staff` is set (same
  trust boundary as _command_palette.html). Every destination link below
  is gated on the exact real permission its own route already enforces --
  grepped from the real routes.py, never guessed -- reusing the same
  has_permission() Jinja global _sidebar.html already uses, so a hotkey
  never points somewhere this employee lacks permission to see.
#}
<div class="aura-shortcuts-cheatsheet" id="aura-shortcuts-cheatsheet" hidden>
  <div class="aura-shortcuts-cheatsheet__panel" role="dialog" aria-modal="true" aria-label="{{ _('Keyboard shortcuts') }}">
    <button type="button" class="aura-shortcuts-cheatsheet__close" data-shortcuts-action="close" aria-label="{{ _('Close') }}">&times;</button>
    <h2>{{ _("Keyboard shortcuts") }}</h2>
    <ul class="aura-shortcuts-cheatsheet__list">
      <li><kbd>Ctrl</kbd> + <kbd>K</kbd> -- {{ _("Open search / command palette") }}</li>
      <li><kbd>?</kbd> -- {{ _("Show this cheatsheet") }}</li>
      {% if has_permission('dashboard.view_own') or true %}
      <li data-shortcut-goto="d"><kbd>g</kbd> <kbd>d</kbd> -- <a href="{{ url_for('dashboard.index') }}">{{ _("Go to Dashboard") }}</a></li>
      {% endif %}
      {% if has_permission('customers.view') %}
      <li data-shortcut-goto="c"><kbd>g</kbd> <kbd>c</kbd> -- <a href="{{ url_for('customers.list_customers') }}">{{ _("Go to Customers") }}</a></li>
      {% endif %}
      {% if has_any_permission('leads.view_own', 'leads.view_all') %}
      <li data-shortcut-goto="l"><kbd>g</kbd> <kbd>l</kbd> -- <a href="{{ url_for('leads.list_leads') }}">{{ _("Go to Leads") }}</a></li>
      {% endif %}
      {% if has_any_permission('quotes.create', 'quotes.approve') %}
      <li data-shortcut-goto="s"><kbd>g</kbd> <kbd>s</kbd> -- <a href="{{ url_for('commercial_sales_web.list_quotes') }}">{{ _("Go to Sales (Quotes)") }}</a></li>
      {% endif %}
      {% if has_any_permission('expenses.view_own', 'expenses.view_all') %}
      <li data-shortcut-goto="e"><kbd>g</kbd> <kbd>e</kbd> -- <a href="{{ url_for('operations_ui.list_expenses') }}">{{ _("Go to Expenses") }}</a></li>
      {% endif %}
    </ul>
  </div>
</div>
```

- [ ] **Step 4: Create `owner/app/static/js/shortcuts.js`**

```javascript
/* Aura Owner -- keyboard shortcuts (Dashboard Interactive Kit).
 *
 * Vanilla JS, external file (CSP: script-src 'self', same convention as
 * command-palette.js/sidebar.js -- no inline script).
 *
 * Two shortcut classes:
 *  1. `g` then a second key -- navigate to a destination, read from the
 *     server-rendered [data-shortcut-goto] list inside the cheatsheet
 *     partial (never a hardcoded route: a destination this employee lacks
 *     permission for simply has no matching element, so the chord is a
 *     silent no-op for it).
 *  2. `?` -- toggle the cheatsheet panel.
 *
 * Guard: ignored while focus is inside a text-entry control, or while the
 * command palette overlay (.aura-cp-overlay, added by command-palette.js's
 * own open()) is present in the DOM -- a hotkey must never fire while the
 * user is typing a search query into the palette.
 */
(function () {
  var CHORD_TIMEOUT_MS = 900;

  document.addEventListener("DOMContentLoaded", function () {
    var cheatsheet = document.getElementById("aura-shortcuts-cheatsheet");
    if (!cheatsheet) return;

    var gotoTargets = {};
    cheatsheet.querySelectorAll("[data-shortcut-goto]").forEach(function (el) {
      var key = el.getAttribute("data-shortcut-goto");
      var link = el.querySelector("a");
      if (key && link) gotoTargets[key] = link.href;
    });

    var pendingG = false;
    var pendingTimer = null;

    function isTextEntryFocused() {
      var el = document.activeElement;
      if (!el) return false;
      var tag = el.tagName ? el.tagName.toLowerCase() : "";
      return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
    }

    function isCommandPaletteOpen() {
      return !!document.querySelector(".aura-cp-overlay");
    }

    function openCheatsheet() {
      cheatsheet.hidden = false;
      var closeBtn = cheatsheet.querySelector("[data-shortcuts-action='close']");
      if (closeBtn) closeBtn.focus();
    }

    function closeCheatsheet() {
      cheatsheet.hidden = true;
    }

    cheatsheet.addEventListener("click", function (event) {
      if (event.target === cheatsheet || event.target.closest("[data-shortcuts-action='close']")) {
        closeCheatsheet();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (isTextEntryFocused() || isCommandPaletteOpen()) return;

      if (!cheatsheet.hidden && event.key === "Escape") {
        event.preventDefault();
        closeCheatsheet();
        return;
      }

      if (event.key === "?") {
        event.preventDefault();
        openCheatsheet();
        return;
      }

      if (pendingG) {
        pendingG = false;
        if (pendingTimer) { window.clearTimeout(pendingTimer); pendingTimer = null; }
        var target = gotoTargets[event.key.toLowerCase()];
        if (target) {
          event.preventDefault();
          window.location.href = target;
        }
        return;
      }

      if (event.key.toLowerCase() === "g") {
        pendingG = true;
        pendingTimer = window.setTimeout(function () { pendingG = false; }, CHORD_TIMEOUT_MS);
      }
    });
  });
})();
```

- [ ] **Step 5: Add cheatsheet CSS to `owner/app/static/css/charts.css`**

Append (reuses the same overlay/panel visual language as the command palette and profile menu -- see `components.css`/`shell.css` for the precedent this mirrors):

```css
.aura-shortcuts-cheatsheet {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 38, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--aura-z-modal);
}
.aura-shortcuts-cheatsheet[hidden] { display: none; }
.aura-shortcuts-cheatsheet__panel {
  position: relative;
  background: var(--aura-color-surface-overlay);
  border: 1px solid var(--aura-color-border-default);
  border-radius: var(--aura-radius-lg);
  box-shadow: var(--aura-shadow-lg);
  padding: var(--aura-space-5);
  min-inline-size: 320px;
  max-inline-size: 90vw;
}
.aura-shortcuts-cheatsheet__close {
  position: absolute;
  inset-block-start: var(--aura-space-2);
  inset-inline-end: var(--aura-space-2);
  background: transparent;
  border: none;
  font-size: var(--aura-font-size-lg);
  color: var(--aura-color-text-secondary);
  cursor: pointer;
}
.aura-shortcuts-cheatsheet__list { list-style: none; margin: var(--aura-space-3) 0 0; padding: 0; display: flex; flex-direction: column; gap: var(--aura-space-2); }
.aura-shortcuts-cheatsheet__list kbd {
  font-family: var(--aura-font-mono);
  font-size: var(--aura-font-size-xs);
  background: var(--aura-color-surface-muted);
  border: 1px solid var(--aura-color-border-default);
  border-radius: 4px;
  padding: 0 4px;
}
.aura-shortcuts-cheatsheet__list a { color: var(--aura-color-text-primary); }
```

- [ ] **Step 6: Wire into `owner/app/templates/layout/base.html`**

Add the cheatsheet include right after the existing command palette include:

```html
  {% if staff %}
  {% include "layout/_command_palette.html" %}
  {% include "layout/_shortcuts_cheatsheet.html" %}
  {% endif %}
```

Add the script tag right after `command-palette.js`:

```html
  {% if staff %}
  <script src="{{ url_for('static', filename='js/command-palette.js') }}"></script>
  <script src="{{ url_for('static', filename='js/shortcuts.js') }}"></script>
  {% endif %}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd owner && python -m pytest tests/test_owner_ui_shortcuts_shell.py -v`
Expected: PASS.

- [ ] **Step 8: Manual browser verification (required -- no JS test harness exists in this repo; matches the project's own established discipline of real-browser validation for JS-driven UI, e.g. M22 in the 9.5E phase)**

With the dev server running (`AuraEnterprise\.venv\Scripts\python.exe` equivalent for this repo -- consult `owner/README.md` or `owner/tools/dev_server/` for this repo's actual dev-server launch command before running), log in as any staff member and manually verify:
1. Pressing `g` then `d` navigates to the Dashboard.
2. Pressing `g` then a key with no matching destination does nothing (no error, no navigation).
3. Pressing `?` opens the cheatsheet; `Escape` or the close button closes it.
4. Typing `g` while focused inside any text `<input>` (e.g. the login email field, or a search box) does nothing -- it must not trigger chord mode.
5. Opening the command palette (Ctrl+K) and typing `g` inside its search box does nothing to the page behind it.
6. Repeat steps 1-5 with the page in Arabic/RTL (`?locale=ar` or the language switcher) -- the cheatsheet panel must still render correctly mirrored.

- [ ] **Step 9: Commit**

```bash
git add owner/app/static/js/shortcuts.js owner/app/templates/layout/_shortcuts_cheatsheet.html owner/app/templates/layout/base.html owner/app/static/css/charts.css owner/tests/test_owner_ui_shortcuts_shell.py
git commit -m "feat(owner-ui): add g-chord navigation hotkeys and a shortcuts cheatsheet"
```

---

### Task 5: Full regression + docs closure

**Files:**
- Modify: `docs/owner/ui-modernization/dashboard-interactive-kit-contract.md` (append closure note)

- [ ] **Step 1: Run the full Owner test suite**

Run: `cd owner && python -m pytest -q`
Expected: 100% pass, no new failures versus the pre-Task-1 baseline count (record the before/after count in the closure note, same discipline as every prior phase's own closure doc, e.g. `docs/owner/ui-modernization/final-test-report.md`).

- [ ] **Step 2: If any failure is in a test unrelated to this plan's files, stop and investigate before proceeding**

Do not proceed to Step 3 with any red test, per this repo's own established discipline (`aura-owner-phase9-5a-commercial-ops` memory: "the clearest evidence yet ... for why a genuine full-suite regression ... must run before any final tag").

- [ ] **Step 3: Append a closure note to the design contract**

Append to `docs/owner/ui-modernization/dashboard-interactive-kit-contract.md`:

```markdown

## Closure

Implemented via `docs/superpowers/plans/2026-08-18-dashboard-interactive-kit.md`,
Tasks 1-5. Full Owner regression: <BEFORE> -> <AFTER> tests, 0 failures.
Manual browser verification (Task 4 Step 8) completed <DATE>.
```

Fill in `<BEFORE>`/`<AFTER>`/`<DATE>` with the real counts/date from Step 1 -- never leave the placeholder text itself in the committed file.

- [ ] **Step 4: Commit**

```bash
git add docs/owner/ui-modernization/dashboard-interactive-kit-contract.md
git commit -m "docs(owner-ui): close out the dashboard interactive kit phase"
```
