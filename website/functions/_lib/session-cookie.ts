// The website's account session cookie. Its value is the Owner session token (Owner stores only a
// hash of it); the cookie is the ONLY place the browser holds it, and script can never read it.
//
// Why `__Host-`: owner.actionaura.me is the same site as actionaura.me, so a sibling host could try to
// plant a cookie called aura_acct. The `__Host-` prefix makes browsers refuse any such cookie unless it
// was set Secure, with Path=/ and no Domain, by this exact host.
export const COOKIE_NAME = '__Host-aura_acct';
// Owner's idle timeout is 60 minutes and its absolute limit is 12 hours; Owner is the judge of both.
// The browser copy just must not outlive the idle window by much, so a lost laptop does not keep a
// token for days. Each successful call re-issues it (a sliding window).
export const DEFAULT_MAX_AGE_SECONDS = 3600;
export const MIN_MAX_AGE_SECONDS = 60;

// Owner session tokens are url-safe random strings. Anything else is junk (or an attack), not a session.
const TOKEN_RE = /^[A-Za-z0-9_-]{16,256}$/;

export function isPlausibleToken(value: string): boolean {
  return TOKEN_RE.test(value);
}

export function readSessionToken(request: Request): string | null {
  const header = request.headers.get('cookie');
  if (!header) return null;
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() !== COOKIE_NAME) continue;
    const value = part.slice(eq + 1).trim();
    return isPlausibleToken(value) ? value : null;
  }
  return null;
}

export function clampMaxAge(seconds: unknown): number {
  const n = typeof seconds === 'number' && Number.isFinite(seconds) ? Math.floor(seconds) : DEFAULT_MAX_AGE_SECONDS;
  return Math.min(DEFAULT_MAX_AGE_SECONDS, Math.max(MIN_MAX_AGE_SECONDS, n));
}

/** Set-Cookie header value. HttpOnly + Secure + SameSite=Lax + Path=/ (and no Domain, as `__Host-` demands). */
export function sessionCookie(token: string, maxAgeSeconds: number = DEFAULT_MAX_AGE_SECONDS): string {
  if (!isPlausibleToken(token)) throw new Error('refusing to set a malformed session token');
  return `${COOKIE_NAME}=${token}; Max-Age=${clampMaxAge(maxAgeSeconds)}; Path=/; Secure; HttpOnly; SameSite=Lax`;
}

/** Same name, attributes and an expiry in the past: the browser deletes it. */
export function clearedSessionCookie(): string {
  return `${COOKIE_NAME}=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/; Secure; HttpOnly; SameSite=Lax`;
}
