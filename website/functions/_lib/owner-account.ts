// Server-to-server client for the Owner customer-account API (/api/website/v1/account/*).
//
// The browser never talks to Owner. Every call is signed here with a shared secret, so
// Owner can tell "this came from the Pages Function" from "someone typed a URL".
//
//   canonical = "v1\n" + METHOD + "\n" + PATH + "\n" + ts + "\n" + nonce + "\n" + hex(sha256(body))
//   sig       = hex(HMAC-SHA256(secret, canonical))
//   header    = X-Aura-Site-Signature: v1,kid=<kid>,ts=<unix s>,nonce=<16-64 chars>,sig=<64 hex>
//
// This must match owner/app/customer_accounts/signature.py byte for byte. It is pinned two ways:
// tests/unit/owner-account.test.ts checks the known-answer vector from
// docs/owner/customer-accounts/signature-v1.md plus tests/unit/fixtures/signature-vectors.json, and
// tools/check_signature_vectors.py re-computes that same JSON file with Owner's own Python helper.
// If either side drifts, one of those goes red; nothing here is "tested by looking".
import type { Env } from '../env';

export const SIGNATURE_HEADER = 'X-Aura-Site-Signature';
export const SIGNATURE_VERSION = 'v1';
// Owner refuses anything over its MAX_REQUEST_BYTES; sign-in bodies are tiny. A hung Owner must not
// hold a Pages Function open (and a visitor waiting) for long.
export const OWNER_TIMEOUT_MS = 10_000;

const encoder = new TextEncoder();

function toHex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function sha256Hex(data: string | Uint8Array): Promise<string> {
  const bytes = typeof data === 'string' ? encoder.encode(data) : data;
  return toHex(await crypto.subtle.digest('SHA-256', bytes));
}

export function canonicalString(method: string, path: string, ts: string, nonce: string, bodyHashHex: string): string {
  return [SIGNATURE_VERSION, method.toUpperCase(), path, ts, nonce, bodyHashHex].join('\n');
}

/** Lower-case hex HMAC-SHA256 of the canonical string. `body` is the exact text that is sent on the wire. */
export async function sign(secret: string, method: string, path: string, ts: string, nonce: string, body: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const canonical = canonicalString(method, path, ts, nonce, await sha256Hex(body));
  return toHex(await crypto.subtle.sign('HMAC', key, encoder.encode(canonical)));
}

export function signatureHeaderValue(kid: string, ts: string, nonce: string, sig: string): string {
  return `${SIGNATURE_VERSION},kid=${kid},ts=${ts},nonce=${nonce},sig=${sig}`;
}

/** 24 URL-safe characters (18 random bytes). Owner accepts 16-64 of [A-Za-z0-9_-] and burns each nonce once. */
export function newNonce(): string {
  const raw = crypto.getRandomValues(new Uint8Array(18));
  let bin = '';
  for (const b of raw) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_');
}

export interface OwnerConfig { baseUrl: string; kid: string; secret: string }

/** null = the integration is not configured (fail closed: the BFF answers 503, it never guesses). */
export function ownerConfig(env: Env): OwnerConfig | null {
  const baseUrl = (env.OWNER_ACCOUNT_API_URL ?? '').trim().replace(/\/+$/, '');
  const kid = (env.OWNER_ACCOUNT_KEY_ID ?? '').trim();
  const secret = env.OWNER_ACCOUNT_SECRET ?? '';
  if (!baseUrl || !kid || secret.length < 32) return null;
  return { baseUrl, kid, secret };
}

export interface SignedRequest { url: string; init: RequestInit }

/** Build the signed POST. `now` and `nonce` are injectable so the known-answer test can pin the output. */
export async function buildSignedRequest(
  cfg: OwnerConfig,
  action: string,
  payload: Record<string, unknown>,
  opts: { now?: number; nonce?: string } = {},
): Promise<SignedRequest> {
  const url = `${cfg.baseUrl}/${action.replace(/^\/+/, '')}`;
  // PATH is the path Owner will see, with no query and no host.
  const path = new URL(url).pathname;
  const body = JSON.stringify(payload);
  const ts = String(opts.now ?? Math.floor(Date.now() / 1000));
  const nonce = opts.nonce ?? newNonce();
  const sig = await sign(cfg.secret, 'POST', path, ts, nonce, body);
  return {
    url,
    init: {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json',
        [SIGNATURE_HEADER]: signatureHeaderValue(cfg.kid, ts, nonce, sig),
      },
      body,
    },
  };
}

export type OwnerOutcome =
  | { kind: 'ok'; status: number; body: Record<string, unknown>; headers: Headers }
  | { kind: 'refused'; status: number; body: Record<string, unknown>; headers: Headers }
  // 404 from Owner means its account API is switched off (no real secret there), or the route is gone.
  | { kind: 'off' }
  // Unreachable, timed out, 5xx, or a reply that is not the JSON shape we expect.
  | { kind: 'unavailable' };

async function parseJson(res: Response): Promise<Record<string, unknown> | null> {
  try {
    const j = await res.json();
    return j && typeof j === 'object' && !Array.isArray(j) ? (j as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** Signed JSON call. Never throws; every failure is a value the handler maps to one fixed public answer. */
export async function callOwner(
  env: Env,
  action: string,
  payload: Record<string, unknown>,
  fetchImpl: typeof fetch = fetch,
): Promise<OwnerOutcome> {
  const cfg = ownerConfig(env);
  if (!cfg) return { kind: 'off' };
  let res: Response;
  try {
    const req = await buildSignedRequest(cfg, action, payload);
    res = await fetchImpl(req.url, { ...req.init, signal: AbortSignal.timeout(OWNER_TIMEOUT_MS) });
  } catch {
    return { kind: 'unavailable' };
  }
  if (res.status === 404) return { kind: 'off' };
  if (res.status >= 500) return { kind: 'unavailable' };
  const body = await parseJson(res);
  if (!body) return { kind: 'unavailable' };
  return res.ok
    ? { kind: 'ok', status: res.status, body, headers: res.headers }
    : { kind: 'refused', status: res.status, body, headers: res.headers };
}

/** Signed call whose answer is a file: the Response is handed back untouched so its body can be streamed. */
export async function callOwnerStream(
  env: Env,
  action: string,
  payload: Record<string, unknown>,
  fetchImpl: typeof fetch = fetch,
): Promise<Response | null> {
  const cfg = ownerConfig(env);
  if (!cfg) return null;
  try {
    const req = await buildSignedRequest(cfg, action, payload);
    // The timeout covers waiting for the response headers, not the whole transfer: AbortSignal.timeout
    // would cut a slow 250 MB download. So a plain controller that we clear once headers arrive.
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), OWNER_TIMEOUT_MS);
    try {
      return await fetchImpl(req.url, { ...req.init, signal: ctl.signal });
    } finally {
      clearTimeout(timer);
    }
  } catch {
    return null;
  }
}
