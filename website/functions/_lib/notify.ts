import type { Env } from '../env';
import type { Lead } from '../../src/lib/lead';

export type NotifyResult = { ok: boolean; error?: string };
type Provider = (env: Env, lead: Lead, id: number) => Promise<NotifyResult>;

// Owner answers in well under a second; this only bounds a hung connection so
// the waitUntil budget is never spent waiting on it.
const OWNER_TIMEOUT_MS = 10_000;

const errorText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

async function sendSmtp(env: Env, lead: Lead, id: number): Promise<NotifyResult> {
  if (!env.SMTP_HOST || !env.SMTP_USER || !env.SMTP_PASS || !env.NOTIFY_TO) return { ok: false, error: 'smtp config missing' };
  // worker-mailer writes ASCII headers verbatim, so a CR/LF in the name would
  // inject headers. lead.ts strips control characters too; this is defence in depth.
  const oneLine = (s: string) => s.replace(/[\r\n]+/g, ' ');
  const port = Number(env.SMTP_PORT || 465);
  let mailer: { close?: () => unknown } | undefined;
  try {
    const { WorkerMailer } = await import('worker-mailer');
    const connected = await WorkerMailer.connect({
      host: env.SMTP_HOST,
      port,
      secure: port === 465,
      credentials: { username: env.SMTP_USER, password: env.SMTP_PASS },
      authType: 'plain',
    });
    mailer = connected;
    await connected.send({
      from: { name: 'Action Aura website', email: env.SMTP_USER },
      to: env.NOTIFY_TO,
      subject: `Demo request #${id}: ${oneLine(lead.name)}`,
      text: [`Name: ${lead.name}`, `Phone: ${lead.phone}`, `Business: ${lead.businessType || '-'} ${lead.businessName || ''}`.trim(), `Branches: ${lead.branches || '-'}`, `Language: ${lead.lang}`, '', lead.message || ''].join('\n'),
    });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  } finally {
    try { await mailer?.close?.(); } catch { /* close must never throw */ }
  }
}

async function sendWebhook(env: Env, lead: Lead, id: number): Promise<NotifyResult> {
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

// Forwards the stored lead to the Owner CRM (POST /api/intake/website-lead).
// Owner is idempotent on lead_id, so a retried delivery never duplicates.
async function sendOwner(env: Env, lead: Lead, id: number): Promise<NotifyResult> {
  if (!env.OWNER_INTAKE_URL || !env.OWNER_INTAKE_SECRET) return { ok: false, error: 'owner config missing' };
  // The bearer secret must never travel in clear text.
  if (!env.OWNER_INTAKE_URL.startsWith('https://')) return { ok: false, error: 'owner url must be https' };
  try {
    const res = await fetch(env.OWNER_INTAKE_URL, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json',
        authorization: `Bearer ${env.OWNER_INTAKE_SECRET}`,
      },
      body: JSON.stringify({
        lead_id: String(id),
        name: lead.name,
        phone: lead.phone,
        business_type: lead.businessType,
        business_name: lead.businessName,
        branches: lead.branches,
        message: lead.message,
        lang: lead.lang,
      }),
      // Following a redirect would re-send the bearer to wherever it points.
      redirect: 'manual',
      signal: AbortSignal.timeout(OWNER_TIMEOUT_MS),
    });
    if (!res.ok) return { ok: false, error: `owner status ${res.status}` };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: errorText(e) };
  }
}

// A Map, not an object literal: "constructor" or "__proto__" must never resolve to a provider.
const PROVIDERS = new Map<string, Provider>([
  ['smtp', sendSmtp],
  ['webhook', sendWebhook],
  ['owner', sendOwner],
]);

// "smtp, owner" -> ['smtp', 'owner']: trimmed, lower-cased, de-duplicated, order
// kept; "none" is dropped, so unset or "none" still means "send nothing".
export function parseProviders(raw: string | undefined): string[] {
  const out: string[] = [];
  for (const part of (raw ?? '').split(',')) {
    const name = part.trim().toLowerCase();
    if (name && name !== 'none' && !out.includes(name)) out.push(name);
  }
  return out;
}

// Never throws: the lead is already stored in D1 before this runs, so a failed
// notification must be reported to the caller, not escalate into a failed request.
// Providers run in parallel and fail independently -- one failing never stops
// another -- and every failure is reported as "<provider>: <error>", joined by "; ".
export async function notify(env: Env, lead: Lead, id: number): Promise<NotifyResult> {
  const names = parseProviders(env.NOTIFY_PROVIDER);
  if (names.length === 0) return { ok: true };
  const results = await Promise.all(
    names.map(async (name): Promise<[string, NotifyResult]> => {
      const run = PROVIDERS.get(name);
      if (!run) return [name, { ok: false, error: 'unknown provider' }];
      try {
        return [name, await run(env, lead, id)];
      } catch (e) {
        return [name, { ok: false, error: errorText(e) }];
      }
    }),
  );
  const errors = results.filter(([, r]) => !r.ok).map(([name, r]) => `${name}: ${r.error ?? 'unknown'}`);
  return errors.length ? { ok: false, error: errors.join('; ') } : { ok: true };
}
