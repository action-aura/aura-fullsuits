// POST /api/account/downloads/authorize  {offerId}
// Asks Owner for a short-lived link (10 minutes, 3 uses) for one installer. The link points at OUR fetch route,
// which streams the file from Owner; the website stores no installer and never sees a storage address.
import { callOwner, commonFailure, DOWNLOAD_TOKEN_RE, gated, json, postOnly, rateLimited, sessionGone, str } from '../../../_lib/account-gate';

export const onRequest = postOnly(gated(async ({ env, body, ip, sessionToken }) => {
  if (!sessionToken) return sessionGone();
  const offerId = str(body, 'offerId', 64);
  if (!/^[A-Za-z0-9_.-]{1,64}$/.test(offerId)) return json({ ok: false, error: 'invalid_request' }, 400);
  const lang = body.lang === 'ar' ? 'ar' : 'en';
  const out = await callOwner(env, 'downloads/authorize', { session_token: sessionToken, offer_id: offerId, client_ip: ip });
  if (out.kind === 'off' || out.kind === 'unavailable') return commonFailure(out);
  if (out.kind === 'refused') {
    if (out.status === 429) return rateLimited(out);
    if (out.status === 401) return sessionGone();
    // Owner refuses downloads until the temporary password has been replaced.
    if (out.status === 403) return json({ ok: false, error: 'password_change_required' }, 403);
    if (out.status === 400 || out.status === 410) return json({ ok: false, error: 'offer_unavailable' }, 404);
    return json({ ok: false, error: 'unavailable' }, 503);
  }
  const token = out.kind === 'ok' ? out.body.token : null;
  if (typeof token !== 'string' || !DOWNLOAD_TOKEN_RE.test(token)) return json({ ok: false, error: 'unavailable' }, 503);
  const seconds = typeof out.body.expires_in_seconds === 'number' ? out.body.expires_in_seconds : 600;
  return json({
    ok: true,
    url: `/api/account/downloads/fetch?t=${encodeURIComponent(token)}&l=${lang}`,
    expiresInSeconds: Math.min(3600, Math.max(0, Math.floor(seconds))),
    maxUses: typeof out.body.max_uses === 'number' ? out.body.max_uses : 3,
  });
}));
