// POST /api/account/summary
// One call that answers "who is signed in, what did they buy, how long is the trial, who do I pay, what can I
// download". Also the page's "am I still signed in?" check. A good answer re-issues the cookie (sliding window).
import { callOwner, commonFailure, gated, json, postOnly, rateLimited, sessionCookie, sessionGone } from '../../_lib/account-gate';
import { publicSummary } from '../../_lib/account-summary';

export const onRequest = postOnly(gated(async ({ env, body, ip, sessionToken }) => {
  if (!sessionToken) return sessionGone();
  const out = await callOwner(env, 'summary', { session_token: sessionToken, client_ip: ip, lang: body.lang === 'ar' ? 'ar' : 'en' });
  if (out.kind === 'off' || out.kind === 'unavailable') return commonFailure(out);
  if (out.kind === 'refused') {
    if (out.status === 429) return rateLimited(out);
    if (out.status === 401) return sessionGone();
    return json({ ok: false, error: 'unavailable' }, 503);
  }
  if (out.kind !== 'ok') return json({ ok: false, error: 'unavailable' }, 503);
  return json({ ok: true, ...publicSummary(out.body) }, 200, {}, [sessionCookie(sessionToken)]);
}));
