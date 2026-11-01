// Cloudflare Turnstile check for the account forms that anonymous visitors can hit (forgot password).
// Same policy as /api/demo: fail closed. A missing secret is a configuration error, never a silent pass,
// unless ALLOW_NO_TURNSTILE=1 is set explicitly (local dev and the test stack).
import type { Env } from '../env';

const TURNSTILE_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';

export type TurnstileResult = 'ok' | 'failed' | 'config';

export async function verifyTurnstile(env: Env, token: string, ip: string, fetchImpl: typeof fetch = fetch): Promise<TurnstileResult> {
  if (!env.TURNSTILE_SECRET) return env.ALLOW_NO_TURNSTILE === '1' ? 'ok' : 'config';
  try {
    const res = await fetchImpl(TURNSTILE_URL, {
      method: 'POST',
      body: new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token, remoteip: ip }),
    });
    return ((await res.json()) as { success?: boolean }).success === true ? 'ok' : 'failed';
  } catch {
    return 'failed';
  }
}
