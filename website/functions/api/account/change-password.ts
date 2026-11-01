// POST /api/account/change-password  {currentPassword, newPassword}   (needs the session cookie)
// Used for the forced change after a temporary password, and for a voluntary change. Owner ends every other
// session and hands back a fresh one, which replaces the cookie.
import { callOwner, clampMaxAge, commonFailure, gated, json, policyReason, postOnly, publicAccount, rateLimited, sessionCookie, sessionGone, str } from '../../_lib/account-gate';
import { isPlausibleToken } from '../../_lib/session-cookie';

export const onRequest = postOnly(gated(async ({ request, env, body, ip, sessionToken }) => {
  if (!sessionToken) return sessionGone();
  const currentPassword = str(body, 'currentPassword', 256);
  const newPassword = str(body, 'newPassword', 256);
  if (!currentPassword || !newPassword) return json({ ok: false, error: 'invalid_request' }, 400);
  const out = await callOwner(env, 'password/change', {
    session_token: sessionToken, current_password: currentPassword, new_password: newPassword,
    client_ip: ip, user_agent: (request.headers.get('user-agent') ?? '').slice(0, 512),
  });
  if (out.kind === 'off' || out.kind === 'unavailable') return commonFailure(out);
  if (out.kind === 'refused') {
    if (out.status === 429) return rateLimited(out);
    // Owner uses 401 for both "current password wrong" and "session gone", told apart by `error`.
    if (out.status === 401) return out.body.error === 'session_invalid' ? sessionGone() : json({ ok: false, error: 'invalid_credentials' }, 401);
    if (out.status === 422) return json({ ok: false, error: 'password_policy', reason: policyReason(out.body.reason) }, 422);
    return json({ ok: false, error: 'unavailable' }, 503);
  }
  const token = out.kind === 'ok' ? out.body.session_token : null;
  if (typeof token !== 'string' || !isPlausibleToken(token)) return json({ ok: false, error: 'unavailable' }, 503);
  return json({ ok: true, account: publicAccount(out.body.account) }, 200, {}, [sessionCookie(token, clampMaxAge(undefined))]);
}));
