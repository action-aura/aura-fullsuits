// CSRF guard for every state-changing account call (every POST).
//
// The session cookie is SameSite=Lax, which already stops cross-site POSTs in current browsers. This is
// the second, independent layer, so one browser quirk cannot be the only thing standing between a
// visitor and a forged "change password" or "sign out":
//   1. Content-Type must be application/json. A cross-site <form> cannot send that without a CORS
//      preflight, and we answer no preflight.
//   2. Origin must be present and equal this site's own origin. Origin is set by the browser and page
//      script cannot forge it; a missing Origin is refused (not waved through) because every browser
//      sends it on a fetch POST.
//   3. Sec-Fetch-Site, when the browser sends it, must say same-origin.
//   4. The custom header X-Aura-Account must be present: only our own script adds it.
export const CSRF_HEADER = 'x-aura-account';

export type CsrfVerdict = { ok: true } | { ok: false; reason: 'content_type' | 'origin' | 'fetch_site' | 'header' };

export function checkCsrf(request: Request): CsrfVerdict {
  const ctype = (request.headers.get('content-type') ?? '').toLowerCase();
  if (!ctype.startsWith('application/json')) return { ok: false, reason: 'content_type' };

  const origin = request.headers.get('origin');
  if (!origin || origin !== new URL(request.url).origin) return { ok: false, reason: 'origin' };

  const site = request.headers.get('sec-fetch-site');
  if (site !== null && site !== 'same-origin') return { ok: false, reason: 'fetch_site' };

  if (request.headers.get(CSRF_HEADER) !== '1') return { ok: false, reason: 'header' };
  return { ok: true };
}
