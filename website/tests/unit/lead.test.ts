import { describe, it, expect } from 'vitest';
import { validateLead, normalizePhone, normalizeDigits, LIMITS } from '../../src/lib/lead';

describe('normalizeDigits', () => {
  it('maps Arabic-Indic and Persian digits to Western', () => expect(normalizeDigits('٠٧٨٠٠٦٩٤٨٥ ۱۲')).toBe('0780069485 12'));
});

describe('normalizePhone', () => {
  it.each([
    ['0780069485', '+962780069485'], ['078 006 9485', '+962780069485'], ['+962 78-006-9485', '+962780069485'],
    ['00962780069485', '+962780069485'], ['٠٧٨٠٠٦٩٤٨٥', '+962780069485'], ['780069485', '+962780069485'],
    ['+971501234567', '+971501234567'],
    ['06 5000000', '+96265000000'], ['+962 6 500 0000', '+96265000000'],
  ])('%s -> %s', (raw, want) => expect(normalizePhone(raw)).toBe(want));
  it.each(['', '12', '07800', '0680069485', 'abc', '+0123456789', '+1234567890123456', '0750069485', '0650000000', '01 5000000'])('rejects %s', (raw) => expect(normalizePhone(raw)).toBeNull());
});

describe('validateLead', () => {
  const good = { name: '  Ahmad  ', phone: '٠٧٨٠٠٦٩٤٨٥', businessType: 'shop', businessName: '', branches: '1', message: '', lang: 'ar' };
  it('accepts a minimal valid lead and trims/normalises', () => {
    const r = validateLead(good);
    expect(r.ok).toBe(true);
    if (r.ok) { expect(r.lead.name).toBe('Ahmad'); expect(r.lead.phone).toBe('+962780069485'); expect(r.lead.lang).toBe('ar'); }
  });
  it('requires name and phone', () => {
    const r = validateLead({ ...good, name: ' ', phone: '' });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors).toEqual({ name: 'nameRequired', phone: 'phoneRequired' });
  });
  it('rejects an invalid phone', () => {
    const r = validateLead({ ...good, phone: '12345' });
    expect(!r.ok && r.errors.phone).toBe('phoneInvalid');
  });
  it('caps lengths', () => {
    const r = validateLead({ ...good, name: 'x'.repeat(LIMITS.name + 1), message: 'y'.repeat(LIMITS.message + 1) });
    expect(!r.ok && r.errors).toEqual({ name: 'nameLong', message: 'tooLong' });
  });
  it('coerces unknown enum values to empty, never trusts them', () => {
    const r = validateLead({ ...good, businessType: '<script>', branches: '99', lang: 'fr' });
    expect(r.ok && [r.lead.businessType, r.lead.branches, r.lead.lang]).toEqual(['', '', 'en']);
  });
  it('treats non-string fields as empty', () => {
    const r = validateLead({ ...good, name: 42 as unknown as string });
    expect(!r.ok && r.errors.name).toBe('nameRequired');
  });
  it('keeps HTML as plain text (escaping is the renderer job)', () => {
    const r = validateLead({ ...good, businessName: '<b>Shop</b>' });
    expect(r.ok && r.lead.businessName).toBe('<b>Shop</b>');
  });
});
