// POST /api/account/sign-in  {email, password}
// Owner checks the password (Argon2, lockouts, no account enumeration). On success the Owner session token goes
// into an HttpOnly cookie and NEVER into the JSON the page script sees.
import { callOwner, clampMaxAge, commonFailure, gated, json, postOnly, publicAccount, rateLimited, sessionCookie, str } from '../../_lib/account-gate';
import { isPlausibleToken } from '../../_lib/session-cookie';

export const onRequest = postOnly(gated(async ({ request, env, body, ip }) => {
  const email = str(body, 'email', 320).trim();
  const password = str(body, 'password', 256);
  // One public answer for "empty", "wrong" and "no such account" alike.
  if (!email || !password) return json({ ok: false, error: 'invalid_credentials' }, 401);

  const out = await callOwner(env, 'sign-in', {
    email, password, client_ip: ip, user_agent: (request.headers.get('user-agent') ?? '').slice(0, 512),
  });
  if (out.kind === 'off' || out.kind === 'unavailable') return commonFailure(out);
  if (out.kind === 'refused') {
    if (out.status === 429) return rateLimited(out);
    if (out.status === 401) return json({ ok: false, error: 'invalid_credentials' }, 401);
    return json({ ok: false, error: 'unavailable' }, 503);
  }
  if (out.kind !== 'ok') return json({ ok: false, error: 'unavailable' }, 503);
  const token = out.body.session_token;
  if (typeof token !== 'string' || !isPlausibleToken(token)) return json({ ok: false, error: 'unavailable' }, 503);
  return json({ ok: true, account: publicAccount(out.body.account) }, 200, {}, [
    sessionCookie(token, clampMaxAge(out.body.idle_seconds)),
  ]);
}));
