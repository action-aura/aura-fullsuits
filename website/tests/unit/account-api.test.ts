// @vitest-environment node
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { onRequest as signIn } from '../../functions/api/account/sign-in';
import { onRequest as signOut } from '../../functions/api/account/sign-out';
import { onRequest as summary } from '../../functions/api/account/summary';
import { onRequest as forgot } from '../../functions/api/account/forgot';
import { onRequest as setPassword } from '../../functions/api/account/set-password';
import { onRequest as changePassword } from '../../functions/api/account/change-password';
import { onRequest as authorize } from '../../functions/api/account/downloads/authorize';
import { onRequest as fetchDownload } from '../../functions/api/account/downloads/fetch';
import { accountPagesMiddleware } from '../../functions/_lib/account-pages';
import { safeDownloadHeaders } from '../../functions/_lib/download-headers';
import { publicSummary } from '../../functions/_lib/account-summary';
import { COOKIE_NAME } from '../../functions/_lib/session-cookie';

const SESSION = 'sessionTOKEN0123456789_-abcdef';
const SECRET = 'aura-test-secret-0123456789abcdefghijklmnop'; // pragma: allowlist secret
const BASE = 'https://actionaura.me';

const env = (over: Record<string, unknown> = {}) => ({
  ACCOUNTS_ENABLED: '1', OWNER_ACCOUNT_API_URL: 'https://owner.test/api/website/v1/account', OWNER_ACCOUNT_KEY_ID: 'k1',
  OWNER_ACCOUNT_SECRET: SECRET, ALLOW_NO_TURNSTILE: '1', ...over,
}) as any;

function req(path: string, body: unknown, over: { headers?: Record<string, string | undefined>; method?: string; cookie?: string | null; ip?: string | null } = {}): Request {
  const h: Record<string, string> = {
    'content-type': 'application/json', origin: BASE, 'sec-fetch-site': 'same-origin', 'x-aura-account': '1',
    'user-agent': 'test-agent', ...(over.ip === null ? {} : { 'cf-connecting-ip': over.ip ?? '203.0.113.5' }),
  };
  for (const [k, v] of Object.entries(over.headers ?? {})) { if (v === undefined) delete h[k]; else h[k] = v; }
  if (over.cookie !== null) h.cookie = `${COOKIE_NAME}=${over.cookie ?? SESSION}`;
  return new Request(BASE + path, { method: over.method ?? 'POST', headers: h, body: over.method === 'GET' ? undefined : JSON.stringify(body) });
}

interface OwnerCall { url: string; sig: string | null; body: any }
let calls: OwnerCall[];
let ownerReply: (url: string, body: any) => Response;

beforeEach(() => {
  calls = [];
  ownerReply = () => new Response('{}', { status: 200 });
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(String(init.body)) : {};
    calls.push({ url, sig: (init.headers as Record<string, string>)['X-Aura-Site-Signature'] ?? null, body });
    return ownerReply(url, body);
  }));
});
afterEach(() => vi.unstubAllGlobals());

const jsonRes = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });
const call = (h: any, r: Request, e = env()) => h({ request: r, env: e });

describe('feature flag', () => {
  it.each([undefined, '', '0', 'false', 'off'])('flag %j: every endpoint is a plain 404 and Owner is never contacted', async (flag) => {
    const e = env({ ACCOUNTS_ENABLED: flag });
    for (const [h, p] of [[signIn, '/api/account/sign-in'], [signOut, '/api/account/sign-out'], [summary, '/api/account/summary'],
      [forgot, '/api/account/forgot'], [setPassword, '/api/account/set-password'], [changePassword, '/api/account/change-password'],
      [authorize, '/api/account/downloads/authorize']] as const) {
      const res = await call(h, req(p, { email: 'a@b.co', password: 'x' }), e);
      expect(res.status, p).toBe(404);
    }
    expect((await call(fetchDownload, req('/api/account/downloads/fetch?t=' + 'a'.repeat(30), null, { method: 'GET' }), e)).status).toBe(404);
    expect(calls).toHaveLength(0);
  });

  it('the page middleware answers 404 when off and never touches the static page', async () => {
    const next = vi.fn(async () => new Response('<html>'));
    expect((await accountPagesMiddleware({ env: env({ ACCOUNTS_ENABLED: '0' }), next })).status).toBe(404);
    expect(next).not.toHaveBeenCalled();
  });

  it('the page middleware, when on, passes the page through as no-store and noindex', async () => {
    const res = await accountPagesMiddleware({ env: env(), next: async () => new Response('<html>', { headers: { 'cache-control': 'public, max-age=600' } }) });
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(res.headers.get('x-robots-tag')).toContain('noindex');
  });

  it('non-POST is a 404, not a 405 that confirms the route', async () => {
    expect((await call(signIn, req('/api/account/sign-in', null, { method: 'GET' }))).status).toBe(404);
  });
});

describe('gate: CSRF, config, client address', () => {
  it('refuses a cross-origin post before anything is sent to Owner', async () => {
    const res = await call(signIn, req('/api/account/sign-in', { email: 'a@b.co', password: 'p' }, { headers: { origin: 'https://evil.example' } }));
    expect(res.status).toBe(403);
    expect(calls).toHaveLength(0);
  });

  it('answers 503 (not a guess) when the Owner secret is missing', async () => {
    const res = await call(signIn, req('/api/account/sign-in', { email: 'a@b.co', password: 'p' }), env({ OWNER_ACCOUNT_SECRET: undefined }));
    expect(res.status).toBe(503);
    expect(calls).toHaveLength(0);
  });

  it('refuses when Cloudflare gave no client address (Owner rate-limits by it), unless dev opts in', async () => {
    const body = { email: 'a@b.co', password: 'p' };
    expect((await call(signIn, req('/api/account/sign-in', body, { ip: null }))).status).toBe(503);
    expect(calls).toHaveLength(0);
    ownerReply = () => jsonRes(401, { ok: false, error: 'invalid_credentials' });
    await call(signIn, req('/api/account/sign-in', body, { ip: null }), env({ ALLOW_NO_CLIENT_IP: '1' }));
    expect(calls[0].body.client_ip).toBe('127.0.0.1');
  });

  it('rejects malformed and oversized bodies', async () => {
    const bad = new Request(BASE + '/api/account/sign-in', { method: 'POST', body: '{nope', headers: req('/x', {}).headers });
    expect((await call(signIn, bad)).status).toBe(400);
    const big = req('/api/account/sign-in', { email: 'a@b.co', password: 'x'.repeat(5000) });
    expect((await call(signIn, big)).status).toBe(413);
  });
});

describe('sign-in', () => {
  const okOwner = (extra: Record<string, unknown> = {}) =>
    jsonRes(200, { ok: true, session_token: SESSION, idle_seconds: 3600, absolute_seconds: 43200,
      account: { email: 'a@shop.test', language: 'ar', status: 'ACTIVE', customer_name: 'Shop', password_must_change: true, secret_internal: 'x' }, ...extra });

  it('sets the HttpOnly cookie, hides the token from the page, forwards the signed client address, whitelists the account', async () => {
    ownerReply = () => okOwner();
    const res = await call(signIn, req('/api/account/sign-in', { email: ' a@shop.test ', password: 'secret-pass' }));
    expect(res.status).toBe(200);
    const text = await res.text();
    expect(text).not.toContain(SESSION);
    expect(text).not.toContain('secret_internal');
    expect(JSON.parse(text)).toEqual({ ok: true, account: { email: 'a@shop.test', language: 'ar', status: 'ACTIVE', customerName: 'Shop', passwordMustChange: true } });
    const cookie = res.headers.get('set-cookie')!;
    expect(cookie).toContain(`${COOKIE_NAME}=${SESSION}`);
    for (const a of ['HttpOnly', 'Secure', 'SameSite=Lax']) expect(cookie).toContain(a);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(calls[0].url).toBe('https://owner.test/api/website/v1/account/sign-in');
    expect(calls[0].body).toMatchObject({ email: 'a@shop.test', password: 'secret-pass', client_ip: '203.0.113.5', user_agent: 'test-agent' });
    expect(calls[0].sig).toMatch(/^v1,kid=k1,ts=\d+,nonce=[A-Za-z0-9_-]{24},sig=[0-9a-f]{64}$/);
  });

  it('wrong credentials and empty fields get the same single answer; no cookie', async () => {
    ownerReply = () => jsonRes(401, { ok: false, error: 'invalid_credentials' });
    const wrong = await call(signIn, req('/api/account/sign-in', { email: 'a@shop.test', password: 'bad' }));
    const empty = await call(signIn, req('/api/account/sign-in', { email: '', password: '' }));
    for (const r of [wrong, empty]) {
      expect(r.status).toBe(401);
      expect(await r.json()).toEqual({ ok: false, error: 'invalid_credentials' });
      expect(r.headers.get('set-cookie')).toBeNull();
    }
    expect(calls).toHaveLength(1); // the empty form never reached Owner
  });

  it('passes the lockout through with Retry-After', async () => {
    ownerReply = () => jsonRes(429, { ok: false, error: 'rate_limited' }, { 'retry-after': '600' });
    const res = await call(signIn, req('/api/account/sign-in', { email: 'a@shop.test', password: 'x' }));
    expect(res.status).toBe(429);
    expect(res.headers.get('retry-after')).toBe('600');
  });

  it.each([[500], [404]])('Owner %i becomes a plain 503', async (status) => {
    ownerReply = () => jsonRes(status, { ok: false });
    const res = await call(signIn, req('/api/account/sign-in', { email: 'a@shop.test', password: 'x' }));
    expect(res.status).toBe(503);
  });

  it('a session token that is not url-safe is not trusted', async () => {
    ownerReply = () => okOwner({ session_token: 'bad token; Path=/' });
    const res = await call(signIn, req('/api/account/sign-in', { email: 'a@shop.test', password: 'x' }));
    expect(res.status).toBe(503);
    expect(res.headers.get('set-cookie')).toBeNull();
  });
});

describe('summary / sign-out / session loss', () => {
  it('without a cookie: 401 and no Owner call', async () => {
    const res = await call(summary, req('/api/account/summary', {}, { cookie: null }));
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it('Owner says the session is gone: 401 and the cookie is cleared', async () => {
    ownerReply = () => jsonRes(401, { ok: false, error: 'session_invalid' });
    const res = await call(summary, req('/api/account/summary', {}));
    expect(res.status).toBe(401);
    expect(res.headers.get('set-cookie')).toContain('Max-Age=0');
  });

  it('good answer is reshaped, whitelisted, and re-issues the cookie', async () => {
    ownerReply = () => jsonRes(200, {
      ok: true, account: { email: 'a@shop.test', language: 'en', status: 'ACTIVE', customer_name: 'Shop', password_must_change: false },
      licences: [{ license_public_id: 'L1', product_code: 'RETAIL', product_name: 'Aura Retail', plan_code: 'P2', plan_name: 'Standard', branch_label: null, device_allowance: 2, internal_note: 'x',
        trial: { state: 'ACTIVE', starts_on: '2026-11-01', ends_on: '2026-11-07', days_left: 5 } }],
      seller: { name: 'Sami', phone: '+962790000000', email: 'leak@x.test' },
      downloads: [{ offer_id: 'win-1', platform: 'windows', version: '1.0.0', size_bytes: 250000000, filename: 'AuraRetail.exe', storage_path: '/secret' },
        { offer_id: 'ios-1', platform: 'ios', version: '1' }],
      storage_credentials: 'nope',
    });
    const res = await call(summary, req('/api/account/summary', { lang: 'en' }));
    const text = await res.text();
    for (const leaked of ['internal_note', 'leak@x.test', 'storage_path', 'storage_credentials', 'ios-1']) expect(text).not.toContain(leaked);
    const j = JSON.parse(text);
    expect(j.licences[0]).toMatchObject({ planName: 'Standard', deviceAllowance: 2, trial: { state: 'ACTIVE', endsOn: '2026-11-07', daysLeft: 5 } });
    expect(j.seller).toEqual({ name: 'Sami', phone: '+962790000000' });
    expect(j.downloads).toEqual([{ offerId: 'win-1', platform: 'windows', version: '1.0.0', sizeBytes: 250000000, filename: 'AuraRetail.exe' }]);
    expect(res.headers.get('set-cookie')).toContain(`${COOKIE_NAME}=${SESSION}`);
    expect(calls[0].body).toEqual({ session_token: SESSION, client_ip: '203.0.113.5', lang: 'en' });
  });

  it('publicSummary survives hostile or empty input', () => {
    expect(() => publicSummary({})).not.toThrow();
    const s = publicSummary({ licences: 'x', downloads: [null, 5, { offer_id: 3 }], seller: [], account: 7, trial: 1 }) as any;
    expect(s.licences).toEqual([]);
    expect(s.downloads).toEqual([]);
    expect(s.seller).toBeNull();
    expect(publicSummary({ licences: [{ trial: { state: 'BOGUS' } }] }).licences).toMatchObject([{ trial: { state: 'NONE' } }]);
  });

  it('sign-out clears the cookie even when Owner is down', async () => {
    ownerReply = () => jsonRes(500, {});
    const res = await call(signOut, req('/api/account/sign-out', {}));
    expect(res.status).toBe(200);
    expect(res.headers.get('set-cookie')).toContain('Max-Age=0');
    expect(calls[0].url).toMatch(/\/sign-out$/);
  });
});

describe('forgot / set-password / change-password', () => {
  it('forgot: the same 202 for any address, with the challenge checked first', async () => {
    ownerReply = () => jsonRes(202, { ok: true });
    const res = await call(forgot, req('/api/account/forgot', { email: 'nobody@x.test', lang: 'ar' }));
    expect(res.status).toBe(202);
    expect(calls[0].body).toEqual({ email: 'nobody@x.test', client_ip: '203.0.113.5', lang: 'ar' });
  });

  it('forgot: a failed Turnstile never reaches Owner; a missing secret fails closed', async () => {
    const fetchMock = vi.fn(async (url: string) => (String(url).includes('turnstile') ? jsonRes(200, { success: false }) : jsonRes(202, { ok: true })));
    vi.stubGlobal('fetch', fetchMock);
    const withSecret = env({ TURNSTILE_SECRET: 'ts', ALLOW_NO_TURNSTILE: undefined });
    expect((await call(forgot, req('/api/account/forgot', { email: 'a@b.co', turnstile: 'tok' }), withSecret)).status).toBe(403);
    expect(fetchMock.mock.calls.filter(([u]) => String(u).includes('owner.test'))).toHaveLength(0);
    expect((await call(forgot, req('/api/account/forgot', { email: 'a@b.co' }), env({ ALLOW_NO_TURNSTILE: undefined }))).status).toBe(503);
  });

  it('set-password maps token and policy failures to stable codes', async () => {
    ownerReply = () => jsonRes(400, { ok: false, error: 'token_invalid' });
    const bad = await call(setPassword, req('/api/account/set-password', { token: 't'.repeat(30), password: 'longenough-pw' }));
    expect(bad.status).toBe(400);
    expect((await bad.json()).error).toBe('token_invalid');
    ownerReply = () => jsonRes(422, { ok: false, error: 'password_policy', reason: 'too_common' });
    const weak = await call(setPassword, req('/api/account/set-password', { token: 't'.repeat(30), password: 'password123' }));
    expect(weak.status).toBe(422);
    expect(await weak.json()).toEqual({ ok: false, error: 'password_policy', reason: 'too_common' });
    ownerReply = () => jsonRes(422, { ok: false, error: 'password_policy', reason: '<script>' });
    expect((await (await call(setPassword, req('/api/account/set-password', { token: 't'.repeat(30), password: 'x' }))).json()).reason).toBe('too_common');
  });

  it('change-password: rotates the cookie to the new session token', async () => {
    const NEW = 'newSESSIONtoken0123456789_-abcd';
    ownerReply = () => jsonRes(200, { ok: true, session_token: NEW, account: { email: 'a@shop.test', language: 'en', status: 'ACTIVE', customer_name: 'S', password_must_change: false } });
    const res = await call(changePassword, req('/api/account/change-password', { currentPassword: 'temp-pass-1', newPassword: 'brand-new-pass-9' }));
    expect(res.status).toBe(200);
    expect(res.headers.get('set-cookie')).toContain(`${COOKIE_NAME}=${NEW}`);
    expect(calls[0].body).toMatchObject({ session_token: SESSION, current_password: 'temp-pass-1', new_password: 'brand-new-pass-9' });
    expect(JSON.stringify(await res.json())).not.toContain(NEW);
  });

  it('change-password: wrong current password and dead session are told apart', async () => {
    ownerReply = () => jsonRes(401, { ok: false, error: 'invalid_credentials' });
    const wrong = await call(changePassword, req('/api/account/change-password', { currentPassword: 'x', newPassword: 'y'.repeat(12) }));
    expect(wrong.status).toBe(401);
    expect((await wrong.json()).error).toBe('invalid_credentials');
    expect(wrong.headers.get('set-cookie')).toBeNull();
    ownerReply = () => jsonRes(401, { ok: false, error: 'session_invalid' });
    const gone = await call(changePassword, req('/api/account/change-password', { currentPassword: 'x', newPassword: 'y'.repeat(12) }));
    expect((await gone.json()).error).toBe('session_invalid');
    expect(gone.headers.get('set-cookie')).toContain('Max-Age=0');
  });
});

describe('downloads', () => {
  const TOKEN = 'dl_TOKEN-0123456789abcdefghij';

  it('authorize returns a link to OUR fetch route, not an Owner or storage address', async () => {
    ownerReply = () => jsonRes(200, { ok: true, token: TOKEN, expires_in_seconds: 600, max_uses: 3, storage_url: 'https://r2.example/x' });
    const res = await call(authorize, req('/api/account/downloads/authorize', { offerId: 'win-1', lang: 'ar' }));
    const text = await res.text();
    expect(text).not.toContain('r2.example');
    expect(JSON.parse(text)).toEqual({ ok: true, url: `/api/account/downloads/fetch?t=${TOKEN}&l=ar`, expiresInSeconds: 600, maxUses: 3 });
    expect(calls[0].body).toEqual({ session_token: SESSION, offer_id: 'win-1', client_ip: '203.0.113.5' });
  });

  it('authorize maps refusals', async () => {
    const run = async (status: number, body: object) => { ownerReply = () => jsonRes(status, body); return call(authorize, req('/api/account/downloads/authorize', { offerId: 'win-1' })); };
    expect((await run(403, { ok: false, error: 'password_change_required' })).status).toBe(403);
    expect((await run(400, { ok: false, error: 'offer_unavailable' })).status).toBe(404);
    expect((await run(401, { ok: false, error: 'session_invalid' })).headers.get('set-cookie')).toContain('Max-Age=0');
    expect((await call(authorize, req('/api/account/downloads/authorize', { offerId: '../../etc' }))).status).toBe(400);
  });

  it('fetch STREAMS the body through with safe headers and a signed request carrying the token', async () => {
    const chunks = [new Uint8Array(1000).fill(7), new Uint8Array(500).fill(9)];
    let pulled = 0;
    const stream = new ReadableStream<Uint8Array>({ pull(c) { if (pulled < chunks.length) c.enqueue(chunks[pulled++]); else c.close(); } });
    ownerReply = () => new Response(stream, { status: 200, headers: { 'content-type': 'text/html', 'content-disposition': 'attachment; filename="AuraRetail-1.0.0.exe"', 'content-length': '1500' } });
    const res = await call(fetchDownload, req(`/api/account/downloads/fetch?t=${TOKEN}&l=en`, null, { method: 'GET', cookie: null }));
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('application/octet-stream'); // text/html is not a file type we serve
    expect(res.headers.get('content-disposition')).toBe('attachment; filename="AuraRetail-1.0.0.exe"');
    expect(res.headers.get('x-content-type-options')).toBe('nosniff');
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(pulled, 'body must not be read before the client reads it').toBeLessThan(chunks.length + 1);
    const bytes = new Uint8Array(await res.arrayBuffer());
    expect(bytes.length).toBe(1500);
    expect(calls[0].body).toEqual({ token: TOKEN, client_ip: '203.0.113.5' });
    expect(calls[0].url).toMatch(/\/downloads\/fetch$/);
  });

  it.each([[410], [404], [401]])('fetch: Owner %i sends the visitor back to the downloads page, not an error blob', async (status) => {
    ownerReply = () => jsonRes(status, { ok: false });
    const res = await call(fetchDownload, req(`/api/account/downloads/fetch?t=${TOKEN}&l=ar`, null, { method: 'GET', cookie: null }));
    expect(res.status).toBe(303);
    expect(res.headers.get('location')).toBe('/ar/account/downloads/?e=expired');
  });

  it('fetch: a malformed token never reaches Owner', async () => {
    const res = await call(fetchDownload, req('/api/account/downloads/fetch?t=../../x', null, { method: 'GET', cookie: null }));
    expect(res.status).toBe(303);
    expect(calls).toHaveLength(0);
  });

  it('safeDownloadHeaders rejects odd filenames and non-file types (CR/LF cannot even be constructed in a Headers object)', () => {
    const odd = ['attachment; filename="../../etc/passwd"', 'attachment; filename="a.apk"; filename*=UTF-8\'\'x', 'inline; filename="a.apk"'];
    for (const d of odd) {
      const h = safeDownloadHeaders(new Headers({ 'content-type': 'application/vnd.android.package-archive', 'content-disposition': d }));
      expect(h.get('content-type')).toBe('application/vnd.android.package-archive');
      expect(h.get('content-disposition'), d).toBe('attachment');
    }
    expect(safeDownloadHeaders(new Headers({ 'content-disposition': 'attachment; filename="a b(1).apk"' })).get('content-disposition')).toBe('attachment; filename="a b(1).apk"');
    expect(safeDownloadHeaders(new Headers({ 'content-length': '12abc' })).get('content-length')).toBeNull();
  });
});
