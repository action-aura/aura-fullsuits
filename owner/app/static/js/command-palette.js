/* Aura Owner -- Command Palette (Ctrl+K / Cmd+K), Stage C.
 *
 * Vanilla JS, external file (CSP: script-src 'self', same convention as
 * sidebar.js/theme.js/product-tour.js -- no inline script, no framework).
 *
 * Two real data sources, never invented client-side:
 *  1. #aura-command-palette-data -- a small, fixed-size, already
 *     permission-filtered JSON island (app/command_palette/service.py::
 *     get_static_commands()), read once at open time. This is NOT "the
 *     dataset" -- it's the same order of magnitude as the sidebar's own
 *     real link count, filtered client-side by simple substring match.
 *  2. The real, bounded, permission-scoped search endpoint
 *     (data-search-url), fetched per keystroke (debounced, cancellable)
 *     for anything 2+ characters. Never a full-table client-side search.
 *
 * Recent commands: localStorage["aura-owner-command-palette-recent"]
 * holds only {label, url} pairs for destinations the user actually
 * navigated to -- never a search query string, never any entity's real
 * field values beyond the label already rendered on screen. Capped at 5.
 * Presentation-only client preference, not a security or business record
 * (same status as sidebar.js's own collapsed/expanded preference).
 *
 * No console.log/console.error of search terms, results, or fetch
 * errors anywhere in this file -- a failed/aborted request just falls
 * back to a real empty/loading state, nothing is dumped to the console.
 */
(function () {
  var RECENT_KEY = "aura-owner-command-palette-recent";
  var RECENT_LIMIT = 5;
  var DEBOUNCE_MS = 220;

  document.addEventListener("DOMContentLoaded", function () {
    var root = document.getElementById("aura-command-palette-root");
    var trigger = document.getElementById("aura-command-palette-trigger");
    var template = document.getElementById("aura-command-palette-template");
    if (!root || !template) return;

    var searchUrl = root.dataset.searchUrl;
    var minQueryLength = parseInt(root.dataset.minQueryLength, 10) || 2;
    var labelRecent = root.dataset.labelRecent || "Recent";
    var labelSearching = root.dataset.labelSearching || "Searching...";
    var emptyTemplate = root.dataset.emptyTemplate || "No results for \"__QUERY__\"";

    var dataEl = document.getElementById("aura-command-palette-data");
    var paletteData = { navigate: [], create: [] };
    try {
      paletteData = JSON.parse((dataEl && dataEl.textContent) || "{}");
    } catch (e) {
      paletteData = { navigate: [], create: [] };
    }
    var staticCommands = (paletteData.navigate || []).concat(paletteData.create || []);

    var overlay = null;
    var input = null;
    var listbox = null;
    var statusEl = null;
    var emptyEl = null;
    var lastFocused = null;
    var currentOptions = [];
    var activeIndex = -1;
    var debounceTimer = null;
    var activeAbortController = null;

    function getRecent() {
      try {
        var raw = window.localStorage.getItem(RECENT_KEY);
        var parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
      } catch (e) {
        return [];
      }
    }

    function pushRecent(label, url) {
      try {
        var next = getRecent().filter(function (entry) { return entry.url !== url; });
        next.unshift({ label: label, url: url });
        next = next.slice(0, RECENT_LIMIT);
        window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
      } catch (e) { /* localStorage unavailable -- recent list simply stays empty */ }
    }

    function setStatus(text) {
      if (statusEl) statusEl.textContent = text || "";
    }

    function clearListbox() {
      if (listbox) listbox.innerHTML = "";
      currentOptions = [];
      activeIndex = -1;
      if (input) input.setAttribute("aria-activedescendant", "");
    }

    function setActive(index) {
      if (activeIndex >= 0 && currentOptions[activeIndex]) {
        currentOptions[activeIndex].el.classList.remove("is-active");
        currentOptions[activeIndex].el.setAttribute("aria-selected", "false");
      }
      activeIndex = index;
      if (activeIndex >= 0 && currentOptions[activeIndex]) {
        var opt = currentOptions[activeIndex];
        opt.el.classList.add("is-active");
        opt.el.setAttribute("aria-selected", "true");
        input.setAttribute("aria-activedescendant", opt.id);
        if (opt.el.scrollIntoView) opt.el.scrollIntoView({ block: "nearest" });
      } else if (input) {
        input.setAttribute("aria-activedescendant", "");
      }
    }

    function moveActive(delta) {
      if (!currentOptions.length) return;
      var next = activeIndex + delta;
      if (next < 0) next = currentOptions.length - 1;
      if (next >= currentOptions.length) next = 0;
      setActive(next);
    }

    function activate(option) {
      pushRecent(option.label, option.url);
      window.location.href = option.url;
    }

    function renderSections(sections) {
      clearListbox();
      var idx = 0;
      sections.forEach(function (section) {
        if (!section.items.length) return;
        var groupLi = document.createElement("li");
        groupLi.className = "aura-cp-group-label";
        groupLi.setAttribute("role", "presentation");
        groupLi.textContent = section.label;
        listbox.appendChild(groupLi);

        section.items.forEach(function (item) {
          var optId = "aura-cp-opt-" + idx;
          var li = document.createElement("li");
          li.id = optId;
          li.className = "aura-cp-option";
          li.setAttribute("role", "option");
          li.setAttribute("aria-selected", "false");

          var labelSpan = document.createElement("span");
          labelSpan.className = "aura-cp-option-label";
          labelSpan.textContent = item.label;
          li.appendChild(labelSpan);

          if (item.context) {
            var ctxSpan = document.createElement("span");
            ctxSpan.className = "aura-cp-option-context";
            ctxSpan.textContent = item.context;
            li.appendChild(ctxSpan);
          }

          var record = { id: optId, el: li, label: item.label, url: item.url };
          li.addEventListener("mousemove", (function (record) {
            return function () { setActive(currentOptions.indexOf(record)); };
          })(record));
          li.addEventListener("click", (function (record) {
            return function () { activate(record); };
          })(record));

          listbox.appendChild(li);
          currentOptions.push(record);
          idx += 1;
        });
      });
      if (currentOptions.length > 0) setActive(0);
    }

    function staticMatches(term) {
      var lower = term.toLowerCase();
      return staticCommands.filter(function (command) {
        return command.label.toLowerCase().indexOf(lower) !== -1;
      });
    }

    function sectionsFromCommandList(items) {
      var byGroup = {};
      var order = [];
      items.forEach(function (item) {
        if (!byGroup[item.group]) { byGroup[item.group] = []; order.push(item.group); }
        byGroup[item.group].push({ label: item.label, context: "", url: item.url });
      });
      return order.map(function (group) { return { label: group, items: byGroup[group] }; });
    }

    function sectionsFromResults(results) {
      var byType = {};
      var order = [];
      (results || []).forEach(function (result) {
        if (!byType[result.type_label]) { byType[result.type_label] = []; order.push(result.type_label); }
        byType[result.type_label].push({ label: result.label, context: result.context, url: result.url });
      });
      return order.map(function (group) { return { label: group, items: byType[group] }; });
    }

    function render(rawTerm, entityResults) {
      var term = (rawTerm || "").trim();
      var sections = [];

      if (!term) {
        var recent = getRecent();
        if (recent.length) {
          sections.push({
            label: labelRecent,
            items: recent.map(function (entry) { return { label: entry.label, context: "", url: entry.url }; }),
          });
        }
        sections = sections.concat(sectionsFromCommandList(staticCommands));
      } else {
        sections = sections.concat(sectionsFromCommandList(staticMatches(term)));
        sections = sections.concat(sectionsFromResults(entityResults));
      }

      renderSections(sections);

      var totalCount = sections.reduce(function (sum, section) { return sum + section.items.length; }, 0);
      var showEmpty = term.length > 0 && totalCount === 0;
      if (emptyEl) {
        emptyEl.hidden = !showEmpty;
        if (showEmpty) {
          var message = emptyTemplate.replace("__QUERY__", term);
          emptyEl.textContent = message;
          setStatus(message);
        } else {
          setStatus("");
        }
      }
    }

    function runSearch(term) {
      var controller = (typeof window.AbortController !== "undefined") ? new window.AbortController() : null;
      activeAbortController = controller;
      var url = searchUrl + "?q=" + encodeURIComponent(term);
      window
        .fetch(url, { method: "GET", credentials: "same-origin", signal: controller ? controller.signal : undefined })
        .then(function (response) {
          if (!response.ok) throw new Error("search_request_failed");
          return response.json();
        })
        .then(function (payload) {
          // Stale-response guard: the user may have kept typing while this
          // request was in flight -- AbortController already cancels the
          // network request itself, this is belt-and-suspenders against a
          // race where an old response still resolves just after a newer
          // request started.
          if (!input || input.value.trim() !== term) return;
          render(term, payload.results || []);
        })
        .catch(function (error) {
          if (error && error.name === "AbortError") return; // superseded, not a real failure
          if (!input || input.value.trim() !== term) return;
          render(term, []);
        });
    }

    function scheduleSearch(rawTerm) {
      var term = (rawTerm || "").trim();
      if (debounceTimer) { window.clearTimeout(debounceTimer); debounceTimer = null; }
      if (activeAbortController) { activeAbortController.abort(); activeAbortController = null; }

      if (term.length < minQueryLength) {
        render(rawTerm, []);
        return;
      }
      render(rawTerm, []); // show static matches immediately while entity results load
      setStatus(labelSearching);
      debounceTimer = window.setTimeout(function () { runSearch(term); }, DEBOUNCE_MS);
    }

    function close() {
      if (!overlay) return;
      if (debounceTimer) { window.clearTimeout(debounceTimer); debounceTimer = null; }
      if (activeAbortController) { activeAbortController.abort(); activeAbortController = null; }
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      overlay = null;
      input = null;
      listbox = null;
      statusEl = null;
      emptyEl = null;
      currentOptions = [];
      activeIndex = -1;
      if (lastFocused && lastFocused.focus) lastFocused.focus();
    }

    function open() {
      if (overlay) return;
      lastFocused = document.activeElement;
      var fragment = template.content.cloneNode(true);
      document.body.appendChild(fragment);
      overlay = document.body.querySelector(".aura-cp-overlay:last-of-type");
      input = overlay.querySelector(".aura-cp-input");
      listbox = overlay.querySelector("[data-cp-listbox]");
      statusEl = overlay.querySelector("[data-cp-status]");
      emptyEl = overlay.querySelector("[data-cp-empty]");

      input.setAttribute("aria-expanded", "true");
      render("", []);

      overlay.addEventListener("click", function (event) {
        if (event.target === overlay) close();
      });
      overlay.querySelectorAll("[data-cp-action='close']").forEach(function (btn) {
        btn.addEventListener("click", close);
      });
      input.addEventListener("input", function () {
        scheduleSearch(input.value);
      });
      input.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          moveActive(1);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          moveActive(-1);
        } else if (event.key === "Enter") {
          event.preventDefault();
          if (activeIndex >= 0 && currentOptions[activeIndex]) activate(currentOptions[activeIndex]);
        } else if (event.key === "Escape") {
          event.preventDefault();
          close();
        } else if (event.key === "Tab") {
          // The dialog has exactly one focusable control (the input) --
          // keep focus inside rather than tabbing out to the page behind
          // the overlay while it's open.
          event.preventDefault();
        }
      });

      input.focus();
    }

    function toggle() {
      if (overlay) close();
      else open();
    }

    document.addEventListener("keydown", function (event) {
      var isModified = event.ctrlKey || event.metaKey;
      if (!isModified) return;
      var key = event.key ? event.key.toLowerCase() : "";
      if (key !== "k") return;
      event.preventDefault();
      toggle();
    });

    if (trigger) {
      trigger.addEventListener("click", toggle);
    }
  });
})();
