// POST /api/account/set-password  {token, password}
// Finishes an invitation or reset link. The token came from the link's #fragment (never sent to any server
// until now). No session is created: the person signs in with the new password afterwards.
import { callOwner, commonFailure, gated, json, policyReason, postOnly, rateLimited, str } from '../../_lib/account-gate';

export const onRequest = postOnly(gated(async ({ env, body, ip }) => {
  const token = str(body, 'token', 256);
  const password = str(body, 'password', 256);
  if (!token || !password) return json({ ok: false, error: 'invalid_request' }, 400);
  const out = await callOwner(env, 'password/set', { token, password, client_ip: ip });
  if (out.kind === 'off' || out.kind === 'unavailable') return commonFailure(out);
  if (out.kind === 'refused') {
    if (out.status === 429) return rateLimited(out);
    if (out.status === 400) return json({ ok: false, error: 'token_invalid' }, 400);
    if (out.status === 422) return json({ ok: false, error: 'password_policy', reason: policyReason(out.body.reason) }, 422);
    return json({ ok: false, error: 'unavailable' }, 503);
  }
  return json({ ok: true });
}));
