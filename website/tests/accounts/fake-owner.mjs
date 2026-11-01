// A stand-in for the Owner customer-account API, for the Playwright run only.
//
// It checks the signature with its OWN implementation (node:crypto, written from the spec in
// docs/owner/customer-accounts/signature-v1.md), not by importing the website's signer: if the website signed
// something Owner would refuse, this refuses it too, so a green run means the BFF's request really verifies.
//
// The routes and JSON shapes are the contract the website codes against; the same shapes are written up in
// the slice notes for the Owner builder (progress.md). Test accounts are chosen by email.
import http from 'node:http';
import crypto from 'node:crypto';

const PORT = Number(process.env.FAKE_OWNER_PORT ?? 8799);
const KID = 'k1';
const SECRET = process.env.FAKE_OWNER_SECRET ?? 'fake-owner-shared-secret-0123456789abcdef'; // pragma: allowlist secret (test fake only)
const BASE = '/api/website/v1/account';
const MAX_SKEW = 60;
const DOWNLOAD_BYTES = 3 * 1024 * 1024;
const INVITE_TOKEN = 'good-invite-token-0123456789abcdef'; // pragma: allowlist secret (test fake only)

const accounts = new Map();
const sessions = new Map(); // token -> email
const nonces = new Set();
const downloadTokens = new Map(); // token -> {uses, offer}
const log = [];
let usedInvite = false;

function seed() {
  accounts.clear(); sessions.clear(); nonces.clear(); downloadTokens.clear(); log.length = 0; usedInvite = false;
  const add = (email, password, extra) => accounts.set(email, { email, password, language: 'en', status: 'ACTIVE', customer_name: 'Haddad Market', password_must_change: false, trial: { state: 'ACTIVE', starts_on: '2026-11-01', ends_on: '2026-11-07', days_left: 5 }, ...extra });
  add('temp@shop.test', 'Temp-pass-2026', { password_must_change: true, trial: { state: 'NOT_STARTED', starts_on: null, ends_on: null, days_left: null } });
  add('ready@shop.test', 'Shop-pass-2026-ok', {});
  add('ar@shop.test', 'Shop-pass-2026-ok', { language: 'ar', customer_name: 'سوبرماركت الحداد' });
  add('ended@shop.test', 'Shop-pass-2026-ok', { trial: { state: 'ENDED', starts_on: '2026-10-20', ends_on: '2026-10-26', days_left: 0 } });
  add('last@shop.test', 'Shop-pass-2026-ok', { trial: { state: 'ACTIVE', starts_on: '2026-10-26', ends_on: '2026-11-01', days_left: 0 } });
  add('locked@shop.test', 'Shop-pass-2026-ok', {});
}
seed();

const sha256hex = (b) => crypto.createHash('sha256').update(b).digest('hex');
const token = () => crypto.randomBytes(24).toString('base64url');
const accountView = (a) => ({ email: a.email, language: a.language, status: a.status, customer_name: a.customer_name, password_must_change: a.password_must_change });

function verify(req, path, raw) {
  const header = req.headers['x-aura-site-signature'] ?? '';
  const [version, ...rest] = header.split(',');
  if (version !== 'v1') return 'bad_version';
  const f = Object.fromEntries(rest.map((p) => { const i = p.indexOf('='); return [p.slice(0, i), p.slice(i + 1)]; }));
  if (Object.keys(f).sort().join() !== 'kid,nonce,sig,ts') return 'malformed';
  if (!/^[0-9]{1,12}$/.test(f.ts) || !/^[A-Za-z0-9_-]{16,64}$/.test(f.nonce) || !/^[0-9a-f]{64}$/.test(f.sig) || !/^[A-Za-z0-9_.-]{1,32}$/.test(f.kid)) return 'malformed';
  if (f.kid !== KID) return 'unknown_kid';
  if (Math.abs(Date.now() / 1000 - Number(f.ts)) > MAX_SKEW) return 'skew';
  const canonical = ['v1', req.method.toUpperCase(), path, f.ts, f.nonce, sha256hex(raw)].join('\n');
  const expected = crypto.createHmac('sha256', Buffer.from(SECRET, 'utf8')).update(canonical, 'utf8').digest('hex');
  if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(f.sig))) return 'bad_signature';
  if (nonces.has(f.nonce)) return 'replay';
  nonces.add(f.nonce); // only after the signature passed, like Owner
  return null;
}

const send = (res, status, body, headers = {}) => {
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', ...headers });
  res.end(JSON.stringify(body));
};

function policy(password, a) {
  if (password.length < 10) return 'too_short';
  if (['password123', 'passwordpassword'].includes(password.toLowerCase())) return 'too_common';
  if (password.toLowerCase().includes(a.email.split('@')[0])) return 'contains_personal';
  return null;
}

function summary(a) {
  return {
    ok: true,
    account: accountView(a),
    licences: [{
      license_public_id: 'LIC-7QX2', product_code: 'RETAIL', product_name: 'Aura Retail', plan_code: 'P2', plan_name: 'Growth',
      branch_label: null, device_allowance: 2, trial: a.trial,
    }],
    seller: { name: 'Sami Al-Khatib', phone: '+962 79 000 1234' },
    downloads: [
      { offer_id: 'win-1', platform: 'windows', version: '1.0.0-rc.17', size_bytes: DOWNLOAD_BYTES, filename: 'AuraRetail-Setup-1.0.0-rc.17.exe' },
      { offer_id: 'apk-1', platform: 'android', version: '1.0.0-rc.17', size_bytes: DOWNLOAD_BYTES, filename: 'AuraRetail-1.0.0-rc.17.apk' },
    ],
  };
}

// The bytes of the "installer" are a repeating counter, so a test can recompute the hash independently.
function* installerChunks() {
  const chunk = Buffer.alloc(64 * 1024);
  for (let i = 0; i < chunk.length; i++) chunk[i] = i % 251;
  for (let sent = 0; sent < DOWNLOAD_BYTES; sent += chunk.length) yield chunk;
}
export const EXPECTED_SHA256 = (() => { const h = crypto.createHash('sha256'); for (const c of installerChunks()) h.update(c); return h.digest('hex'); })();

const handlers = {
  'sign-in': (b, res) => {
    const a = accounts.get(String(b.email).toLowerCase());
    if (a?.email === 'locked@shop.test') return send(res, 429, { ok: false, error: 'rate_limited' }, { 'retry-after': '600' });
    if (!a || a.password !== b.password) return send(res, 401, { ok: false, error: 'invalid_credentials' });
    const t = token(); sessions.set(t, a.email);
    send(res, 200, { ok: true, session_token: t, account: accountView(a), idle_seconds: 3600, absolute_seconds: 43200 });
  },
  'sign-out': (b, res) => { sessions.delete(b.session_token); send(res, 200, { ok: true }); },
  'summary': (b, res) => {
    const a = accounts.get(sessions.get(b.session_token));
    if (!a) return send(res, 401, { ok: false, error: 'session_invalid' });
    send(res, 200, summary(a));
  },
  'password/forgot': (b, res) => { log.push({ forgot: String(b.email).toLowerCase(), lang: b.lang }); send(res, 202, { ok: true }); },
  'password/set': (b, res) => {
    if (b.token !== INVITE_TOKEN || usedInvite) return send(res, 400, { ok: false, error: 'token_invalid' });
    const a = accounts.get('temp@shop.test');
    const why = policy(b.password, a);
    if (why) return send(res, 422, { ok: false, error: 'password_policy', reason: why });
    usedInvite = true; a.password = b.password; a.password_must_change = false;
    send(res, 200, { ok: true, account: accountView(a) });
  },
  'password/change': (b, res) => {
    const a = accounts.get(sessions.get(b.session_token));
    if (!a) return send(res, 401, { ok: false, error: 'session_invalid' });
    if (a.password !== b.current_password) return send(res, 401, { ok: false, error: 'invalid_credentials' });
    const why = policy(b.new_password, a);
    if (why) return send(res, 422, { ok: false, error: 'password_policy', reason: why });
    a.password = b.new_password; a.password_must_change = false;
    for (const [t, e] of sessions) if (e === a.email) sessions.delete(t);
    const t = token(); sessions.set(t, a.email);
    send(res, 200, { ok: true, session_token: t, account: accountView(a) });
  },
  'downloads/authorize': (b, res) => {
    const a = accounts.get(sessions.get(b.session_token));
    if (!a) return send(res, 401, { ok: false, error: 'session_invalid' });
    if (a.password_must_change) return send(res, 403, { ok: false, error: 'password_change_required' });
    if (!['win-1', 'apk-1'].includes(b.offer_id)) return send(res, 400, { ok: false, error: 'offer_unavailable' });
    const t = token(); downloadTokens.set(t, { uses: 0, offer: b.offer_id, issued: Date.now() });
    send(res, 200, { ok: true, token: t, expires_in_seconds: 600, max_uses: 3, filename: 'x', size_bytes: DOWNLOAD_BYTES });
  },
  'downloads/fetch': (b, res) => {
    const d = downloadTokens.get(b.token);
    if (!d) return send(res, 404, { ok: false, error: 'token_not_found' });
    if (d.uses >= 3 || Date.now() - d.issued > 600_000) return send(res, 410, { ok: false, error: 'token_expired' });
    d.uses += 1;
    const win = d.offer === 'win-1';
    res.writeHead(200, {
      'content-type': win ? 'application/x-msdownload' : 'application/vnd.android.package-archive',
      'content-length': String(DOWNLOAD_BYTES),
      'content-disposition': `attachment; filename="${win ? 'AuraRetail-Setup-1.0.0-rc.17.exe' : 'AuraRetail-1.0.0-rc.17.apk'}"`,
    });
    const it = installerChunks();
    // Manual next(): a `for...of` that returns early on backpressure calls it.return() and closes the generator,
    // which truncated the body to the first chunk or two (this fake, not the website, was the bug).
    const pump = () => {
      for (let n = it.next(); !n.done; n = it.next()) { if (!res.write(n.value)) return void res.once('drain', pump); }
      res.end();
    };
    pump();
  },
};

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/__health') return send(res, 200, { ok: true });
  if (url.pathname === '/__reset') { seed(); return send(res, 200, { ok: true }); }
  if (url.pathname === '/__log') return send(res, 200, { log });
  if (url.pathname === '/__sha') return send(res, 200, { sha256: EXPECTED_SHA256 });
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    const raw = Buffer.concat(chunks);
    const action = url.pathname.startsWith(BASE + '/') ? url.pathname.slice(BASE.length + 1) : null;
    if (req.method !== 'POST' || !action || !handlers[action]) return send(res, 404, { ok: false });
    const bad = verify(req, url.pathname, raw);
    if (bad) { log.push({ rejected: bad, action }); return send(res, 401, { ok: false, error: 'unauthorized' }); }
    let body;
    try { body = JSON.parse(raw.toString('utf8')); } catch { return send(res, 400, { ok: false, error: 'invalid_json' }); }
    if (typeof body.client_ip !== 'string' || !body.client_ip) return send(res, 400, { ok: false, error: 'invalid_request' });
    log.push({ action, ip: body.client_ip, sessionBound: Boolean(body.session_token) });
    handlers[action](body, res);
  });
}).listen(PORT, '127.0.0.1', () => console.log(`fake owner on ${PORT}`));
