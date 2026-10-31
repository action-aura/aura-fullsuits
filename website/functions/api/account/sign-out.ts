// POST /api/account/sign-out
// The cookie is cleared whatever Owner says: a visitor who pressed "Sign out" must not stay signed in in the
// browser because Owner was briefly unreachable. (Owner's copy of the session then just expires on its own.)
import { callOwner, clearedSessionCookie, gated, json, postOnly } from '../../_lib/account-gate';

export const onRequest = postOnly(gated(async ({ env, ip, sessionToken }) => {
  if (sessionToken) await callOwner(env, 'sign-out', { session_token: sessionToken, client_ip: ip });
  return json({ ok: true }, 200, {}, [clearedSessionCookie()]);
}));
