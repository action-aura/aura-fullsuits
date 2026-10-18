// @vitest-environment node
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { onRequestPost } from '../../functions/api/demo';

const TURNSTILE_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';
const HOOK_URL = 'https://hook.test/x';

type Call = { sql: string; binds: unknown[] };

let events: string[];
let calls: Call[];
let tsOk: boolean;
let hookThrows: boolean;

function makeDB(rateCount = 0) {
  return {
    prepare(sql: string) {
      return {
        bind: (...binds: unknown[]) => ({
          run: async () => {
            calls.push({ sql, binds });
            events.push(`db:${sql}`);
            return { meta: { last_row_id: 7 } };
          },
          first: async () => {
            calls.push({ sql, binds });
            events.push(`db:${sql}`);
            return { n: rateCount };
          },
        }),
      };
    },
  };
}

function makeEnv(over: Record<string, unknown> = {}, rateCount = 0) {
  return {
    DB: makeDB(rateCount),
    TURNSTILE_SECRET: 'sec',
    IP_SALT: 'salt',
    NOTIFY_PROVIDER: 'webhook',
    NOTIFY_WEBHOOK_URL: HOOK_URL,
    ...over,
  } as any;
}

const goodFields = {
  name: 'Ahmad',
  phone: '٠٧٨٠٠٦٩٤٨٥',
  lang: 'ar',
  'cf-turnstile-response': 'tok',
  website: '',
};

function jsonReq(fields: Record<string, string> = goodFields, accept = 'application/json') {
  return new Request('https://actionaura.me/api/demo', {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept, 'CF-Connecting-IP': '1.2.3.4' },
    body: JSON.stringify(fields),
  });
}

function formReq(fields: Record<string, string> = goodFields, accept = 'text/html') {
  return new Request('https://actionaura.me/api/demo', {
    method: 'POST',
    headers: {
      'content-type': 'application/x-www-form-urlencoded',
      accept,
      'CF-Connecting-IP': '1.2.3.4',
    },
    body: new URLSearchParams(fields),
  });
}

const inserts = () => calls.filter((c) => c.sql.includes('INSERT INTO leads'));
const idxOf = (pred: (e: string) => boolean) => events.findIndex(pred);

beforeEach(() => {
  events = [];
  calls = [];
  tsOk = true;
  hookThrows = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: any) => {
      const u = typeof url === 'string' ? url : url.url ?? String(url);
      events.push(`fetch:${u}`);
      if (u === TURNSTILE_URL) return Response.json({ success: tsOk });
      if (u === HOOK_URL) {
        if (hookThrows) throw new Error('hook down');
        return new Response('ok', { status: 200 });
      }
      return new Response('unexpected', { status: 500 });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('demo function', () => {
  it('(a) valid JSON lead: 200, one INSERT, phone normalised', async () => {
    const res = await onRequestPost({ request: jsonReq(), env: makeEnv() } as any);
    expect(res.status).toBe(200);
    expect(inserts()).toHaveLength(1);
    expect(inserts()[0].binds).toContain('+962780069485');
  });

  it('(b) honeypot filled: 200, zero DB calls', async () => {
    const res = await onRequestPost({
      request: jsonReq({ ...goodFields, website: 'http://spam' }),
      env: makeEnv(),
    } as any);
    expect(res.status).toBe(200);
    expect(calls).toHaveLength(0);
  });

  it('(c) Turnstile failure: 403, no INSERT', async () => {
    tsOk = false;
    const res = await onRequestPost({ request: jsonReq(), env: makeEnv() } as any);
    expect(res.status).toBe(403);
    expect(inserts()).toHaveLength(0);
  });

  it('(d) no secret and no allow flag: 500, no INSERT', async () => {
    const res = await onRequestPost({
      request: jsonReq(),
      env: makeEnv({ TURNSTILE_SECRET: undefined }),
    } as any);
    expect(res.status).toBe(500);
    expect(inserts()).toHaveLength(0);
  });

  it('(d2) no IP_SALT: 500, no INSERT', async () => {
    const res = await onRequestPost({
      request: jsonReq(),
      env: makeEnv({ IP_SALT: undefined }),
    } as any);
    expect(res.status).toBe(500);
    expect(inserts()).toHaveLength(0);
  });

  it('(e) rate count 5: 429, no INSERT', async () => {
    const res = await onRequestPost({ request: jsonReq(), env: makeEnv({}, 5) } as any);
    expect(res.status).toBe(429);
    expect(inserts()).toHaveLength(0);
  });

  it('(f) validation error: 422 with errors.phone', async () => {
    const res = await onRequestPost({
      request: jsonReq({ ...goodFields, phone: '123' }),
      env: makeEnv(),
    } as any);
    expect(res.status).toBe(422);
    const body: any = await res.json();
    expect(body.ok).toBe(false);
    expect(body.errors.phone).toBeTruthy();
    expect(inserts()).toHaveLength(0);
  });

  it('(g) form-encoded valid, lang=ar: 303 to /ar/contact/sent/', async () => {
    const res = await onRequestPost({ request: formReq(), env: makeEnv() } as any);
    expect(res.status).toBe(303);
    expect(res.headers.get('Location')).toBe('/ar/contact/sent/');
    expect(inserts()).toHaveLength(1);
  });

  it('(h) webhook throws: still 200, UPDATE notify_error after INSERT', async () => {
    hookThrows = true;
    const res = await onRequestPost({ request: jsonReq(), env: makeEnv() } as any);
    expect(res.status).toBe(200);
    const ins = idxOf((e) => e.startsWith('db:') && e.includes('INSERT INTO leads'));
    const upd = idxOf((e) => e.startsWith('db:') && e.includes('UPDATE leads') && e.includes('notify_error'));
    const hook = idxOf((e) => e === `fetch:${HOOK_URL}`);
    expect(ins).toBeGreaterThanOrEqual(0);
    expect(hook).toBeGreaterThan(ins);
    expect(upd).toBeGreaterThan(ins);
    expect(upd).toBeGreaterThan(hook);
  });

  it('(h2) waitUntil: responds first, notify failure recorded only after the captured promise settles', async () => {
    hookThrows = true;
    const captured: Promise<unknown>[] = [];
    const waitUntil = vi.fn((p: Promise<unknown>) => {
      captured.push(p);
    });
    const res = await onRequestPost({ request: jsonReq(), env: makeEnv(), waitUntil } as any);
    expect(res.status).toBe(200);
    expect(waitUntil).toHaveBeenCalledTimes(1);
    await Promise.all(captured);
    const upd = idxOf((e) => e.startsWith('db:') && e.includes('UPDATE leads') && e.includes('notify_error'));
    expect(upd).toBeGreaterThanOrEqual(0);
    const updCall = calls.find((c) => c.sql.includes('UPDATE leads'));
    expect(updCall?.binds).toEqual([expect.any(String), 7]);
  });

  it('(i) text/plain: 415', async () => {
    const req = new Request('https://actionaura.me/api/demo', {
      method: 'POST',
      headers: { 'content-type': 'text/plain', 'CF-Connecting-IP': '1.2.3.4' },
      body: 'hello',
    });
    const res = await onRequestPost({ request: req, env: makeEnv() } as any);
    expect(res.status).toBe(415);
    expect(inserts()).toHaveLength(0);
  });

  it('(j) INSERT happens before any notify fetch', async () => {
    const res = await onRequestPost({ request: jsonReq(), env: makeEnv() } as any);
    expect(res.status).toBe(200);
    const ins = idxOf((e) => e.startsWith('db:') && e.includes('INSERT INTO leads'));
    const hook = idxOf((e) => e === `fetch:${HOOK_URL}`);
    expect(ins).toBeGreaterThanOrEqual(0);
    expect(hook).toBeGreaterThanOrEqual(0);
    expect(ins).toBeLessThan(hook);
  });

  it('(k) form body with accept json: 200 JSON, not 303', async () => {
    const res = await onRequestPost({
      request: formReq(goodFields, 'application/json'),
      env: makeEnv(),
    } as any);
    expect(res.status).toBe(200);
    const body: any = await res.json();
    expect(body.ok).toBe(true);
    expect(res.headers.get('Location')).toBeNull();
  });
});
