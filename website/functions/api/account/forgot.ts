// POST /api/account/forgot  {email, lang, turnstile}
// Always the same 202 whatever the address was (Owner makes the same promise), so this cannot be used to
// find out who has an account. Turnstile first: an anonymous mail trigger needs a bot check.
import { callOwner, commonFailure, gated, json, postOnly, rateLimited, str } from '../../_lib/account-gate';
import { verifyTurnstile } from '../../_lib/turnstile';

export const onRequest = postOnly(gated(async ({ env, body, ip }) => {
  const email = str(body, 'email', 320).trim();
  if (!email) return json({ ok: false, error: 'invalid_request' }, 400);
  const challenge = await verifyTurnstile(env, str(body, 'turnstile', 2048), ip);
  if (challenge === 'config') return json({ ok: false, error: 'unavailable' }, 503);
  if (challenge === 'failed') return json({ ok: false, error: 'challenge' }, 403);

  const out = await callOwner(env, 'password/forgot', { email, client_ip: ip, lang: body.lang === 'ar' ? 'ar' : 'en' });
  const down = commonFailure(out);
  if (down) return down;
  if (out.kind === 'refused' && out.status === 429) return rateLimited(out);
  if (out.kind !== 'ok') return json({ ok: false, error: 'unavailable' }, 503);
  return json({ ok: true }, 202);
}));
