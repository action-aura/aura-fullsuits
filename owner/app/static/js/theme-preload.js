/* Aura Owner -- pre-paint shell-preference restore.
 *
 * Must run synchronously, before tokens.css is parsed/applied, so there
 * is never a flash of the wrong theme or a layout jump from the sidebar
 * changing width after first paint. Loaded via a normal (non-async,
 * non-defer) <script src> in layout/base.html's <head>, before the
 * stylesheet <link>s -- an external same-origin file, not an inline
 * <script>, because the app's CSP is script-src 'self' with no
 * 'unsafe-inline' (owner/app/security/headers.py) and inline scripts
 * are correctly blocked. A blocking external <script src> at this
 * position gives the same synchronous-before-first-paint guarantee an
 * inline script would have, at the cost of one extra same-origin
 * request for a file the browser will cache.
 *
 * Reads three presentation-only, non-security localStorage keys:
 * "aura-owner-theme" ("light"|"dark"|"system", default "system" --
 * leaving data-theme unset here falls through to tokens.css's
 * prefers-color-scheme media query), "aura-owner-sidebar"
 * ("collapsed"|"expanded", default "expanded"), and
 * "aura-owner-chart-palette" ("brand"|"vivid"|"cb-safe", default "brand"
 * -- leaving data-chart-palette unset falls through to tokens.css's bare
 * :root chart-palette values, which ARE the brand ramp). See theme.js,
 * sidebar.js and chart-palette.js for the interactive toggle controls
 * that write these keys.
 */
(function () {
  try {
    var theme = window.localStorage.getItem("aura-owner-theme");
    if (theme === "light" || theme === "dark") {
      document.documentElement.setAttribute("data-theme", theme);
    }
    if (window.localStorage.getItem("aura-owner-sidebar") === "collapsed") {
      document.documentElement.setAttribute("data-sidebar", "collapsed");
    }
    var chartPalette = window.localStorage.getItem("aura-owner-chart-palette");
    if (chartPalette === "vivid" || chartPalette === "cb-safe") {
      document.documentElement.setAttribute("data-chart-palette", chartPalette);
    }
  } catch (e) { /* localStorage unavailable (private mode, disabled storage) */ }
})();
