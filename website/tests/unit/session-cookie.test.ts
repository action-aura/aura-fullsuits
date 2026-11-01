// @vitest-environment node
import { describe, it, expect } from 'vitest';
import {
  COOKIE_NAME, sessionCookie, clearedSessionCookie, readSessionToken, clampMaxAge, isPlausibleToken, DEFAULT_MAX_AGE_SECONDS,
} from '../../functions/_lib/session-cookie';

const TOKEN = 'abcdEFGH0123_-abcdEFGH0123_-ab';
const req = (cookie?: string) => new Request('https://actionaura.me/api/account/summary', { headers: cookie === undefined ? {} : { cookie } });

describe('session cookie', () => {
  it('carries every protective attribute, and the __Host- rules (Secure, Path=/, no Domain)', () => {
    const c = sessionCookie(TOKEN);
    expect(c.startsWith(`${COOKIE_NAME}=${TOKEN};`)).toBe(true);
    expect(c.startsWith('__Host-')).toBe(true);
    for (const attr of ['HttpOnly', 'Secure', 'SameSite=Lax', 'Path=/', `Max-Age=${DEFAULT_MAX_AGE_SECONDS}`]) expect(c).toContain(attr);
    expect(c.toLowerCase()).not.toContain('domain=');
  });

  it('is short lived: max-age is capped at one hour and floored at one minute', () => {
    expect(clampMaxAge(10_000_000)).toBe(3600);
    expect(clampMaxAge(5)).toBe(60);
    expect(clampMaxAge(1800)).toBe(1800);
    expect(clampMaxAge('3000')).toBe(3600);
    expect(clampMaxAge(NaN)).toBe(3600);
    expect(sessionCookie(TOKEN, 99_999)).toContain('Max-Age=3600');
  });

  it('refuses to set a malformed token (no header injection through the cookie value)', () => {
    for (const bad of ['', 'short', 'has space aaaaaaaaaaaaaaaa', 'semi;colon-aaaaaaaaaaaaaaaa', 'new\r\nSet-Cookie: x=1aaaaaaaa', 'a'.repeat(300)]) {
      expect(() => sessionCookie(bad), bad).toThrow();
    }
  });

  it('clearing uses the same name and attributes with an expiry in the past', () => {
    const c = clearedSessionCookie();
    expect(c).toContain(`${COOKIE_NAME}=;`);
    expect(c).toContain('Max-Age=0');
    for (const attr of ['HttpOnly', 'Secure', 'SameSite=Lax', 'Path=/']) expect(c).toContain(attr);
  });

  it('is read back from a Cookie header among other cookies', () => {
    expect(readSessionToken(req(`a=1; ${COOKIE_NAME}=${TOKEN}; b=2`))).toBe(TOKEN);
    expect(readSessionToken(req())).toBeNull();
    expect(readSessionToken(req('a=1'))).toBeNull();
  });

  it('ignores a look-alike name and a junk value', () => {
    expect(readSessionToken(req(`aura_acct=${TOKEN}`))).toBeNull();
    expect(readSessionToken(req(`x${COOKIE_NAME}=${TOKEN}`))).toBeNull();
    expect(readSessionToken(req(`${COOKIE_NAME}=not a token`))).toBeNull();
    expect(readSessionToken(req(`${COOKIE_NAME}=`))).toBeNull();
    expect(isPlausibleToken(TOKEN)).toBe(true);
  });
});
