# Retail Standalone UI Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `aura-fullsuits/products/retail` render a real, working UI on its own — onboarding/login + sidebar + all 9 existing sections — by porting and trimming the legacy monolith's shell (`subsystem-core.js`) into the standalone product, closing the 404-on-`/` bug from the packaged installer.

**Architecture:** A new `GET /` route serves a new, minimal `index.html`. That page loads a trimmed copy of the monolith's `subsystem-core.js` (renamed `app-shell.js`) plus already-complete, unmodified files (`subsystem-retail.js`, `i18n.js`, `import-wizard.js`, locale JSON). The trim removes every code path tied to another subsystem or to the multi-product chooser menu — this product only ever has one system (`retail`), so it launches straight into it after login.

**Tech Stack:** Flask (existing backend, no framework changes), vanilla JS (matches existing `subsystem-retail.js`), no build step — files are served as static assets exactly as today.

## Global Constraints

- No changes to `subsystem-retail.js`'s business logic (per spec).
- No changes to the legacy monolith (`AuraEnterprise\AuraEnterprise`) — read-only.
- No Import Wizard *nav entry* and no Backup/Restore UI this pass (per spec) — see Task 2's note on why `import-wizard.js` is still loaded as a dependency despite this.
- EN/AR + RTL must work in the shell chrome (setup modal, login modal, sidebar), not just inside `subsystem-retail.js`'s own sections.
- Every source line referenced below was read directly from the actual files during planning — line numbers are exact as of this writing. If any referenced file has changed since, stop and re-read it before applying that step.

---

### Task 1: Copy static, unmodified dependencies into `products/retail/frontend`

**Files:**
- Create: `products/retail/frontend/icons.js` (copy of `AuraEnterprise\AuraEnterprise\static\js\aura-icons.js`)
- Create: `products/retail/frontend/css/main.css` (copy of `AuraEnterprise\AuraEnterprise\static\css\main.css`)
- Create: `products/retail/frontend/css/rtl.css` (copy of `AuraEnterprise\AuraEnterprise\static\css\rtl.css`)

**Interfaces:**
- Produces: `window.AuraIcons.render(name, size)` (used by `app-shell.js` and `subsystem-retail.js` for icons), plus all CSS classes used by the shell markup built in Task 3 (`.sub-sidebar`, `.sub-nav-item`, `.sub-header`, `.sub-content`, `.sub-kpi-*`, theme/RTL variables, etc.)

**Note on CSS:** the design spec called for trimming `main.css`/`rtl.css` of other-subsystem-only rules. Decision made during planning: copy both **verbatim, unmodified** instead. Reason: `main.css` has ~65 lines referencing other subsystems' class prefixes (`.crm-`, `.accounting-`, etc.) scattered through 3415 lines — none of retail's DOM ever uses those classes, so they're simply dead weight, not a functional risk. Hand-trimming a 3415-line stylesheet risks deleting a rule that's actually shared/generic by mistake, which would visibly break retail's own UI — a worse outcome than a few extra KB of unused CSS. This satisfies the spec's actual goal (no functional coupling to other subsystems) at much lower risk.

- [ ] **Step 1: Copy the three files**

```bash
mkdir -p "products/retail/frontend/css"
cp "../AuraEnterprise/AuraEnterprise/static/js/aura-icons.js" "products/retail/frontend/icons.js"
cp "../AuraEnterprise/AuraEnterprise/static/css/main.css" "products/retail/frontend/css/main.css"
cp "../AuraEnterprise/AuraEnterprise/static/css/rtl.css" "products/retail/frontend/css/rtl.css"
```

(Run from the `aura-fullsuits` repo root; adjust the relative path to `AuraEnterprise\AuraEnterprise` if your checkout layout differs — both repos sit side by side under `c:\Users\Dell\Desktop\AuraEnterprise\`.)

- [ ] **Step 2: Verify**

```bash
wc -l products/retail/frontend/icons.js products/retail/frontend/css/main.css products/retail/frontend/css/rtl.css
```

Expected: 3 files, roughly matching the source line counts (icons.js is a dense one-liner-per-icon file — check it's non-empty and starts with `window.AuraIcons = (function(){`).

- [ ] **Step 3: Commit**

```bash
git add products/retail/frontend/icons.js products/retail/frontend/css/
git commit -m "feat(retail): add icons.js + main.css/rtl.css for standalone shell"
```

---

### Task 2: Port and trim `app-shell.js` from the monolith's `subsystem-core.js`

**Files:**
- Create: `products/retail/frontend/app-shell.js` (trimmed copy of `AuraEnterprise\AuraEnterprise\static\js\subsystem-core.js`, 2031 source lines)

**Interfaces:**
- Consumes: `window.AuraIcons` (Task 1), `window.AuraI18n` / `t()` global (already-existing `products/retail/frontend/i18n.js`, unchanged), backend routes `/api/auth/session`, `/api/onboarding/status`, `/api/onboarding/create-admin`, `/api/onboarding/complete`, `/api/auth/login`, `/api/auth/logout` (all already implemented in `commercial_runtime/identity/`, verified field-name-compatible during planning — no backend changes needed).
- Produces: `window.SubsystemApp` with `.init()`, `.launch(systemId, sectionId)`, `.exit()`, `.logout()`, `._navigate(sectionId)`, `.apiGet/apiPost/apiDelete`, `.formatCurrency/formatDate/badge/kpiCard/showToast` — this is the exact global `subsystem-retail.js` already expects (it calls `window.SubsystemApp?.checkAuthAndSetup` on 401 and renders into `#sub-content`, per `products/retail/frontend/subsystem-retail.js` — unchanged, not touched by this task).

This file is a **trim**, not a rewrite: copy the source verbatim, then remove exactly the blocks below (all line numbers are against the **original 2031-line source**). Do the line-range removals first, in the exact order given (highest starting line first) — each removal only shifts line numbers *below* itself, and every range below it in this list is still untouched at that point, so processing top-to-bottom in this list is always safe.

- [ ] **Step 1: Copy the source file verbatim**

```bash
cp "../AuraEnterprise/AuraEnterprise/static/js/subsystem-core.js" "products/retail/frontend/app-shell.js"
wc -l products/retail/frontend/app-shell.js
```

Expected: 2031 lines.

- [ ] **Step 2: Remove six dead/other-subsystem blocks by line range**

Each range below was read in full during planning and confirmed to be a self-contained block (starts right after the prior block's closer, ends right before the next kept line):

| Range (1-indexed, inclusive) | What it is | Why it's removed |
|---|---|---|
| 1695–2000 | `EventSystem` global (cross-subsystem activity timeline), `window._seedDemoData`/`window._wipeDemoData` (monolith's public demo portal), `StudioOverlay` (Dashboard Studio) | None of these have any backend route in `products/retail/backend` (`/api/events/`, `/api/demo/seed-all`, `/api/demo/wipe-all` don't exist there) and none are in the 9-section nav scope |
| 1298–1541 | `renderEmptyDashboard` + widget system (`loadDemoData`, `clearDemoData`, `changeChartType`, `downloadChart`, `showWidgetModal`, `removeWidget`, `triggerUpload`) | Dead code for retail specifically: `_navigate()` only calls `renderEmptyDashboard` when `this.active === 'dashboard'`, but `this.active` holds the *systemId* and will only ever be `'retail'` in this build (see Task 2 Step 3b) |
| 820–860 | `_showSystemMenu()` (in-app multi-subsystem chooser) | No longer reachable after Step 3c rewrites `exit()` to not call it |
| 552–762 | The "EIP Menu Shell" builder inside `init()` — active-modules fetch/filter, the full chooser grid HTML/CSS template literal, and the old conditional auto-launch | Replaced with an unconditional direct launch in Step 3a — a single-system build never needs a chooser |
| 478–504 | `clinic` and `events` entries inside the `systems` object | Not retail |
| 347–460 | `accounting`, `hr`, `inventory`, `crm`, `pm`, `marketing` entries inside the `systems` object | Not retail |
| 204–315 | `SubAI` global (per-subsystem AI chat panel) | Calls `/api/sub/${subsystem}/ai/chat`, which doesn't exist in `products/retail/backend` — no AI blueprint is registered there |

Apply with this Python snippet (run from the repo root; uses the `aura-fullsuits` venv's Python):

```bash
.venv/Scripts/python.exe - <<'EOF'
path = "products/retail/frontend/app-shell.js"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()
before = len(lines)
# Descending start-line order — each deletion leaves everything above it untouched.
for start, end in [(1695, 2000), (1298, 1541), (820, 860), (552, 762), (478, 504), (347, 460), (204, 315)]:
    del lines[start - 1:end]
with open(path, "w", encoding="utf-8", newline="") as f:
    f.writelines(lines)
print(f"{before} -> {len(lines)} lines")
EOF
```

Expected output: `2031 -> 976 lines` (2031 minus 306+244+41+211+27+114+112 = 1055 removed lines).

- [ ] **Step 3: Verify the six removals landed cleanly**

```bash
grep -n "EventSystem\|StudioOverlay\|_seedDemoData\|renderEmptyDashboard\|_showSystemMenu\|SubAI\|accounting:\|hr:\|inventory:\|crm:\|pm:\|marketing:\|clinic:\|events:" products/retail/frontend/app-shell.js
```

Expected: **no matches**. If anything prints, a range boundary was off by a line — re-copy from source (Step 1) and check the boundary against the source file before retrying.

- [ ] **Step 3a: Insert the direct-launch call at the end of `init()`'s auth check**

After the line-range removals, `init()`'s auth-check block ends right where the deleted chooser code used to begin, with nothing closing the function. Find this exact text (unique in the file — it's the only `catch` block with this comment) and append the launch call plus the method's closing brace:

Old:
```javascript
        this.currentUser = sess.user || {};
        this.role        = (sess.user && sess.user.role) || '';
        this.clinicRole  = (sess.user && sess.user.clinic_role) || '';
      } catch(e) {
        // Can't reach server — proceed and let individual API calls handle 401s
      }
    }
```

New:
```javascript
        this.currentUser = sess.user || {};
        this.role        = (sess.user && sess.user.role) || '';
        this.clinicRole  = (sess.user && sess.user.clinic_role) || '';
      } catch(e) {
        // Can't reach server — proceed and let individual API calls handle 401s
      }
    }

    // Single-product build: there is only ever one system, so skip the
    // multi-subsystem chooser entirely and launch straight into it.
    this.launch('retail', 'dashboard');
  },
```

- [ ] **Step 3b: Trim `systems` to just `retail`, and trim `_RENDERERS` in `_navigate()` to match**

The `systems` object (after Step 2's removals) should now read exactly:

```javascript
  systems: {
    retail: {
      name: 'Retail & POS',
      icon: '🛍️',
      accent: '#f43f5e',
      accentRgb: '244,63,94',
      nav: [
        { id: 'dashboard',  label: 'Dashboard',        icon: '🏠' },
        { id: 'pos',        label: 'Point of Sale',     icon: '🛒' },
        { id: 'products',   label: 'Products',          icon: '📦' },
        { id: 'customers',  label: 'Customers',         icon: '👥' },
        { id: 'suppliers',  label: 'Suppliers',         icon: '🏭' },
        { id: 'purchases',  label: 'Purchase Orders',   icon: '📋' },
        { id: 'returns',    label: 'Returns',           icon: '↩️' },
        { id: 'reports',    label: 'Reports',           icon: '📊' },
        { id: 'scanner',    label: 'Barcode Scanner',   icon: '🔦', desktopOnly: true },
      ]
    },
  },
```

Confirm this with `grep -n -A20 "systems: {" products/retail/frontend/app-shell.js` — if the structure above doesn't match exactly (e.g. a stray comma or bracket), fix it by hand now before continuing; a malformed object literal breaks the whole file.

Then find `_RENDERERS` inside `_navigate()` and trim it from 8 entries to 1:

Old:
```javascript
        const _RENDERERS = {
          accounting: 'AccountingSystem',
          hr:         'HRSystem',
          inventory:  'InventorySystem',
          crm:        'CRMSystem',
          pm:         'PMSystem',
          marketing:  'MarketingSystem',
          retail:     'RetailSystem',
          clinic:     'ClinicSystem',
        };
```

New:
```javascript
        const _RENDERERS = {
          retail: 'RetailSystem',
        };
```

- [ ] **Step 3c: Simplify `exit()` — no chooser to return to**

Old:
```javascript
  exit() {
    this.active = null;
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.documentElement.style.removeProperty('--sub-accent');
    document.documentElement.style.removeProperty('--sub-accent-rgb');
    SubAI.close();

    // Clear hash for subsystem
    if (window.AuraRouter) {
      const mode = window.isDemoMode ? 'demo' : 'real';
      window.AuraRouter.save(mode, null, null);
    }

    // Route back to a SYSTEM CHOOSER (keeps the user signed in) — never dump
    // them to the sign-in landing on an accidental "Back to Menu".
    const subsMenu = document.getElementById('page-subs-menu');
    if (window.isDemoMode && subsMenu) {
        subsMenu.classList.add('active');   // demo keeps its existing chooser
    } else {
        this._showSystemMenu();             // standalone: in-app chooser, no re-login
    }
  },
```

New:
```javascript
  exit() {
    // Single-product build: "exit" (clicking the logo) just returns to the
    // retail dashboard — there is no other subsystem to choose between.
    this.launch('retail', 'dashboard');
  },
```

(This also removes the only remaining call to `SubAI.close()`, so the dangling `window.SubAI = SubAI;` export line — from Step 3d — is the last SubAI reference left to clean up.)

- [ ] **Step 3d: Drop the dangling `window.SubAI` export**

Find `window.SubAI = SubAI;` (a single line, right after `window.SubsystemApp = SubsystemApp;`) and delete just that line — `SubAI` no longer exists after Step 2's removal of lines 204–315.

- [ ] **Step 3e: Remove the Dashboard-Studio, Import-Data, and Back-to-Menu buttons from `_renderShell()`**

These three buttons call globals this build doesn't load: `StudioOverlay.open()` (deleted in Step 2), `ImportWizard.openForSystem()` (see Task 3's note — `import-wizard.js` *is* loaded, but there's no dedicated Settings-level entry point for it this pass, so this specific header shortcut is out of scope), and `SubsystemApp.exit()` via a "Back to Menu" label that no longer makes sense with only one system.

Old (inside `_renderShell()`'s sidebar-bottom block):
```javascript
        <div class="sub-sidebar-bottom">
          ${hasAI ? `
          <button class="sub-ai-btn" onclick="SubAI.open('${systemId}')">
            <span>🤖</span> <span>${t('AI Assistant')}</span>
            <span class="ai-pulse"></span>
          </button>
          ` : ''}
          <button class="sub-exit-btn" onclick="StudioOverlay.open()" style="background:linear-gradient(135deg,rgba(99,102,241,0.2),rgba(56,189,248,0.1));border-color:rgba(99,102,241,0.3);color:#818cf8;margin-bottom:4px;">
            <span>📊</span> <span>${t('Dashboard Studio')}</span>
          </button>
          <button class="sub-exit-btn" onclick="SubsystemApp.exit()">
            <span>←</span> <span>${t('Back to Menu')}</span>
          </button>
          <button class="sub-exit-btn" onclick="SubsystemApp.logout()" style="background:rgba(239,68,68,0.1);border-color:rgba(239,68,68,0.25);color:#f87171;margin-top:4px;">
            <span>⏻</span> <span>${t('Log Out')}</span>
          </button>
        </div>
```

New:
```javascript
        <div class="sub-sidebar-bottom">
          ${hasAI ? `
          <button class="sub-ai-btn" onclick="SubAI.open('${systemId}')">
            <span>🤖</span> <span>${t('AI Assistant')}</span>
            <span class="ai-pulse"></span>
          </button>
          ` : ''}
          <button class="sub-exit-btn" onclick="SubsystemApp.logout()" style="background:rgba(239,68,68,0.1);border-color:rgba(239,68,68,0.25);color:#f87171;margin-top:4px;">
            <span>⏻</span> <span>${t('Log Out')}</span>
          </button>
        </div>
```

(The `hasAI` branch is left in place — it already evaluates to `false` for this build, since `this.activeModules` is never fetched anymore and stays `undefined`, and `undefined && (...)` is falsy — so the AI button never renders. Leaving the guarded branch in is lower-risk than also hunting down and removing `hasAI`'s own declaration line.)

And the Import-Data header button:

Old:
```javascript
            <button class="sub-import-btn" onclick="ImportWizard.openForSystem('${systemId}')" title="Import Excel, CSV or JSON data">
              <svg width="15" height="15" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path d="M10 2.5a.75.75 0 0 1 .75.75v8.19l2.47-2.47a.75.75 0 1 1 1.06 1.06l-3.75 3.75a.75.75 0 0 1-1.06 0L5.72 9.03a.75.75 0 0 1 1.06-1.06l2.47 2.47V3.25A.75.75 0 0 1 10 2.5zM4 15.25a.75.75 0 0 1 .75-.75h10.5a.75.75 0 0 1 0 1.5H4.75a.75.75 0 0 1-.75-.75z"/>
              </svg>
              <span>${t('Import Data')}</span>
            </button>
```

New: (delete the whole button — nothing replaces it)

- [ ] **Step 4: Final structural check**

```bash
node --check products/retail/frontend/app-shell.js
```

Expected: no output (syntax OK). This project already uses `node --check` for JS validation (see `dev-environment` conventions) — if Node isn't on PATH in this shell, refresh it per the project's documented PATH-refresh command first.

- [ ] **Step 5: Commit**

```bash
git add products/retail/frontend/app-shell.js
git commit -m "feat(retail): port trimmed standalone shell from monolith subsystem-core.js"
```

---

### Task 3: Build `index.html` and wire the `/` route

**Files:**
- Create: `products/retail/frontend/index.html`
- Modify: `products/retail/backend/app.py` (add one route)

**Interfaces:**
- Consumes: `app-shell.js` (Task 2), `subsystem-retail.js`, `i18n.js`, `import-wizard.js`, `icons.js` (Task 1), `css/main.css`, `css/rtl.css` — all served today from Flask's existing `static_url_path='/static'` mapped to `PRODUCT_DIR / 'frontend'` (`products/retail/backend/app.py:46`).
- Produces: `GET /` → 200 with this HTML, fixing the installer's 404.

**Note on `import-wizard.js`:** the spec scoped this pass to "core 9 sections only, no Import Wizard UI." That holds for *navigation* — there's no dedicated Import screen or Settings entry point added here. But `subsystem-retail.js` (left unmodified, per constraints) already has three working "⬆ Import" buttons baked into its Products/Customers/Suppliers sections (`products/retail/frontend/subsystem-retail.js:949,1165,1319`), each calling `ImportWizard.open(...)`. If `import-wizard.js` isn't loaded, those three pre-existing buttons throw `ImportWizard is not defined` the moment anyone clicks them — a real, clickable-during-a-demo failure. `import-wizard.js` already exists complete in `products/retail/frontend/` (791 lines, untouched) — loading it costs nothing and isn't new functionality, it just keeps existing buttons from crashing. Load it as a plain dependency.

- [ ] **Step 1: Write `products/retail/frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Aura Retail</title>
  <script>
    // Apply the saved theme before first paint to avoid a flash (default: light).
    try { document.documentElement.setAttribute('data-theme', localStorage.getItem('aura_theme') || 'light'); } catch(e) {}
    // This build only ever serves Retail — the shell skips the multi-subsystem
    // chooser entirely and launches straight into it (see app-shell.js).
    window.IS_STANDALONE = true;
  </script>
  <link rel="stylesheet" href="/static/css/main.css">
  <link rel="stylesheet" href="/static/css/rtl.css">
  <style>
    body, html { margin: 0; padding: 0; width: 100%; height: 100%; background: #020617; color: white; }
  </style>
</head>
<body>
  <div id="app"></div>

  <script src="/static/icons.js"></script>
  <script src="/static/i18n.js"></script>
  <script src="/static/import-wizard.js"></script>
  <script src="/static/subsystem-retail.js"></script>
  <script src="/static/app-shell.js"></script>
  <script>
    AuraI18n.load().then(() => SubsystemApp.init());
  </script>
</body>
</html>
```

- [ ] **Step 2: Add the `/` route in `products/retail/backend/app.py`**

Read the file first to find the current location of the two existing routes (`_health` and `_version`, both immediately before the blueprint-registration block) — insert the new route right after `_version()` and before the `from commercial_runtime.identity.auth_routes import auth_bp` line, matching the file's existing style.

Old (the anchor to insert after — exact text from the current file):
```python
@app.route('/api/version', methods=['GET'])
def _version():
    """Release metadata for the About screen / release-manifest tooling
    (Wave 1B). Separate from /api/health on purpose -- that route's
    contract is frozen for the launcher's readiness probe (see its
    docstring); this one is free to grow."""
    from config import SCHEMA_VERSION, CALCULATION_VERSION, PRODUCT_CODE
    return jsonify({
        'product_code': PRODUCT_CODE,
        'product_name': 'Aura Retail',
        'app_version': APP_VERSION,
        'schema_version': SCHEMA_VERSION,
        'calculation_version': CALCULATION_VERSION,
    }), 200


from commercial_runtime.identity.auth_routes import auth_bp
```

New:
```python
@app.route('/api/version', methods=['GET'])
def _version():
    """Release metadata for the About screen / release-manifest tooling
    (Wave 1B). Separate from /api/health on purpose -- that route's
    contract is frozen for the launcher's readiness probe (see its
    docstring); this one is free to grow."""
    from config import SCHEMA_VERSION, CALCULATION_VERSION, PRODUCT_CODE
    return jsonify({
        'product_code': PRODUCT_CODE,
        'product_name': 'Aura Retail',
        'app_version': APP_VERSION,
        'schema_version': SCHEMA_VERSION,
        'calculation_version': CALCULATION_VERSION,
    }), 200


@app.route('/', methods=['GET'])
def _index():
    """Serves the standalone shell (frontend/index.html), which loads
    app-shell.js + subsystem-retail.js and drives onboarding/login/nav.
    Was previously unrouted entirely -- the packaged launcher opened this
    exact URL and 404'd (see docs/superpowers/specs/2026-08-06-retail-standalone-ui-shell-design.md)."""
    return app.send_static_file('index.html')


from commercial_runtime.identity.auth_routes import auth_bp
```

(`app.send_static_file` works here because `static_folder` is already `PRODUCT_DIR / 'frontend'` — `index.html` from Task 3 Step 1 lands directly in that folder, no new path configuration needed.)

- [ ] **Step 3: Commit**

```bash
git add products/retail/frontend/index.html products/retail/backend/app.py
git commit -m "feat(retail): serve index.html shell at / (fixes installer 404)"
```

---

### Task 4: Verify from source, then rebuild and verify the packaged installer

**Files:** none created/modified — verification only.

- [ ] **Step 1: Run from source with a clean app-data directory**

```bash
rm -rf "$LOCALAPPDATA/AuraRetail"   # or the OS equivalent — this build's data dir, safe to wipe for a fresh-install test
cd products/retail/backend
../../../.venv/Scripts/python.exe app.py
```

- [ ] **Step 2: Manual browser pass against `http://127.0.0.1:5000`**

Check off each as verified, in order:
- [ ] Page loads (no 404, no blank screen, no console errors on load)
- [ ] Setup modal appears (fresh DB, no admin yet)
- [ ] Create-admin form submits successfully → lands in the app (not stuck on the modal, no error toast)
- [ ] Sidebar shows exactly: Dashboard, Point of Sale, Products, Customers, Suppliers, Purchase Orders, Returns, Reports, Barcode Scanner — nothing else
- [ ] Each of the 9 sections loads without a console error when clicked
- [ ] Logging out and back in works (`/api/auth/login` path, not just the setup path)
- [ ] Language toggle (`EN | ع` button) switches to Arabic, layout flips RTL, sidebar labels translate
- [ ] Clicking the logo/home area doesn't error (exercises the trimmed `exit()`)
- [ ] The three "⬆ Import" buttons (Products/Customers/Suppliers) open the Import Wizard modal instead of throwing a console error

- [ ] **Step 3: Rebuild the packaged installer**

```bash
.venv/Scripts/pyinstaller products/retail/packaging/aura_retail.spec --noconfirm
"/c/Users/Dell/AppData/Local/Programs/Inno Setup 6/ISCC.exe" products/retail/packaging/aura_retail_setup.iss
```

- [ ] **Step 4: Install and repeat Step 2's full checklist against the installed app**

Run the freshly-built `dist/installers/AuraRetail-Setup-<version>.exe`, launch the installed app, and re-verify every item in Step 2 against the real packaged `.exe` — this is what actually closes out the original bug report, not the source run alone.

- [ ] **Step 5: Record the result**

If every checklist item passes, this plan is done — no commit needed for this task (verification-only). If anything fails, open a new task here describing exactly what broke and where, rather than patching ad hoc — the next session needs that context.

---

## Residual gaps (explicitly out of scope, not silently dropped)

- No automated frontend tests exist for this code today, ported or new — this plan doesn't add any either (documented in the design spec as a known gap).
- Import Wizard has no dedicated nav entry / Settings screen (only the three pre-existing inline buttons work).
- No Backup/Restore UI (backend blueprint exists, unused by this shell).
- `canClinic()`, `apiGet/apiPost/apiDelete/wipeDemoData/kpiCard/renderChart/showToast` on `SubsystemApp`, and the `hasAI` sidebar branch are all kept but unused by `subsystem-retail.js` today — left in as harmless, low-risk-to-remove generic utilities rather than hunted down individually.
