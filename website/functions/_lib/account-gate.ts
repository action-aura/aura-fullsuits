// Shared front door for every /api/account/* handler. One place decides, in this order:
//   1. Feature flag off -> 404, indistinguishable from a route that does not exist.
//   2. Wrong method -> 404 (never a 405 that confirms the route).
//   3. POST without a passing CSRF check -> 403.
//   4. JSON body parsed and size-bounded.
// Responses are always `Cache-Control: no-store`: they carry session state.
import type { Env } from '../env';
import { checkCsrf } from './csrf';
import { callOwner, ownerConfig, type OwnerOutcome } from './owner-account';
import { clearedSessionCookie, clampMaxAge, readSessionToken, sessionCookie } from './session-cookie';

export function accountsEnabled(env: Env): boolean {
  const v = (env.ACCOUNTS_ENABLED ?? '').trim().toLowerCase();
  return v === '1' || v === 'true';
}

const MAX_BODY_BYTES = 4096;

export function json(body: Record<string, unknown>, status = 200, extra: Record<string, string> = {}, cookies: string[] = []): Response {
  const headers = new Headers({ 'content-type': 'application/json', 'cache-control': 'no-store', ...extra });
  for (const c of cookies) headers.append('set-cookie', c);
  return new Response(JSON.stringify(body), { status, headers });
}

export function notFound(): Response {
  return new Response('Not found', { status: 404, headers: { 'cache-control': 'no-store' } });
}

/** The visitor's address as Cloudflare saw it. Owner uses it for lockouts, so a guess would be worse than a refusal. */
export function clientIp(request: Request, env: Env): string | null {
  const ip = (request.headers.get('CF-Connecting-IP') ?? '').trim();
  if (ip) return ip;
  return env.ALLOW_NO_CLIENT_IP === '1' ? '127.0.0.1' : null;
}

export interface GateContext {
  request: Request;
  env: Env;
  body: Record<string, unknown>;
  ip: string;
  sessionToken: string | null;
}

type Handler = (g: GateContext) => Promise<Response>;

/** Wraps a POST handler. */
export function gated(handler: Handler) {
  return async (ctx: { request: Request; env: Env }): Promise<Response> => {
    const { request, env } = ctx;
    if (!accountsEnabled(env)) return notFound();
    const csrf = checkCsrf(request);
    if (!csrf.ok) return json({ ok: false, error: 'forbidden' }, 403);
    if (!ownerConfig(env)) return json({ ok: false, error: 'unavailable' }, 503);

    const raw = await request.text();
    if (raw.length > MAX_BODY_BYTES) return json({ ok: false, error: 'too_large' }, 413);
    let body: Record<string, unknown> = {};
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return json({ ok: false, error: 'invalid_request' }, 400);
        body = parsed as Record<string, unknown>;
      } catch {
        return json({ ok: false, error: 'invalid_request' }, 400);
      }
    }
    const ip = clientIp(request, env);
    if (!ip) return json({ ok: false, error: 'unavailable' }, 503);
    return handler({ request, env, body, ip, sessionToken: readSessionToken(request) });
  };
}

/** Only POST is routed; anything else is a 404. Exported as onRequest by handlers that use `gated`. */
export function postOnly(post: (ctx: { request: Request; env: Env }) => Promise<Response>) {
  return async (ctx: { request: Request; env: Env }): Promise<Response> => {
    if (ctx.request.method !== 'POST') return notFound();
    return post(ctx);
  };
}

export function str(body: Record<string, unknown>, key: string, max: number): string {
  const v = body[key];
  return typeof v === 'string' ? v.slice(0, max) : '';
}

/** Owner failures that every endpoint answers the same way. Returns null when the outcome is a normal ok/refused. */
export function commonFailure(out: OwnerOutcome): Response | null {
  if (out.kind === 'off' || out.kind === 'unavailable') return json({ ok: false, error: 'unavailable' }, 503);
  return null;
}

export function rateLimited(out: Extract<OwnerOutcome, { kind: 'refused' }>): Response {
  const retry = out.headers.get('retry-after');
  const extra: Record<string, string> = retry && /^\d{1,6}$/.test(retry) ? { 'retry-after': retry } : {};
  return json({ ok: false, error: 'rate_limited' }, 429, extra);
}

/** Owner said the session is gone: tell the browser and clear the cookie so it stops sending it. */
export function sessionGone(): Response {
  return json({ ok: false, error: 'session_invalid' }, 401, {}, [clearedSessionCookie()]);
}

/** Shape of a one-time download token (Owner issues url-safe random strings). */
export const DOWNLOAD_TOKEN_RE = /^[A-Za-z0-9_-]{16,128}$/;

export function policyReason(v: unknown): string {
  return v === 'too_short' || v === 'too_long' || v === 'too_common' || v === 'contains_personal' || v === 'same_as_old' ? v : 'generic';
}

/** Owner account view the browser may see. Whitelisted field by field: nothing unexpected is forwarded. */
export function publicAccount(raw: unknown): Record<string, unknown> {
  const a = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  return {
    email: typeof a.email === 'string' ? a.email : '',
    language: a.language === 'ar' ? 'ar' : 'en',
    status: typeof a.status === 'string' ? a.status : '',
    customerName: typeof a.customer_name === 'string' ? a.customer_name : null,
    passwordMustChange: a.password_must_change === true,
  };
}

export { callOwner, clampMaxAge, sessionCookie, clearedSessionCookie };
