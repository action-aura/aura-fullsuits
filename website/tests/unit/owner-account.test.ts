// @vitest-environment node
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  sign, sha256Hex, canonicalString, newNonce, buildSignedRequest, ownerConfig, signatureHeaderValue, callOwner, SIGNATURE_HEADER,
} from '../../functions/_lib/owner-account';

interface Vector { name: string; secret: string; kid: string; method: string; path: string; ts: string; nonce: string; body: string; body_sha256: string; sig: string; header: string }
// Produced by Owner's own Python signer and re-checked against it by tools/check_signature_vectors.py.
const VECTORS: Vector[] = JSON.parse(readFileSync(new URL('./fixtures/signature-vectors.json', import.meta.url), 'utf-8'));

// The documented known-answer vector (docs/owner/customer-accounts/signature-v1.md). Written out here in full,
// not read from the fixture, so a regenerated fixture cannot silently move this pin.
const KAT = {
  secret: 'aura-kat-secret-0123456789abcdefghijklmnopqrstuv', // pragma: allowlist secret
  path: '/api/website/v1/account/sign-in', ts: '1790000000', nonce: 'kat-nonce-0123456789ab',
  body: '{"email":"owner@haddad.example","password":"Tr0ub4dor-and-3-horses","client_ip":"203.0.113.5","lang":"en"}',
  bodyHash: '9c16876004ff573925af9b456c4f2c01da3eb1c3a9cc51bb0eb61109bba27448',
  sig: '729cdd1adc289d537d1bd21a5fd2844adcb1ab6e5fde59f8b669623a5e19bb0a',
};

describe('HMAC signing, byte for byte with Owner', () => {
  it('reproduces the documented known-answer vector', async () => {
    expect(await sha256Hex(KAT.body)).toBe(KAT.bodyHash);
    expect(await sign(KAT.secret, 'POST', KAT.path, KAT.ts, KAT.nonce, KAT.body)).toBe(KAT.sig);
    expect(signatureHeaderValue('k1', KAT.ts, KAT.nonce, KAT.sig)).toBe(
      'v1,kid=k1,ts=1790000000,nonce=kat-nonce-0123456789ab,sig=' + KAT.sig,
    );
  });

  it('canonical string is the six lines joined by single LF, upper-case method, no trailing newline', () => {
    expect(canonicalString('post', '/p', '1', 'n', 'h')).toBe('v1\nPOST\n/p\n1\nn\nh');
  });

  it.each(VECTORS.map((v) => [v.name, v] as const))('matches Owner on: %s', async (_n, v) => {
    expect(await sha256Hex(v.body)).toBe(v.body_sha256);
    const sig = await sign(v.secret, v.method, v.path, v.ts, v.nonce, v.body);
    expect(sig).toBe(v.sig);
    expect(signatureHeaderValue(v.kid, v.ts, v.nonce, sig)).toBe(v.header);
  });

  it('every part of the request changes the signature (the check can fail)', async () => {
    const base = await sign(KAT.secret, 'POST', KAT.path, KAT.ts, KAT.nonce, KAT.body);
    const variants = await Promise.all([
      sign(KAT.secret, 'POST', KAT.path, KAT.ts, KAT.nonce, KAT.body + ' '),
      sign(KAT.secret, 'POST', KAT.path + 'x', KAT.ts, KAT.nonce, KAT.body),
      sign(KAT.secret, 'GET', KAT.path, KAT.ts, KAT.nonce, KAT.body),
      sign(KAT.secret, 'POST', KAT.path, '1790000001', KAT.nonce, KAT.body),
      sign(KAT.secret, 'POST', KAT.path, KAT.ts, 'kat-nonce-0123456789ac', KAT.body),
      sign(KAT.secret + 'x', 'POST', KAT.path, KAT.ts, KAT.nonce, KAT.body),
    ]);
    for (const v of variants) expect(v).not.toBe(base);
    expect(new Set(variants).size).toBe(variants.length);
  });
});

describe('buildSignedRequest', () => {
  const cfg = { baseUrl: 'https://owner.example/api/website/v1/account', kid: 'k1', secret: KAT.secret };

  it('signs the exact body it sends, with the path Owner will see', async () => {
    const req = await buildSignedRequest(cfg, 'sign-in', JSON.parse(KAT.body), { now: 1790000000, nonce: KAT.nonce });
    expect(req.url).toBe('https://owner.example/api/website/v1/account/sign-in');
    expect(req.init.body).toBe(KAT.body);
    const headers = req.init.headers as Record<string, string>;
    expect(headers[SIGNATURE_HEADER]).toBe('v1,kid=k1,ts=1790000000,nonce=kat-nonce-0123456789ab,sig=' + KAT.sig);
  });

  it('an action with a slash still signs the full path', async () => {
    const req = await buildSignedRequest(cfg, 'password/change', {}, { now: 1790000000, nonce: KAT.nonce });
    const expected = await sign(KAT.secret, 'POST', '/api/website/v1/account/password/change', '1790000000', KAT.nonce, '{}');
    expect((req.init.headers as Record<string, string>)[SIGNATURE_HEADER]).toContain('sig=' + expected);
  });

  it('header fields satisfy the shapes Owner parses (kid, ts, nonce 16-64, sig 64 hex)', async () => {
    const req = await buildSignedRequest(cfg, 'me', {});
    const h = (req.init.headers as Record<string, string>)[SIGNATURE_HEADER];
    const m = /^v1,kid=([A-Za-z0-9_.-]{1,32}),ts=([0-9]{1,12}),nonce=([A-Za-z0-9_-]{16,64}),sig=([0-9a-f]{64})$/.exec(h);
    expect(m, h).not.toBeNull();
    expect(Math.abs(Number(m![2]) - Date.now() / 1000)).toBeLessThan(5);
  });

  it('nonces are url-safe, long enough and never repeat', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 500; i++) {
      const n = newNonce();
      expect(n).toMatch(/^[A-Za-z0-9_-]{24}$/);
      seen.add(n);
    }
    expect(seen.size).toBe(500);
  });
});

describe('ownerConfig / callOwner failure handling', () => {
  const env = (o: Record<string, unknown> = {}) => ({ OWNER_ACCOUNT_API_URL: 'https://o.test/api/website/v1/account/', OWNER_ACCOUNT_KEY_ID: 'k1', OWNER_ACCOUNT_SECRET: KAT.secret, ...o }) as any;

  it('is null unless URL, kid and a long secret are all present (fail closed)', () => {
    expect(ownerConfig(env())).not.toBeNull();
    expect(ownerConfig(env({ OWNER_ACCOUNT_SECRET: 'short' }))).toBeNull();
    expect(ownerConfig(env({ OWNER_ACCOUNT_SECRET: undefined }))).toBeNull();
    expect(ownerConfig(env({ OWNER_ACCOUNT_KEY_ID: '' }))).toBeNull();
    expect(ownerConfig(env({ OWNER_ACCOUNT_API_URL: '' }))).toBeNull();
    expect(ownerConfig(env())!.baseUrl).toBe('https://o.test/api/website/v1/account');
  });

  const reply = (status: number, body: unknown, headers: Record<string, string> = {}) =>
    (async () => new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers })) as unknown as typeof fetch;

  it('maps Owner answers to one of four outcomes and never throws', async () => {
    expect((await callOwner(env({ OWNER_ACCOUNT_SECRET: '' }), 'me', {}, reply(200, {}))).kind).toBe('off');
    expect((await callOwner(env(), 'me', {}, reply(404, {}))).kind).toBe('off');
    expect((await callOwner(env(), 'me', {}, reply(502, {}))).kind).toBe('unavailable');
    expect((await callOwner(env(), 'me', {}, reply(200, 'not json'))).kind).toBe('unavailable');
    expect((await callOwner(env(), 'me', {}, reply(200, [1, 2]))).kind).toBe('unavailable');
    expect((await callOwner(env(), 'me', {}, (async () => { throw new TypeError('network'); }) as unknown as typeof fetch)).kind).toBe('unavailable');
    const refused = await callOwner(env(), 'me', {}, reply(401, { ok: false, error: 'unauthorized' }));
    expect(refused).toMatchObject({ kind: 'refused', status: 401 });
    expect(await callOwner(env(), 'me', {}, reply(200, { ok: true }))).toMatchObject({ kind: 'ok', status: 200 });
  });
});
