import type { Env } from '../env';
import type { Lead } from '../../src/lib/lead';

// Never throws: the lead is already stored in D1 before this runs, so a failed
// notification must be reported to the caller, not escalate into a failed request.
export async function notify(env: Env, lead: Lead, id: number): Promise<{ ok: boolean; error?: string }> {
  if (!env.NOTIFY_PROVIDER || env.NOTIFY_PROVIDER === 'none') return { ok: true };
  if (env.NOTIFY_PROVIDER !== 'webhook') return { ok: false, error: 'unknown provider' };
  if (!env.NOTIFY_WEBHOOK_URL) return { ok: false, error: 'webhook url missing' };
  try {
    const res = await fetch(env.NOTIFY_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ id, ...lead }),
    });
    if (!res.ok) return { ok: false, error: `webhook status ${res.status}` };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}
