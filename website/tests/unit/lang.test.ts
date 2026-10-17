import { describe, it, expect } from 'vitest';
import { pickLang, waLink, PHONE_TEL } from '../../src/lib/lang';

describe('pickLang', () => {
  it('honours a valid stored choice', () => expect(pickLang('ar', ['en-US'])).toBe('ar'));
  it('ignores junk stored values', () => {
    for (const junk of ['fr', '', 'AR', 'null', 'en-US']) expect(pickLang(junk, ['ar-JO'])).toBe('ar');
  });
  it('maps ar-* device languages to ar', () => expect(pickLang(null, ['ar-JO'])).toBe('ar'));
  it('first supported device language wins', () => {
    expect(pickLang(null, ['en-US', 'ar-JO'])).toBe('en');
    expect(pickLang(null, ['fr-FR', 'ar'])).toBe('ar');
  });
  it('falls back to en', () => {
    expect(pickLang(null, [])).toBe('en');
    expect(pickLang(null, ['fr-FR', 'de'])).toBe('en');
  });
  it('lowercases device languages', () => expect(pickLang(null, ['AR-jo'])).toBe('ar'));
});

describe('waLink', () => {
  it('builds an encoded wa.me link', () => {
    expect(waLink('مرحبا & hi')).toBe('https://wa.me/962780069485?text=' + encodeURIComponent('مرحبا & hi'));
  });
  it('omits text when empty', () => expect(waLink('')).toBe('https://wa.me/962780069485'));
  it('exposes the tel link', () => expect(PHONE_TEL).toBe('tel:+962780069485'));
});
