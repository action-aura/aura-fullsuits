import type { Env } from '../env';
import { validateLead } from '../../src/lib/lead';
import { notify } from '../_lib/notify';

const TURNSTILE_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';
const RATE_LIMIT = 5;

async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export const onRequestPost = async (ctx: { request: Request; env: Env }): Promise<Response> => {
  const { request, env } = ctx;
  const wantsJson = (request.headers.get('accept') ?? '').includes('application/json');
  const ctype = request.headers.get('content-type') ?? '';

  // 1. Only JSON or form bodies; anything else is not our client.
  let fields: Record<string, unknown>;
  try {
    if (ctype.includes('application/json')) {
      const j = await request.json();
      fields = j && typeof j === 'object' ? (j as Record<string, unknown>) : {};
    } else if (ctype.includes('application/x-www-form-urlencoded') || ctype.includes('multipart/form-data')) {
      fields = Object.fromEntries((await request.formData()).entries());
    } else {
      return Response.json({ ok: false, error: 'unsupported' }, { status: 415 });
    }
  } catch {
    return Response.json({ ok: false, error: 'badbody' }, { status: 400 });
  }

  const lang = fields.lang === 'ar' ? 'ar' : 'en';
  const fail = (status: number, body: Record<string, unknown>): Response =>
    wantsJson
      ? Response.json({ ok: false, ...body }, { status })
      : new Response(null, { status: 303, headers: { Location: `/${lang}/contact/#form-error` } });

  // 2. Honeypot: bots fill the hidden field. Answer 200 so they learn nothing; store and notify nothing.
  if (typeof fields.website === 'string' && fields.website !== '') {
    return Response.json({ ok: true });
  }

  const ip = request.headers.get('CF-Connecting-IP') ?? '';

  // 3. Turnstile. Fail closed: a missing secret is a config error, never a silent pass,
  // unless ALLOW_NO_TURNSTILE=1 is set explicitly (local dev).
  if (env.TURNSTILE_SECRET) {
    let passed = false;
    try {
      const res = await fetch(TURNSTILE_URL, {
        method: 'POST',
        body: new URLSearchParams({
          secret: env.TURNSTILE_SECRET,
          response: String(fields['cf-turnstile-response'] ?? ''),
          remoteip: ip,
        }),
      });
      passed = ((await res.json()) as { success?: boolean }).success === true;
    } catch {
      passed = false;
    }
    if (!passed) return fail(403, { error: 'challenge' });
  } else if (env.ALLOW_NO_TURNSTILE !== '1') {
    return fail(500, { error: 'config' });
  }

  // 4. Validate (also normalises the phone).
  const v = validateLead(fields);
  if (!v.ok) return fail(422, { errors: v.errors });
  const lead = v.lead;

  // 5. Rate limit per salted IP hash; raw IPs are never stored.
  const ipHash = await sha256Hex(env.IP_SALT + ip);
  const rate = (await env.DB
    .prepare("SELECT COUNT(*) AS n FROM leads WHERE ip_hash=? AND created_at > datetime('now','-10 minutes')")
    .bind(ipHash)
    .first()) as { n: number } | null;
  if ((rate?.n ?? 0) >= RATE_LIMIT) return fail(429, { error: 'rate' });

  // 6. Store first: D1 is the source of truth, so no lead is lost if notification fails.
  const ins = await env.DB
    .prepare(
      "INSERT INTO leads (created_at, name, phone, business_type, business_name, branches, message, lang, ip_hash) VALUES (datetime('now'),?,?,?,?,?,?,?,?)",
    )
    .bind(lead.name, lead.phone, lead.businessType, lead.businessName, lead.branches, lead.message, lead.lang, ipHash)
    .run();
  const id = Number((ins as { meta?: { last_row_id?: number } }).meta?.last_row_id ?? 0);

  // 7. Notify after storing; a failure is recorded on the row but the lead is safe, so still succeed.
  const n = await notify(env, lead, id);
  if (!n.ok) {
    try {
      await env.DB.prepare('UPDATE leads SET notify_error=? WHERE id=?').bind(n.error ?? 'unknown', id).run();
    } catch {
      // lead is already stored; nothing more to do
    }
  }

  // 8. Accept header (not content-type) picks the format: the JS form also sends FormData.
  return wantsJson
    ? Response.json({ ok: true })
    : new Response(null, { status: 303, headers: { Location: `/${lead.lang}/contact/sent/` } });
};
