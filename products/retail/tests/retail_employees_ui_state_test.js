/**
 * retail_employees_ui_state_test.js — audit of employees.js against the two
 * UI-state defect shapes just fixed next door in subsystem-retail.js
 * (retail-hardware-viewports): the render-generation race, and the
 * hardcoded-wrong-label Save-button restore. See CLAUDE.md's "retail
 * schema"/launch-readiness notes and subsystem-retail.js's own "Render
 * generation guard" comment (above its Router) and _restoreSaveButton
 * comment (above _saveProduct) for the two originals this file's subject
 * was audited against.
 *
 * ── SHAPE 1: SAVE-BUTTON RESTORE — AUDITED, ALREADY CORRECT ─────────────────
 *
 * employees.js has four handlers that flip a button to a busy label before
 * an await: _submitInvite, _saveRole, _saveBranchScope, _savePin. All four
 * restore inside a `finally` block (not a duplicated explicit call on each
 * failure branch, unlike the six subsystem-retail.js handlers that had the
 * bug) — which already covers every non-success exit AND success itself
 * (harmlessly, since the modal is already removed by the time `finally`
 * runs on a successful save). Every one of the four restores to the LABEL
 * THE BUTTON WAS ACTUALLY RENDERED WITH:
 *   _saveRole         -> t('Save')                              (static)
 *   _saveBranchScope  -> t('Save')                              (static)
 *   _savePin          -> t('Save')                               (static)
 *   _submitInvite     -> reduced ? t('Add Cashier') : t('Create Invite')
 *                        (DYNAMIC — the exact shape that was wrong six times
 *                        next door, restoring to a hardcoded 'Save' that is
 *                        correct for an Edit button and wrong for every
 *                        "Add X" button)
 * No fix was needed here. The tests below PIN that correctness (both the
 * static and, more importantly, the dynamic case) so a future edit cannot
 * quietly reintroduce the hardcoded-label bug, and mutation-prove the pin
 * both ways: deleting the restore, and swapping the dynamic ternary for a
 * hardcoded 'Save' (the exact historical bug, reproduced on purpose).
 *
 * ── SHAPE 2: RENDER-GENERATION RACE — FOUND AND FIXED HERE ──────────────────
 *
 * render()'s branch-manager (delegated) arm awaits a roster probe (GET
 * /api/admin/employees) before deciding whether to show the reduced screen
 * or the refusal, and only THEN writes into `c` -- which is #sub-content,
 * the ONE persistent container every screen in this app renders into
 * (RetailSystem.render()'s router swaps its innerHTML per screen but never
 * replaces the element). Before this fix, a user who navigated away from
 * Employees while that probe was in flight would have the late response
 * overwrite whatever screen they had navigated TO — not a null-write crash
 * like the Dashboard bug, but the same "stale continuation writes into a
 * screen it no longer owns" shape, arguably more visible here since it
 * replaces the WHOLE screen rather than a few KPI fields.
 *
 * The fix mirrors subsystem-retail.js's _beginRender()/_isStaleRender()
 * exactly in name and contract (a genuine early return, never a try/catch)
 * but is LOCAL to RetailEmployees, not reused from RetailSystem — see the
 * comment on RetailEmployees._renderGeneration in employees.js for why
 * (short version: this screen's own test harness, and any future caller,
 * has no obligation to hand RetailSystem's dashboard-only counter to this
 * screen, and reaching for it would be a load-order dependency this screen
 * does not need). _load() itself needed no equivalent guard: it captures its
 * `tbody` reference ONCE, synchronously, before its own internal awaits, so
 * a stale call's late write always lands on the node that was live when
 * THAT call started, never on a node a later render created — see _load()'s
 * own docstring in employees.js.
 *
 * Modelled on retail_render_race_test.js (the deterministic hand-resolved-
 * promise shape) and retail_employees_screen_test.js (the vm harness for
 * this file, reused directly below rather than reimplemented).
 *
 * No test framework is configured for this vanilla-JS, build-step-free
 * frontend (see CLAUDE.md), so this runs standalone on Node built-ins:
 *
 *   node products/retail/tests/retail_employees_ui_state_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND_FILE = path.join(__dirname, '..', 'frontend', 'employees.js');
const SRC = fs.readFileSync(FRONTEND_FILE, 'utf8');

/* Match the file's OWN line ending -- an anchor written with the wrong one
   would silently match nothing, making a mutation proof below pass while
   proving nothing (same hazard retail_branches_test.js's identical helper
   guards against). */
function eolOf(src) { return src.indexOf('\r\n') !== -1 ? '\r\n' : '\n'; }
function nlFor(src) { const eol = eolOf(src); return (s) => s.replace(/\n/g, eol); }

// ─────────────────────────────────────────────────────────────────────────────
// THE SANDBOX — the real employees.js in a vm context, never a
// reimplementation. Element/document stubs match
// retail_employees_screen_test.js's `load()` (this screen's existing
// harness) so both files exercise the identical shape of RetailSystem/
// SubsystemApp/document stub.
// ─────────────────────────────────────────────────────────────────────────────

function makeElementStub() {
  return {
    innerHTML: '',
    textContent: '',
    value: '',
    disabled: false,
    id: '',
    className: '',
    style: {},
    dataset: {},
    appendChild() {},
    addEventListener(type, fn) { this['_on' + type] = fn; },
    removeEventListener() {},
    remove() {},
    focus() {},
    select() {},
    setSelectionRange() {},
    getAttribute() { return null; },
    setAttribute() {},
    querySelector() { return makeElementStub(); },
    querySelectorAll() { return []; },
  };
}

/**
 * @param {object} opts
 *   source         -- employees.js text (a mutant, for the proofs below)
 *   role           -- SubsystemApp.role
 *   currentUserId  -- SubsystemApp.currentUser.id
 *   putResult      -- response PUT resolves with (default {success:true})
 *   putThrows      -- if true, PUT rejects (network failure)
 *   postResult     -- response POST resolves with (default {success:true, setup_link:'...'})
 *   postThrows     -- if true, POST rejects (network failure)
 *   deferGets      -- if true, `_get` returns a promise this file settles by
 *                     hand (pushed onto the returned `pendingGets` array,
 *                     in call order) instead of resolving immediately --
 *                     used only by the render-race section below.
 */
function load(opts) {
  const options = opts || {};
  const toasts = [];
  const pendingGets = []; // { url, resolve(payload) } -- deferGets mode only

  const RetailSystemStub = {
    _injectStyles() {},
    _esc(v) {
      return String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },
    _badge(text, color) { return `<span class="ret-badge ret-badge-${color || 'blue'}">${text}</span>`; },
    async _get(url) {
      if (options.deferGets) {
        let resolveFn;
        const p = new Promise((resolve) => { resolveFn = resolve; });
        pendingGets.push({ url: String(url), resolve: resolveFn });
        return p;
      }
      if (String(url).includes('/api/sub/retail/branches')) {
        return { status: 'success', data: options.branches || [] };
      }
      return options.employeesResult || { success: true, employees: options.employees || [] };
    },
    async _post() {
      if (options.postThrows) throw new Error('Network failure');
      return options.postResult === undefined
        ? { success: true, setup_link: 'http://x/#setup/tok' }
        : options.postResult;
    },
    async _put() {
      if (options.putThrows) throw new Error('Network failure');
      return options.putResult === undefined ? { success: true } : options.putResult;
    },
    async _del() { return { success: true }; },
  };

  const sandbox = {
    console,
    t: (s) => s, // identity stub -- this file checks button/DOM STATE, not catalogs
    confirm: () => true,
    navigator: {},
    RetailSystem: RetailSystemStub,
    SubsystemApp: {
      role: 'role' in options ? options.role : 'admin',
      currentUser: { id: 'currentUserId' in options ? options.currentUserId : undefined },
      showToast(msg, type) { toasts.push([msg, type]); },
    },
    document: {
      _byId: {},
      getElementById(id) {
        if (!this._byId[id]) this._byId[id] = makeElementStub();
        return this._byId[id];
      },
      createElement() { return makeElementStub(); },
      querySelector() { return makeElementStub(); },
      querySelectorAll() { return []; },
      head: { appendChild() {} },
      body: { appendChild() {} },
    },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(opts && opts.source || SRC, sandbox, { filename: FRONTEND_FILE });

  assert.ok(sandbox.RetailEmployees, 'employees.js did not expose window.RetailEmployees');
  return { screen: sandbox.RetailEmployees, toasts, doc: sandbox.document, pendingGets };
}

async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

// ─────────────────────────────────────────────────────────────────────────────
// MUTATION HARNESS — same shape as retail_render_race_test.js / retail_branches_test.js
// ─────────────────────────────────────────────────────────────────────────────

function mutate(src, pairs) {
  const nl = nlFor(src);
  let out = src;
  for (const [rawFind, rawReplace] of pairs) {
    const find = nl(rawFind);
    const replace = nl(rawReplace);
    const hits = out.split(find).length - 1;
    assert.strictEqual(
      hits, 1,
      `Mutation anchor occurs ${hits} time(s), expected exactly 1:\n  ${JSON.stringify(find)}\n\n` +
      'A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it.'
    );
    out = out.replace(find, replace);
  }
  return out;
}

async function provesMutation(what, mutatedSrc, check) {
  let threw = null;
  try {
    await check(mutatedSrc);
  } catch (err) {
    threw = err;
  }
  assert.ok(
    threw,
    `MUTATION SURVIVED — ${what}\n` +
    'The guard for this passed against a build with the behaviour deliberately broken, so it is ' +
    'not actually watching it. Fix the check, not the mutation.'
  );
  return `${what}  [caught: ${String(threw.message || threw).split('\n')[0].slice(0, 140)}]`;
}

const BM_SELF_ROW_GRANTED = {
  id: 'u-bm', employee_id: 'EMP-BM01', email: 'manager@shop.local',
  role: 'manager', effective_role: 'manager', branch_scope_uid: 'br-1',
  status: 'active', has_pin: true, can_manage_staff: true,
};
const BM_SELF_ROW_REVOKED = Object.assign({}, BM_SELF_ROW_GRANTED, { can_manage_staff: false });

// ═════════════════════════════════════════════════════════════════════════════
// SHAPE 1 — Save-button restore (PIN: already correct; both handlers below)
// ═════════════════════════════════════════════════════════════════════════════

// ── (a) _saveRole -- the STATIC-label case ──────────────────────────────────

function seedRoleButton(ctx) {
  const btn = ctx.doc.getElementById('emp-role-btn');
  btn.textContent = 'Save'; // as _openRole() actually renders it
  btn.disabled = false;
  ctx.doc.getElementById('emp-role-select').value = 'manager';
  return btn;
}

async function testRoleRefusalRestoresButtonLabelAndEnabled(src) {
  const ctx = load({ source: src, putResult: { success: false, error: 'Could not change the role.' } });
  const btn = seedRoleButton(ctx);
  await ctx.screen._saveRole('u1');
  assert.strictEqual(btn.textContent, 'Save',
    `a PUT refusal should restore the Save button's original label, but it is stuck on: ${JSON.stringify(btn.textContent)}`);
  assert.strictEqual(btn.disabled, false, 'the Save button is still disabled after a refusal.');
  console.log('PASS: _saveRole restores its label and re-enables after a server refusal');
}

async function testRoleThrownRequestRestoresButton(src) {
  const ctx = load({ source: src, putThrows: true });
  const btn = seedRoleButton(ctx);
  await ctx.screen._saveRole('u1');
  assert.strictEqual(btn.textContent, 'Save',
    `a thrown (network failure) PUT left the Save button on a stale label: ${JSON.stringify(btn.textContent)}`);
  assert.strictEqual(btn.disabled, false, 'a thrown request left the Save button disabled.');
  console.log('PASS: _saveRole restores its label and re-enables after a thrown request');
}

// THE ALLOW-HALF -- a "fix" that never lets a save succeed would pass the two
// tests above trivially. A real successful save must still close the modal
// and reload the roster.
async function testRoleSuccessfulSaveStillClosesModalAndReloads(src) {
  const ctx = load({ source: src, putResult: { success: true } });
  const btn = seedRoleButton(ctx);
  const modal = ctx.doc.getElementById('emp-role-modal');
  let removed = false;
  modal.remove = () => { removed = true; };
  let loaded = false;
  ctx.screen._load = () => { loaded = true; };

  await ctx.screen._saveRole('u1');

  assert.ok(removed, 'a successful role save did not close the role modal.');
  assert.ok(loaded, 'a successful role save did not reload the employee list.');
  assert.ok(ctx.toasts.some(([m, ty]) => ty === 'success' && m === 'Role updated'),
    `the "Role updated" success toast did not appear. Toasts: ${JSON.stringify(ctx.toasts)}`);
  console.log('PASS: a successful role save still closes the modal, reloads the list, and toasts success (unchanged)');
}

// ANTI-VACUITY -- the button must actually be flipped to its busy label
// mid-flight, or the two restore tests above would pass against a handler
// that never touched the button at all.
async function testRoleBusyLabelActuallyChangesDuringInFlight(src) {
  const ctx = load({ source: src, putResult: { success: true } });
  const btn = seedRoleButton(ctx);
  ctx.screen._load = () => {};

  const pending = ctx.screen._saveRole('u1'); // deliberately not yet awaited
  assert.strictEqual(btn.textContent, 'Working…',
    `ANTI-VACUITY: the button never switched to its busy label during the in-flight window (saw ${JSON.stringify(btn.textContent)})`);
  assert.strictEqual(btn.disabled, true, 'ANTI-VACUITY: the button was never disabled during the in-flight window.');
  await pending;
  console.log('PASS: _saveRole actually switches the button to its busy label during the in-flight window');
}

// ── (b) _submitInvite -- the DYNAMIC-label case (the one that matters most:
//        this is the exact shape -- restore to the label the button was
//        ACTUALLY RENDERED WITH -- that was wrong, hardcoded to 'Save', in
//        six subsystem-retail.js handlers) ──────────────────────────────────

function seedInviteButton(ctx, reduced) {
  const btn = ctx.doc.getElementById('emp-inv-btn');
  // As _openInvite(reduced) actually renders it:
  // `${reduced ? t('Add Cashier') : t('Create Invite')}`.
  btn.textContent = reduced ? 'Add Cashier' : 'Create Invite';
  btn.disabled = false;
  ctx.doc.getElementById('emp-inv-email').value = 'new@shop.local';
  return btn;
}

async function testInviteRefusalRestoresTheDynamicRenderedLabel(src, reduced, expectedLabel) {
  const ctx = load({ source: src, postResult: { success: false, error: 'Email already registered.' } });
  const btn = seedInviteButton(ctx, reduced);
  await ctx.screen._submitInvite(reduced);
  assert.strictEqual(btn.textContent, expectedLabel,
    `_submitInvite(reduced=${reduced}): a refusal should restore "${expectedLabel}" (the label this ` +
    `button was actually rendered with), not a hardcoded guess. Got: ${JSON.stringify(btn.textContent)}`);
  assert.strictEqual(btn.disabled, false, `_submitInvite(reduced=${reduced}): still disabled after a refusal.`);
  console.log(`PASS: _submitInvite(reduced=${reduced}) restores its own rendered label ("${expectedLabel}"), not a hardcoded one`);
}

async function testInviteThrownRequestRestoresTheDynamicLabel(src) {
  const ctx = load({ source: src, postThrows: true });
  const btn = seedInviteButton(ctx, true);
  await ctx.screen._submitInvite(true);
  assert.strictEqual(btn.textContent, 'Add Cashier',
    `a thrown invite request left the button on a stale label: ${JSON.stringify(btn.textContent)}`);
  assert.strictEqual(btn.disabled, false, 'a thrown invite request left the button disabled.');
  console.log('PASS: _submitInvite restores its rendered label after a thrown request too');
}

async function testInviteSuccessfulSubmitStillShowsTheLink(src) {
  const ctx = load({ source: src, postResult: { success: true, setup_link: 'http://x/#setup/tok' } });
  const btn = seedInviteButton(ctx, false);
  const modal = ctx.doc.getElementById('emp-invite-modal');
  let removed = false;
  modal.remove = () => { removed = true; };
  let shownLink = null;
  ctx.screen._showInviteLink = (email, link) => { shownLink = link; };
  ctx.screen._load = () => {};

  await ctx.screen._submitInvite(false);

  assert.ok(removed, 'a successful invite did not close the invite modal.');
  assert.strictEqual(shownLink, 'http://x/#setup/tok', 'a successful invite did not show the setup link.');
  console.log('PASS: a successful invite still closes the modal and shows the setup link (unchanged)');
}

// ═════════════════════════════════════════════════════════════════════════════
// SHAPE 2 — Render generation guard (FIXED here)
// ═════════════════════════════════════════════════════════════════════════════

async function testSupersededBranchManagerRenderWritesNothing(src) {
  const ctx = load({ source: src, role: 'manager', currentUserId: 'u-bm', deferGets: true });
  const c = makeElementStub();

  // Render A: the user opens Employees, and is left hanging on its roster probe.
  const pA = ctx.screen.render(c);
  await settle();
  assert.strictEqual(ctx.pendingGets.length, 1, `expected render A to have reached its probe by now. Got ${ctx.pendingGets.length}`);

  // Render B: the user navigated away and back (or the router re-rendered
  // Employees again) before A's probe settled -- A is now superseded.
  const pB = ctx.screen.render(c);
  await settle();
  assert.strictEqual(ctx.pendingGets.length, 2, `expected render B to have reached its own probe by now. Got ${ctx.pendingGets.length}`);

  // B resolves and paints the reduced screen FIRST.
  ctx.pendingGets[1].resolve({ success: true, employees: [BM_SELF_ROW_GRANTED] });
  await settle();
  // B's own render() then awaits _load(probe), which fetches the branch list.
  assert.strictEqual(ctx.pendingGets.length, 3, `expected B's _load() to have reached the branch-list fetch. Got ${ctx.pendingGets.length}`);
  ctx.pendingGets[2].resolve({ status: 'success', data: [] });
  await pB;
  await settle();

  assert.ok(c.innerHTML.includes('Add Cashier'),
    `render B's reduced screen did not paint before A resolved. Got: ${c.innerHTML}`);
  assert.ok(!c.innerHTML.includes('store owner only.'),
    'the refusal panel appeared even though B was granted -- test setup is wrong.');

  // THEN A's probe resolves -- late, after B has already painted the screen
  // the user is currently looking at. A's own row here is REVOKED, so an
  // unguarded A would overwrite the screen with the refusal panel.
  ctx.pendingGets[0].resolve({ success: true, employees: [BM_SELF_ROW_REVOKED] });
  let threw = null;
  try { await pA; } catch (e) { threw = e; }
  await settle();

  assert.strictEqual(threw, null, 'a superseded render must resolve cleanly, not throw. Got: ' + (threw && (threw.stack || threw.message)));

  // THE ASSERTION THAT MATTERS: the screen must STILL show render B's
  // content -- A's late, stale (and in this case REVOKED) roster answer must
  // not have overwritten what the user is looking at.
  assert.ok(c.innerHTML.includes('Add Cashier'),
    `render A (superseded) overwrote the screen -- B's reduced table is gone. Got: ${c.innerHTML}`);
  assert.ok(!c.innerHTML.includes('store owner only.'),
    `render A (superseded) painted its own refusal panel over B's screen. Got: ${c.innerHTML}`);
  // Exactly 3 probe/branch fetches total (A's probe, B's probe, B's branch
  // list) -- A must not have gone on to fetch branches of its own after
  // being superseded.
  assert.strictEqual(ctx.pendingGets.length, 3,
    `render A (superseded) went on to fetch something of its own after the guard should have stopped it. Got ${ctx.pendingGets.length} calls`);

  console.log('PASS: a superseded branch-manager render writes nothing (screen keeps showing the newer render) and resolves without throwing');
}

// THE NORMAL PATH still works, and anti-vacuity: a lone render whose probe
// resolves with nothing else running DOES paint the reduced screen. A guard
// that makes every render a no-op would pass the test above trivially and
// destroy the screen for every branch manager -- this is the test that
// catches that wrong fix.
async function testNormalBranchManagerRenderStillWorks(src) {
  const ctx = load({ source: src, role: 'manager', currentUserId: 'u-bm', deferGets: true });
  const c = makeElementStub();

  const p = ctx.screen.render(c);
  await settle();
  assert.strictEqual(ctx.pendingGets.length, 1, 'expected exactly one probe for a lone render');
  ctx.pendingGets[0].resolve({ success: true, employees: [BM_SELF_ROW_GRANTED] });
  await settle();
  assert.strictEqual(ctx.pendingGets.length, 2, 'expected the branch-list fetch after a granted probe');
  ctx.pendingGets[1].resolve({ status: 'success', data: [] });
  await p;
  await settle();

  assert.ok(c.innerHTML.includes('Add Cashier'),
    `a normal, uncontested branch-manager render did not paint the reduced screen. Got: ${c.innerHTML}`);
  assert.strictEqual(ctx.screen._reduced, true, 'a normal, uncontested render did not set reduced mode');

  console.log('PASS: a normal, uncontested branch-manager render still paints the reduced screen (anti-vacuity: the guard does not block it)');
}

// ═════════════════════════════════════════════════════════════════════════════
// MAIN
// ═════════════════════════════════════════════════════════════════════════════

async function main() {
  const results = [];
  let failed = 0;

  async function run(name, fn) {
    try {
      const detail = await fn();
      results.push(`  ok   ${name}` + (detail ? `\n       ${detail}` : ''));
    } catch (err) {
      failed += 1;
      results.push(`  FAIL ${name}\n       ${(err && err.stack) || err}`);
    }
  }

  // ── Shape 1: against the REAL, unmutated build ───────────────────────────
  await run('testRoleRefusalRestoresButtonLabelAndEnabled',
    () => testRoleRefusalRestoresButtonLabelAndEnabled(SRC));
  await run('testRoleThrownRequestRestoresButton',
    () => testRoleThrownRequestRestoresButton(SRC));
  await run('testRoleSuccessfulSaveStillClosesModalAndReloads',
    () => testRoleSuccessfulSaveStillClosesModalAndReloads(SRC));
  await run('testRoleBusyLabelActuallyChangesDuringInFlight',
    () => testRoleBusyLabelActuallyChangesDuringInFlight(SRC));
  await run('testInviteRefusalRestoresTheDynamicRenderedLabel (reduced=true, "Add Cashier")',
    () => testInviteRefusalRestoresTheDynamicRenderedLabel(SRC, true, 'Add Cashier'));
  await run('testInviteRefusalRestoresTheDynamicRenderedLabel (reduced=false, "Create Invite")',
    () => testInviteRefusalRestoresTheDynamicRenderedLabel(SRC, false, 'Create Invite'));
  await run('testInviteThrownRequestRestoresTheDynamicLabel',
    () => testInviteThrownRequestRestoresTheDynamicLabel(SRC));
  await run('testInviteSuccessfulSubmitStillShowsTheLink',
    () => testInviteSuccessfulSubmitStillShowsTheLink(SRC));

  // ── Shape 1: mutation proofs (both directions) ───────────────────────────

  // M1: delete the restore from _saveRole's finally => the refusal-restore
  // pin must FAIL, naming the stale busy label it found instead.
  await run('M1: restore removed from _saveRole => refusal-restore pin FAILS', async () => {
    const broken = mutate(SRC, [[
      "    } finally {\n      if (btn) { btn.disabled = false; btn.textContent = t('Save'); }\n    }\n  },\n\n  // ── Branch scope",
      "    } finally {\n      // MUTATED: restore removed -- button stays on its busy label\n    }\n  },\n\n  // ── Branch scope",
    ]]);
    return provesMutation('M1 restore removed from _saveRole', broken,
      (b) => testRoleRefusalRestoresButtonLabelAndEnabled(b));
  });

  // M2 (the dangerous wrong fix, ENGINEERING.md #1 "prove both directions"):
  // force _saveRole to never take the success branch. The allow-half test
  // must catch this -- a "restore" that works by never letting a save
  // succeed is worse than the bug it claims to fix.
  await run('M2: _saveRole never completes a save => allow-half pin FAILS', async () => {
    const broken = mutate(SRC, [[
      "      if (res && res.success) {\n        this._closeModal('emp-role-modal');\n        SubsystemApp.showToast(t('Role updated'), 'success');",
      "      if (false) { // MUTATED: never takes the success branch, even on a real success\n        this._closeModal('emp-role-modal');\n        SubsystemApp.showToast(t('Role updated'), 'success');",
    ]]);
    return provesMutation('M2 _saveRole never completes a save', broken,
      (b) => testRoleSuccessfulSaveStillClosesModalAndReloads(b));
  });

  // M3: reproduce the EXACT historical bug on purpose -- swap _submitInvite's
  // dynamic ternary restore for a hardcoded 'Save' (correct for nothing this
  // screen renders: the button says either "Add Cashier" or "Create Invite",
  // never "Save"). The dynamic-label pin must catch it for BOTH branches of
  // the ternary, not just one.
  await run('M3: _submitInvite restore hardcoded to \'Save\' => dynamic-label pin FAILS (both branches)', async () => {
    const broken = mutate(SRC, [[
      "      if (btn) { btn.disabled = false; btn.textContent = reduced ? t('Add Cashier') : t('Create Invite'); }",
      "      if (btn) { btn.disabled = false; btn.textContent = t('Save'); } // MUTATED: hardcoded, wrong for every Add/Create label",
    ]]);
    await provesMutation('M3a reduced=true restores "Save" instead of "Add Cashier"', broken,
      (b) => testInviteRefusalRestoresTheDynamicRenderedLabel(b, true, 'Add Cashier'));
    return provesMutation('M3b reduced=false restores "Save" instead of "Create Invite"', broken,
      (b) => testInviteRefusalRestoresTheDynamicRenderedLabel(b, false, 'Create Invite'));
  });

  // ── Shape 2: against the REAL, unmutated build ────────────────────────────
  await run('testSupersededBranchManagerRenderWritesNothing',
    () => testSupersededBranchManagerRenderWritesNothing(SRC));
  await run('testNormalBranchManagerRenderStillWorks',
    () => testNormalBranchManagerRenderStillWorks(SRC));

  // ── Shape 2: mutation proofs (both directions) ────────────────────────────

  // M4: remove the staleness check entirely => the race test must go RED,
  // and with the REAL corruption (B's screen overwritten), not a crash.
  await run('M4: generation check removed => testSupersededBranchManagerRenderWritesNothing FAILS', async () => {
    const broken = mutate(SRC, [[
      '    if (this._isStaleRender(renderToken)) return;\n',
      '    // MUTATED: generation check removed\n',
    ]]);
    return provesMutation('M4 generation check removed', broken,
      (b) => testSupersededBranchManagerRenderWritesNothing(b));
  });

  // M5 (the dangerous wrong fix): make the guard ALWAYS treat the render as
  // superseded. Passes M4's test trivially (nothing ever writes) while
  // silently turning the branch-manager screen into a permanent blank/
  // loading screen for every delegated manager -- the normal-path test must
  // catch it.
  await run('M5: guard always returns early (allow-half destroyed) => testNormalBranchManagerRenderStillWorks FAILS', async () => {
    const broken = mutate(SRC, [[
      '    if (this._isStaleRender(renderToken)) return;\n',
      '    if (true) return; // MUTATED: every render treated as superseded\n',
    ]]);
    return provesMutation('M5 guard always returns early', broken,
      (b) => testNormalBranchManagerRenderStillWorks(b));
  });

  console.log(results.join('\n'));
  if (failed) {
    console.error(`\nFAIL: retail_employees_ui_state_test.js — ${failed} of ${results.length} check(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\nPASS: retail_employees_ui_state_test.js — ${results.length} check(s)`);
  }
}

main().catch((err) => {
  console.error('FAIL: retail_employees_ui_state_test.js (runner)');
  console.error((err && err.stack) || err);
  process.exitCode = 1;
});
