/**
 * Action Aura — Retail chart theme (P1a item #9, "Charts in the product's
 * own colours").
 *
 * WHAT THIS FIXES. Before this file existed, every Chart.js instance in
 * subsystem-retail.js either shipped Chart.js's own untouched built-in
 * defaults (grey Helvetica-ish ticks, a solid grid, an English-only legend)
 * or re-derived the same token reads locally at each of the two call sites
 * (the dashboard's hourly/payment charts, the Reports screen's four charts)
 * -- two private copies of "how do I turn a CSS token into a colour Chart.js
 * can consume" that could silently drift from each other. This is the one
 * place that logic lives now.
 *
 * WHAT THIS DOES NOT TOUCH. subsystem-retail.js's RETAIL_PAYMENT_METHOD_COLORS
 * map and the Reports/dashboard chart series colours (#2782ab, #a855f7,
 * #8b5cf6, #0c8b61) are CATEGORICAL identity colours -- "Revenue" is always
 * the same colour on every theme -- deliberately, per that file's own
 * extensive comment above RETAIL_PAYMENT_METHOD_COLORS and above rep-trend's
 * dataset. They are individually contrast-measured against all five themes'
 * card ground and pinned by retail_chart_series_contrast_test.js (a
 * pre-existing test this crew does not own). Swapping them for a single
 * shared token per theme would either collapse two series into one colour
 * (this file's own tick colour, "identical on every theme" defeats
 * categorical identity) or require re-deriving five contrast-safe values
 * per series by hand, which is exactly the work that test's own history
 * shows already happened once and is pinned so it cannot regress. This file
 * therefore themes everything AROUND a series colour -- the grid, the axis
 * labels, the legend, whether a legend renders at all -- and leaves series
 * identity colours to their existing, tested home.
 *
 * `window.AuraChartTheme`, mirroring the `window.AuraShare` shape share.js
 * exports for the same reason: a plain global, no build step, node- and
 * browser-testable, guarded for `typeof window` so it loads (and its pure
 * functions run) under a bare `node` test harness with no DOM at all.
 */
(function (root) {
  'use strict';

  // FUNCTIONS, not values cached at module-load time -- see share.js's own
  // identical comment on `hasWindow`, which documents the exact failure
  // this avoids: retail_chart_theme_test.js require()'s this file directly
  // and stubs `global.window`/`global.getComputedStyle` PER TEST, after
  // this module has already loaded once. A value cached at load time would
  // freeze on "no window" (a bare `node` process has none yet) and never
  // see any later test's stub.
  function hasWindow() { return typeof window !== 'undefined'; }
  function doc() { return hasWindow() ? window.document : null; }

  function cssToken(name, fallback) {
    var d = doc();
    if (!d || !d.documentElement || typeof getComputedStyle !== 'function') return fallback;
    try {
      var v = (getComputedStyle(d.documentElement).getPropertyValue(name) || '').trim();
      return v || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function moneyIn() { return cssToken('--text-money-positive', cssToken('--sub-accent', '#0c8b61')); }
  function moneyOut() { return cssToken('--text-money-negative', cssToken('--state-danger-text', '#ef4444')); }
  function accent() { return cssToken('--sub-accent', '#2782ab'); }
  function gridColor() { return cssToken('--border-hairline', 'rgba(148,163,184,0.18)'); }
  function tickColor() { return cssToken('--text-secondary', '#51607a'); }
  function legendTextColor() { return cssToken('--text-secondary', '#51607a'); }

  function translate(label) {
    try {
      if (hasWindow() && typeof window.t === 'function') return window.t(label);
      if (typeof t === 'function') return t(label); // eslint-disable-line no-undef
    } catch (e) { /* fall through */ }
    return label;
  }

  // FIX ROUND 3 (LOW-4): the decimal count is the SHOP CURRENCY's, not a
  // hard-coded 3. A dollar shop's axis read "12.000" beside a "$12.00" KPI --
  // the same two-precisions-for-one-figure bug axisMoney already fixed once
  // for fractional ticks. `decimals` defaults to 3 (JOD, the product's home
  // currency, DESIGN.md section 1: amounts are "three decimals (JD 12.345)"),
  // and anything that is not a whole number 0..6 falls back to that default
  // rather than reaching toFixed(), which throws RangeError above 100.
  function jdAxisLabel(value, decimals) {
    var n = Number(value);
    if (!isFinite(n)) return String(value);
    var dp = Number(decimals);
    if (decimals == null || !Number.isInteger(dp) || dp < 0 || dp > 6) dp = 3;
    return n.toFixed(dp);
  }

  function shouldShowLegend(datasetCount) {
    return (Number(datasetCount) || 0) > 1;
  }

  function legendOptions(datasetCount, extra) {
    var base = {
      display: shouldShowLegend(datasetCount),
      labels: { color: legendTextColor(), font: { size: 12 } },
    };
    if (!extra) return base;
    return Object.assign({}, base, extra, {
      labels: Object.assign({}, base.labels, extra.labels || {}),
    });
  }

  function apply() {
    if (!hasWindow() || !window.Chart) return;
    var Chart = window.Chart;
    Chart.defaults.color = tickColor();
    Chart.defaults.borderColor = gridColor();
    Chart.defaults.plugins = Chart.defaults.plugins || {};
    Chart.defaults.plugins.legend = Chart.defaults.plugins.legend || {};
    Chart.defaults.plugins.legend.labels = Object.assign(
      {}, Chart.defaults.plugins.legend.labels, { color: legendTextColor() }
    );
    Chart.defaults.scale = Chart.defaults.scale || {};
    Chart.defaults.scale.grid = Object.assign({}, Chart.defaults.scale.grid, {
      color: gridColor(),
      lineWidth: 1,
    });
    Chart.defaults.scale.ticks = Object.assign({}, Chart.defaults.scale.ticks, {
      color: tickColor(),
    });
  }

  var AuraChartTheme = {
    cssToken: cssToken,
    moneyIn: moneyIn,
    moneyOut: moneyOut,
    accent: accent,
    gridColor: gridColor,
    tickColor: tickColor,
    legendTextColor: legendTextColor,
    translate: translate,
    jdAxisLabel: jdAxisLabel,
    shouldShowLegend: shouldShowLegend,
    legendOptions: legendOptions,
    apply: apply,
  };

  if (hasWindow()) {
    window.AuraChartTheme = AuraChartTheme;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = AuraChartTheme;
  }
})(typeof window !== 'undefined' ? window : this);
