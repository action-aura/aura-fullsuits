import type { Env } from '../env';
import type { Lead } from '../../src/lib/lead';

// Never throws: the lead is already stored in D1 before this runs, so a failed
// notification must be reported to the caller, not escalate into a failed request.
export async function notify(env: Env, lead: Lead, id: number): Promise<{ ok: boolean; error?: string }> {
  if (!env.NOTIFY_PROVIDER || env.NOTIFY_PROVIDER === 'none') return { ok: true };
  if (env.NOTIFY_PROVIDER === 'smtp') {
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
