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


def test_donut_chart_svg_has_no_css_rotation_stacked_on_the_dashoffset_math():
    # The stroke-dashoffset math in the donut macro already rotates the
    # start point to 12 o'clock on its own (see _charts.html's "25 - ns.offset").
    # A CSS transform: rotate() on .aura-donut-chart__svg would double-rotate
    # it -- this guards against that regression reappearing.
    import pathlib

    css_path = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "charts.css"
    css = css_path.read_text(encoding="utf-8")
    svg_rule_start = css.index(".aura-donut-chart__svg")
    svg_rule_end = css.index("}", svg_rule_start)
    svg_rule = css[svg_rule_start:svg_rule_end]
    assert "rotate(" not in svg_rule
