/**
 * The operator's hub screen (products/retail/frontend/site-relay.js).
 *
 * Loads the REAL frontend file through Node's `vm` module against a DOM stub
 * -- the same technique retail_btn_danger_hover_test.js uses -- rather than
 * reimplementing its logic here. A test that re-describes the code proves the
 * description, not the code.
 *
 * WHAT IS WORTH GUARDING ON THIS SCREEN, in priority order:
 *
 *  1. DEVICE LABELS ARE UNTRUSTED INPUT. A label is typed on some other
 *     device and arrives here over sync. Rendering one with innerHTML would
 *     be stored XSS on the till's own admin screen -- the one surface where a
 *     script runs with an admin session. The code uses textContent; this file
 *     pins that, because "use textContent" is exactly the kind of thing a
 *     later refactor into a template string quietly undoes.
 *  2. A NON-HUB INSTALL MUST SHOW NOTHING OPERATIONAL. Most tills will never
 *     be hubs. Showing them a "Connect a device" button that cannot work is
 *     how support calls are made.
 *  3. AN EXPIRED CODE MUST DISAPPEAR. The backend expires it regardless of
 *     what the screen shows, so a code left on screen is one a shop owner
 *     will scan and watch fail for no visible reason.
 *
 * Run:
 *     node products/retail/tests/retail_site_relay_ui_test.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SCRIPT_PATH = path.resolve(
    __dirname, '..', 'frontend', 'site-relay.js');

const ELEMENT_IDS = [
    'message', 'card-disabled', 'card-status', 'card-connect', 'card-devices',
    'fact-addresses', 'fact-port', 'fact-pin', 'btn-pair', 'btn-pair-done',
    'pairing-output', 'pairing-qr', 'pairing-text', 'pairing-countdown',
    'devices-table', 'devices-body', 'devices-empty',
];

function makeElement(tag) {
    const el = {
        tagName: (tag || 'div').toUpperCase(),
        hidden: false,
        className: '',
        _textContent: '',
        innerHTMLWrites: 0,
        style: {},
        children: [],
        listeners: {},
        value: '',
        src: '',
        disabled: false,
        get textContent() { return this._textContent; },
        set textContent(v) { this._textContent = String(v); this.children = []; },
        // Deliberately instrumented rather than implemented: nothing on this
        // screen may build markup from a string, so any write here is a
        // finding, not a feature.
        set innerHTML(v) { this.innerHTMLWrites += 1; this._innerHTML = String(v); },
        get innerHTML() { return this._innerHTML || ''; },
        appendChild(child) { this.children.push(child); return child; },
        addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
        click() { (this.listeners.click || []).forEach((fn) => fn()); },
        select() {},
    };
    return el;
}

function makeHarness({ statusBody, pairBody }) {
    const elements = {};
    ELEMENT_IDS.forEach((id) => { elements[id] = makeElement('div'); });

    const fetchCalls = [];
    const documentListeners = {};

    const fetchImpl = (url, options) => {
        fetchCalls.push({ url, options: options || {} });
        let body = { success: true };
        if (url.endsWith('/status')) { body = statusBody; }
        else if (url.endsWith('/pair-code')) { body = pairBody || { success: true }; }
        return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    };

    const documentStub = {
        getElementById: (id) => elements[id] || null,
        createElement: (tag) => makeElement(tag),
        addEventListener: (name, fn) => {
            (documentListeners[name] = documentListeners[name] || []).push(fn);
        },
    };

    const windowStub = {
        fetch: fetchImpl,
        setInterval: () => 1,
        clearInterval: () => {},
        confirm: () => true,
    };

    const sandbox = {
        document: documentStub,
        window: windowStub,
        fetch: fetchImpl,
        Object,
        Promise,
        Date,
        Math,
        isNaN,
        String,
        console,
    };
    sandbox.globalThis = sandbox;

    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(SCRIPT_PATH, 'utf8'), sandbox, {
        filename: SCRIPT_PATH,
    });

    // The script registers its work on DOMContentLoaded; fire it.
    (documentListeners.DOMContentLoaded || []).forEach((fn) => fn());

    return { elements, fetchCalls, windowStub };
}

function flush() {
    // Two turns of the microtask queue: the status fetch resolves, then its
    // .then chain renders.
    return new Promise((resolve) => setImmediate(() => setImmediate(resolve)));
}

async function testNonHubShowsNothingOperational() {
    const h = makeHarness({ statusBody: { success: true, enabled: false } });
    await flush();

    assert.strictEqual(h.elements['card-disabled'].hidden, false,
        'a non-hub install must be told it is not the hub');
    assert.strictEqual(h.elements['card-connect'].hidden, true,
        'a non-hub install must NOT offer "Connect a device"');
    assert.strictEqual(h.elements['card-devices'].hidden, true,
        'a non-hub install must not show a device list');
}

async function testRunningHubShowsItsDetails() {
    const h = makeHarness({
        statusBody: {
            success: true, enabled: true, port: 5443, spki_pin: 'PIN123',
            addresses: ['192.168.1.50'], devices: [],
        },
    });
    await flush();

    assert.strictEqual(h.elements['card-status'].hidden, false);
    assert.strictEqual(h.elements['fact-port'].textContent, '5443');
    assert.strictEqual(h.elements['fact-pin'].textContent, 'PIN123');
    assert.strictEqual(h.elements['fact-addresses'].textContent, '192.168.1.50');
    assert.strictEqual(h.elements['devices-empty'].hidden, false,
        'a hub with no devices must say so rather than show an empty table');
}

async function testMissingAddressesIsExplainedNotBlank() {
    // local_lan_addresses() is best-effort and returns [] on some
    // multi-interface Windows setups. That is not an error -- the address is a
    // convenience and devices find the hub by its signed beacon -- so the
    // screen must say something useful rather than render an empty field.
    const h = makeHarness({
        statusBody: {
            success: true, enabled: true, port: 5443, spki_pin: 'PIN123',
            addresses: [], devices: [],
        },
    });
    await flush();

    const text = h.elements['fact-addresses'].textContent;
    assert.ok(text && text.length > 0, 'address field must not be blank');
    assert.ok(/still find this hub/i.test(text),
        `expected a reassuring explanation, got: ${text}`);
}

async function testDeviceLabelsAreNeverRenderedAsMarkup() {
    const hostile = '<img src=x onerror="alert(1)">';
    const h = makeHarness({
        statusBody: {
            success: true, enabled: true, port: 5443, spki_pin: 'PIN',
            addresses: [], devices: [{
                installation_id: 'aaaa-bbbb', label: hostile,
                paired_at: '2026-09-14T10:00:00', revoked: false,
            }],
        },
    });
    await flush();

    const rows = h.elements['devices-body'].children;
    assert.strictEqual(rows.length, 1, 'the device row should have rendered');

    let innerHTMLWrites = 0;
    let foundLiteralLabel = false;
    (function walk(node) {
        innerHTMLWrites += node.innerHTMLWrites || 0;
        if (node.textContent === hostile) { foundLiteralLabel = true; }
        (node.children || []).forEach(walk);
    })(h.elements['devices-body']);

    assert.strictEqual(innerHTMLWrites, 0,
        'device rows must never be built with innerHTML -- labels arrive from '
        + 'other devices and are untrusted input on an admin screen');
    assert.ok(foundLiteralLabel,
        'the hostile label should appear as literal TEXT, proving it was set '
        + 'via textContent rather than parsed as markup');
}

async function testRemoveButtonCallsTheRevokeEndpoint() {
    const h = makeHarness({
        statusBody: {
            success: true, enabled: true, port: 5443, spki_pin: 'PIN',
            addresses: [], devices: [{
                installation_id: 'device-42', label: 'Tablet',
                paired_at: '2026-09-14T10:00:00', revoked: false,
            }],
        },
    });
    await flush();

    // Last cell of the row holds the Remove button.
    const row = h.elements['devices-body'].children[0];
    const actionCell = row.children[row.children.length - 1];
    assert.strictEqual(actionCell.children.length, 1, 'expected a Remove button');
    actionCell.children[0].click();
    await flush();

    const revokeCall = h.fetchCalls.find((c) => c.url.indexOf('/devices/') !== -1);
    assert.ok(revokeCall, 'clicking Remove must call the revoke endpoint');
    assert.strictEqual(revokeCall.url, '/api/site-relay/devices/device-42/revoke');
    assert.strictEqual(revokeCall.options.method, 'POST');
}

async function testARevokedDeviceOffersNoRemoveButton() {
    const h = makeHarness({
        statusBody: {
            success: true, enabled: true, port: 5443, spki_pin: 'PIN',
            addresses: [], devices: [{
                installation_id: 'gone', label: 'Old tablet',
                paired_at: '2026-09-14T10:00:00', revoked: true,
            }],
        },
    });
    await flush();

    const row = h.elements['devices-body'].children[0];
    const actionCell = row.children[row.children.length - 1];
    assert.strictEqual(actionCell.children.length, 0,
        'an already-removed device must not offer Remove again');
}

async function testPairingCodeIsShownWithItsQrAndCopyText() {
    const h = makeHarness({
        statusBody: {
            success: true, enabled: true, port: 5443, spki_pin: 'PIN',
            addresses: [], devices: [],
        },
        pairBody: {
            success: true,
            qr_svg: 'data:image/svg+xml;base64,AAAA',
            qr_text: '{"pairing_code":"abc"}',
            expires_in_seconds: 300,
        },
    });
    await flush();

    h.elements['btn-pair'].click();
    await flush();

    assert.strictEqual(h.elements['pairing-output'].hidden, false,
        'the pairing code must become visible');
    assert.strictEqual(h.elements['pairing-qr'].src, 'data:image/svg+xml;base64,AAAA');
    assert.strictEqual(h.elements['pairing-text'].value, '{"pairing_code":"abc"}',
        'the copy-paste fallback must carry the same payload as the QR');
}

async function main() {
    const tests = [
        testNonHubShowsNothingOperational,
        testRunningHubShowsItsDetails,
        testMissingAddressesIsExplainedNotBlank,
        testDeviceLabelsAreNeverRenderedAsMarkup,
        testRemoveButtonCallsTheRevokeEndpoint,
        testARevokedDeviceOffersNoRemoveButton,
        testPairingCodeIsShownWithItsQrAndCopyText,
    ];
    let failed = 0;
    for (const test of tests) {
        try {
            await test();
            console.log(`  ok   ${test.name}`);
        } catch (err) {
            failed += 1;
            console.error(`  FAIL ${test.name}: ${err.message}`);
        }
    }
    console.log(`\n${tests.length - failed} passed, ${failed} failed`);
    process.exit(failed === 0 ? 0 : 1);
}

main();
