/**
 * retail_trial_banner_test.js -- the 7-day trial's two on-screen moments
 * (new customer flow, owner 2026-10-30):
 *
 *   1. while the trial RUNS: a calm "Trial: N days left" bar; the till sells;
 *   2. when it has ENDED (TRIAL_ENDED): "Your free trial has ended -- your data
 *      is safe, selling is locked", pointing at the licensing page where the
 *      Backup / Export buttons and the how-to-pay text live.
 *
 * Runs app-shell.js's real _renderLicenseBanner in a vm (same harness idea as
 * retail_license_banner_test.js) and mutation-proves both directions: a trial
 * bar on a PAID till is the single worst regression here, as is a missing lock
 * wording on an ended one.
 *
 *     node products/retail/tests/retail_trial_banner_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FRONTEND = path.join(__dirname, '..', 'frontend');
const SHELL_SRC = fs.readFileSync(path.join(FRONTEND, 'app-shell.js'), 'utf8');
const EN = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'en.json'), 'utf8'));
const AR = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'locales', 'ar.json'), 'utf8'));

function fakeElement() {
  return {
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {}, remove() {},
    appendChild() {}, setAttribute() {}, removeAttribute() {}, focus() {}, click() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    innerHTML: '', textContent: '', title: '', hidden: false, value: '',
  };
}

function bootShell(src) {
  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    document: {
      readyState: 'complete', body: fakeElement(), documentElement: fakeElement(), head: fakeElement(),
      getElementById() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
      createElement() { return fakeElement(); }, addEventListener() {},
    },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    setTimeout, clearTimeout, setInterval, clearInterval,
    location: { href: '', hash: '', reload() {} },
    navigator: { language: 'en', userAgent: 'node' },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    CustomEvent: function () {}, Event: function () {}, BroadcastChannel: undefined,
    t: (s) => s,
    async fetch() { return { ok: true, status: 200, json: async () => ({}) }; },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(src || SHELL_SRC, sandbox, { filename: 'app-shell.js' });
  return sandbox.SubsystemApp;
}

function render(src, state, trial) {
  const app = bootShell(src);
  app._licenseTrial = trial;
  app._renderLicenseBanner(state);
  return app;
}

const RUNNING = (n) => ({ is_trial: true, ended: false, days_left: n, term_end: '2026-11-05' });

function mutate(find, replace) {
  assert.strictEqual(SHELL_SRC.split(find).length - 1, 1, 'mutation anchor must occur exactly once: ' + find);
  return SHELL_SRC.replace(find, replace);
}

function mustThrow(what, fn) {
  let threw = null;
  try { fn(); } catch (e) { threw = e; }
  assert.ok(threw, 'MUTATION SURVIVED -- ' + what);
  console.log('PASS: ' + what + '  [caught: ' + String(threw.message).split('\n')[0].slice(0, 110) + ']');
}

function testRunningTrialShowsDaysLeftAndStillSells() {
  const app = render(null, 'ACTIVE_ONLINE', RUNNING(3));
  assert.ok(app._licenseBannerEl, 'a running trial rendered no bar');
  const html = app._licenseBannerEl.innerHTML;
  assert.ok(html.includes('Trial: 3 days left'), 'wrong wording: ' + html);
  assert.ok(/openLicensing\(\)/.test(html), 'the bar has no way into the licensing page');
  assert.ok(!app.LICENSE_BANNER_BLOCKED_STATES.includes('ACTIVE_ONLINE'));
  console.log('PASS: a running trial shows "Trial: 3 days left" and ACTIVE_ONLINE stays unblocked');
}

function testSingularAndDualHaveTheirOwnWording() {
  assert.ok(render(null, 'ACTIVE_ONLINE', RUNNING(1))._licenseBannerEl.innerHTML.includes('Trial: 1 day left'));
  assert.ok(render(null, 'ACTIVE_ONLINE', RUNNING(2))._licenseBannerEl.innerHTML.includes('Trial: 2 days left'));
  console.log('PASS: 1 and 2 days use their own keys (Arabic singular / dual)');
}

function testNoTrialBarOnAPaidOrFinishedOrUnknownTill() {
  const cases = {
    'paid lease (is_trial false)': { is_trial: false, ended: false, days_left: null },
    'ended trial': { is_trial: true, ended: true, days_left: null },
    'zero days': { is_trial: true, ended: false, days_left: 0 },
    'no trial block at all': null,
    'old backend without the field': undefined,
  };
  Object.keys(cases).forEach((name) => {
    assert.strictEqual(render(null, 'ACTIVE_ONLINE', cases[name])._licenseBannerEl, null, name + ' still showed a bar');
  });
  console.log('PASS: no trial bar for a paid lease, an ended trial, zero days, or a status without the trial block');
}

function testTrialBarClearsWhenThePaidLeaseArrives() {
  const app = render(null, 'ACTIVE_ONLINE', RUNNING(2));
  assert.ok(app._licenseBannerEl);
  app._licenseTrial = { is_trial: false, ended: false, days_left: null };
  app._renderLicenseBanner('ACTIVE_ONLINE');
  assert.strictEqual(app._licenseBannerEl, null, 'the bar stayed after the lease became paid');
  console.log('PASS: the bar is removed outright once the lease is paid');
}

function testEndedTrialShowsTheLockWording() {
  const app = render(null, 'TRIAL_ENDED', { is_trial: true, ended: true, days_left: null });
  const html = app._licenseBannerEl.innerHTML;
  assert.ok(app.LICENSE_BANNER_BLOCKED_STATES.includes('TRIAL_ENDED'));
  assert.ok(html.includes('Your free trial has ended'), html);
  assert.ok(html.includes('Your data is safe. Selling is locked until you pay.'), html);
  assert.ok(html.includes('How to unlock') && /openLicensing\(\)/.test(html), html);
  assert.ok(!html.includes('This device is not licensed'), 'fell through to the generic "not licensed" wording');
  console.log('PASS: TRIAL_ENDED shows the lock wording and a way to the how-to-pay screen');
}

function testEveryTrialStringHasARealArabicTranslation() {
  const keys = [
    'Trial: 1 day left', 'Trial: 2 days left', 'Trial: {n} days left', 'Trial details',
    'Contact your Aura seller to keep using Aura after the trial.',
    'Your free trial has ended', 'Your data is safe. Selling is locked until you pay.', 'How to unlock',
  ];
  // Plus every literal the licensing page's lock screen uses, read from the source so a string
  // added later cannot ship untranslated. [^'\\]: any char but a quote or backslash; \\. an escape.
  const lic = fs.readFileSync(path.join(FRONTEND, 'licensing.js'), 'utf8');
  const panel = /function trialDaysLeftText[\s\S]*?function renderLicenseStatus/.exec(lic)[0];
  const literal = /(?<![A-Za-z0-9_])t\('((?:[^'\\]|\\.)*)'\)/g;
  for (const m of panel.matchAll(literal)) keys.push(m[1].split("\\'").join("'"));
  assert.ok(keys.length > 20, 'the licensing.js extractor found too few strings');
  const problems = [];
  new Set(keys).forEach((k) => {
    if (!(k in EN)) problems.push('missing in en.json: ' + k);
    if (!(k in AR)) problems.push('missing in ar.json: ' + k);
    else if (AR[k] === k) problems.push('no real Arabic: ' + k);
  });
  assert.deepStrictEqual(problems, []);
  console.log('PASS: all ' + new Set(keys).size + ' trial strings exist in en + ar with a real Arabic value');
}

function testMutations() {
  mustThrow('dropping the trial bar is caught', () => {
    const src = mutate('if (trialRunning) {\n        this._renderTrialBanner', 'if (false) {\n        this._renderTrialBanner');
    assert.ok(render(src, 'ACTIVE_ONLINE', RUNNING(3))._licenseBannerEl, 'a running trial rendered no bar');
  });
  mustThrow('showing the trial bar on a PAID till is caught', () => {
    const src = mutate('trial.is_trial === true && trial.ended !== true', 'true');
    const app = render(src, 'ACTIVE_ONLINE', { is_trial: false, ended: false, days_left: 5 });
    assert.strictEqual(app._licenseBannerEl, null, 'a paid till showed a trial bar');
  });
  mustThrow('losing the TRIAL_ENDED wording is caught', () => {
    const src = mutate("if (state === 'TRIAL_ENDED') {", 'if (false) {');
    const app = render(src, 'TRIAL_ENDED', { is_trial: true, ended: true });
    assert.ok(app._licenseBannerEl.innerHTML.includes('Your free trial has ended'), 'generic wording shown');
  });
}

[
  testRunningTrialShowsDaysLeftAndStillSells, testSingularAndDualHaveTheirOwnWording,
  testNoTrialBarOnAPaidOrFinishedOrUnknownTill, testTrialBarClearsWhenThePaidLeaseArrives,
  testEndedTrialShowsTheLockWording, testEveryTrialStringHasARealArabicTranslation, testMutations,
].forEach((fn) => fn());
console.log('PASS retail_trial_banner_test.js');
