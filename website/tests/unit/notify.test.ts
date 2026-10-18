import { describe, it, expect, vi, beforeEach } from 'vitest';
const send = vi.fn();
const connect = vi.fn(async () => ({ send, close: vi.fn() }));
vi.mock('worker-mailer', () => ({ WorkerMailer: { connect } }));
import { notify } from '../../functions/_lib/notify';

const lead = { name: 'Sami', phone: '+962790000000', businessType: 'shop', businessName: '', branches: '', message: '', lang: 'ar' } as any;
const smtpEnv = { NOTIFY_PROVIDER: 'smtp', SMTP_HOST: 'mail.privateemail.com', SMTP_PORT: '465', SMTP_USER: 'info@actionaura.me', SMTP_PASS: 'x', NOTIFY_TO: 'info@actionaura.me' } as any;

beforeEach(() => { send.mockReset(); connect.mockClear(); });

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
  it('reports, never throws, when the SMTP server fails', async () => {
    send.mockRejectedValue(new Error('auth failed'));
    expect(await notify(smtpEnv, lead, 7)).toEqual({ ok: false, error: 'auth failed' });
  });
  it('refuses to send with missing credentials', async () => {
    expect(await notify({ ...smtpEnv, SMTP_PASS: '' }, lead, 7)).toEqual({ ok: false, error: 'smtp config missing' });
    expect(connect).not.toHaveBeenCalled();
  });
  it('provider none still sends nothing', async () => {
    expect(await notify({ NOTIFY_PROVIDER: 'none' } as any, lead, 7)).toEqual({ ok: true });
    expect(connect).not.toHaveBeenCalled();
  });
});
