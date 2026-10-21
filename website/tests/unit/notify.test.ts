import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
const send = vi.fn();
const close = vi.fn();
const connect = vi.fn(async (_opts?: any) => ({ send, close }));
vi.mock('worker-mailer', () => ({ WorkerMailer: { connect } }));
import { notify } from '../../functions/_lib/notify';

const lead = { name: 'Sami', phone: '+962790000000', businessType: 'shop', businessName: '', branches: '', message: '', lang: 'ar' } as any;
const smtpEnv = { NOTIFY_PROVIDER: 'smtp', SMTP_HOST: 'mail.privateemail.com', SMTP_PORT: '465', SMTP_USER: 'info@actionaura.me', SMTP_PASS: 'x', NOTIFY_TO: 'info@actionaura.me' } as any;

beforeEach(() => { send.mockReset(); connect.mockClear(); close.mockReset(); });

describe('notify smtp', () => {
  it('sends one mail to NOTIFY_TO with the lead in the body', async () => {
    send.mockResolvedValue(undefined);
    expect(await notify(smtpEnv, lead, 7)).toEqual({ ok: true });
    expect(connect).toHaveBeenCalledWith(expect.objectContaining({ host: 'mail.privateemail.com', port: 465, secure: true, credentials: { username: 'info@actionaura.me', password: 'x' } }));
    const msg = send.mock.calls[0][0];
    expect(msg.to).toBe('info@actionaura.me');
    expect(msg.subject).toContain('#7');
    expect(msg.text).toContain('+962790000000');
  });
  it('keeps CR/LF in the name out of the subject (header injection)', async () => {
    send.mockResolvedValue(undefined);
    await notify(smtpEnv, { ...lead, name: 'Sami\r\nReply-To: x@evil.io' }, 7);
    const msg = send.mock.calls[0][0];
    expect(msg.subject).not.toMatch(/[\r\n]/);
    expect(msg.subject).toContain('Reply-To: x@evil.io');
  });
  it('uses STARTTLS-style secure:false on port 587', async () => {
    send.mockResolvedValue(undefined);
    await notify({ ...smtpEnv, SMTP_PORT: '587' }, lead, 7);
    expect(connect).toHaveBeenCalledWith(expect.objectContaining({ port: 587, secure: false }));
  });
  it('closes the mailer once after a successful send', async () => {
    send.mockResolvedValue(undefined);
    await notify(smtpEnv, lead, 7);
    expect(close).toHaveBeenCalledTimes(1);
  });
  it('reports, never throws, when the SMTP server fails', async () => {
    send.mockRejectedValue(new Error('auth failed'));
    expect(await notify(smtpEnv, lead, 7)).toEqual({ ok: false, error: 'smtp: auth failed' });
  });
  it('refuses to send with missing credentials', async () => {
    expect(await notify({ ...smtpEnv, SMTP_PASS: '' }, lead, 7)).toEqual({ ok: false, error: 'smtp: smtp config missing' });
    expect(connect).not.toHaveBeenCalled();
  });
  it('provider none still sends nothing', async () => {
    expect(await notify({ NOTIFY_PROVIDER: 'none' } as any, lead, 7)).toEqual({ ok: true });
    expect(connect).not.toHaveBeenCalled();
  });
});

describe('notify owner + multi-provider', () => {
  const OWNER_URL = 'https://owner.actionaura.me/api/intake/website-lead';
  const SECRET = 's'.repeat(48);
  const ownerEnv = { NOTIFY_PROVIDER: 'owner', OWNER_INTAKE_URL: OWNER_URL, OWNER_INTAKE_SECRET: SECRET } as any;
  const fullLead = { name: 'Sami', phone: '+962790000000', businessType: 'shop', businessName: 'Haddad Market', branches: '2-3', message: 'hi', lang: 'ar' } as any;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async (_url?: any, _init?: any) => new Response('{"ok":true}', { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts the snake_case contract once with bearer, manual redirect and D1 id', async () => {
    expect(await notify(ownerEnv, fullLead, 42)).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(OWNER_URL);
    expect(init.method).toBe('POST');
    expect(init.headers.authorization).toBe('Bearer ' + SECRET);
    expect(init.redirect).toBe('manual');
    expect(JSON.parse(init.body)).toEqual({
      lead_id: '42', name: 'Sami', phone: '+962790000000', business_type: 'shop',
      business_name: 'Haddad Market', branches: '2-3', message: 'hi', lang: 'ar',
    });
  });
  it('treats a 200 replay as ok', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
    expect(await notify(ownerEnv, fullLead, 42)).toEqual({ ok: true });
  });
  it('reports a 401 without leaking the secret', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 401 }));
    const r = await notify(ownerEnv, fullLead, 42);
    expect(r).toEqual({ ok: false, error: 'owner: owner status 401' });
    expect(r.error).not.toContain(SECRET);
  });
  it('reports a network error and resolves, never rejects', async () => {
    fetchMock.mockRejectedValue(new Error('network down'));
    await expect(notify(ownerEnv, fullLead, 42)).resolves.toEqual({ ok: false, error: 'owner: network down' });
  });
  it('refuses with missing url or secret, without calling fetch', async () => {
    const expected = { ok: false, error: 'owner: owner config missing' };
    expect(await notify({ ...ownerEnv, OWNER_INTAKE_URL: '' }, fullLead, 42)).toEqual(expected);
    expect(await notify({ ...ownerEnv, OWNER_INTAKE_SECRET: '' }, fullLead, 42)).toEqual(expected);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('refuses an http:// url, without calling fetch', async () => {
    const r = await notify({ ...ownerEnv, OWNER_INTAKE_URL: 'http://owner.actionaura.me/x' }, fullLead, 42);
    expect(r).toEqual({ ok: false, error: 'owner: owner url must be https' });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('smtp failing does not stop owner', async () => {
    send.mockRejectedValue(new Error('auth failed'));
    const r = await notify({ ...smtpEnv, ...ownerEnv, NOTIFY_PROVIDER: 'smtp,owner' }, fullLead, 42);
    expect(r).toEqual({ ok: false, error: 'smtp: auth failed' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('owner failing does not stop smtp', async () => {
    send.mockResolvedValue(undefined);
    fetchMock.mockResolvedValue(new Response('{}', { status: 503 }));
    const r = await notify({ ...smtpEnv, ...ownerEnv, NOTIFY_PROVIDER: 'smtp,owner' }, fullLead, 42);
    expect(r).toEqual({ ok: false, error: 'owner: owner status 503' });
    expect(send).toHaveBeenCalledTimes(1);
  });
  it('joins both failures in provider order', async () => {
    send.mockRejectedValue(new Error('auth failed'));
    fetchMock.mockResolvedValue(new Response('{}', { status: 503 }));
    const r = await notify({ ...smtpEnv, ...ownerEnv, NOTIFY_PROVIDER: 'smtp,owner' }, fullLead, 42);
    expect(r).toEqual({ ok: false, error: 'smtp: auth failed; owner: owner status 503' });
  });
  it('both succeeding is ok', async () => {
    send.mockResolvedValue(undefined);
    expect(await notify({ ...smtpEnv, ...ownerEnv, NOTIFY_PROVIDER: 'smtp,owner' }, fullLead, 42)).toEqual({ ok: true });
  });
  it('trims, lower-cases, de-duplicates and drops none', async () => {
    expect(await notify({ ...ownerEnv, NOTIFY_PROVIDER: ' owner , OWNER ,none' }, fullLead, 42)).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('unknown provider is reported, others still run', async () => {
    const r = await notify({ ...ownerEnv, NOTIFY_PROVIDER: 'pigeon,owner' }, fullLead, 42);
    expect(r).toEqual({ ok: false, error: 'pigeon: unknown provider' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('prototype names never resolve to a provider', async () => {
    expect(await notify({ NOTIFY_PROVIDER: 'constructor' } as any, fullLead, 42)).toEqual({ ok: false, error: 'constructor: unknown provider' });
  });
  it('a provider throwing outside its own guard is contained', async () => {
    const env = { NOTIFY_PROVIDER: 'smtp', get SMTP_HOST(): string { throw new Error('boom'); } } as any;
    await expect(notify(env, fullLead, 42)).resolves.toEqual({ ok: false, error: 'smtp: boom' });
  });
});
