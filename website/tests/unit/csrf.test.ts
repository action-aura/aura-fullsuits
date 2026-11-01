// @vitest-environment node
import { describe, it, expect } from 'vitest';
import { checkCsrf } from '../../functions/_lib/csrf';

const URL_ = 'https://actionaura.me/api/account/sign-in';
const good = (): Record<string, string> => ({
  'content-type': 'application/json', origin: 'https://actionaura.me', 'sec-fetch-site': 'same-origin', 'x-aura-account': '1',
});
const post = (headers: Record<string, string | undefined>, url = URL_) => {
  const h: Record<string, string> = {};
  for (const [k, v] of Object.entries(headers)) if (v !== undefined) h[k] = v;
  return new Request(url, { method: 'POST', headers: h, body: '{}' });
};

describe('CSRF guard', () => {
  it('passes a same-origin JSON post from our own script', () => {
    expect(checkCsrf(post(good()))).toEqual({ ok: true });
  });

  it('passes when the browser sends no Sec-Fetch-Site (older browsers); Origin still has to match', () => {
    expect(checkCsrf(post({ ...good(), 'sec-fetch-site': undefined }))).toEqual({ ok: true });
  });

  it.each([
    ['a form post (not JSON)', { 'content-type': 'application/x-www-form-urlencoded' }, 'content_type'],
    ['text/plain (the CORS-simple trick)', { 'content-type': 'text/plain' }, 'content_type'],
    ['no content type', { 'content-type': undefined }, 'content_type'],
    ['a foreign Origin', { origin: 'https://evil.example' }, 'origin'],
    ['a look-alike Origin', { origin: 'https://actionaura.me.evil.example' }, 'origin'],
    ['http instead of https', { origin: 'http://actionaura.me' }, 'origin'],
    ['no Origin at all', { origin: undefined }, 'origin'],
    ['Origin "null" (sandboxed iframe)', { origin: 'null' }, 'origin'],
    ['Sec-Fetch-Site cross-site', { 'sec-fetch-site': 'cross-site' }, 'fetch_site'],
    ['Sec-Fetch-Site same-site (a sibling host)', { 'sec-fetch-site': 'same-site' }, 'fetch_site'],
    ['missing custom header', { 'x-aura-account': undefined }, 'header'],
    ['wrong custom header value', { 'x-aura-account': '0' }, 'header'],
  ])('refuses %s', (_name, override, reason) => {
    expect(checkCsrf(post({ ...good(), ...override }))).toEqual({ ok: false, reason });
  });

  it('compares against the origin of the request itself (works on a preview host too)', () => {
    const preview = 'https://abc123.action-aura-website.pages.dev/api/account/sign-in';
    expect(checkCsrf(post({ ...good(), origin: 'https://abc123.action-aura-website.pages.dev' }, preview))).toEqual({ ok: true });
    expect(checkCsrf(post(good(), preview))).toEqual({ ok: false, reason: 'origin' });
  });
});
