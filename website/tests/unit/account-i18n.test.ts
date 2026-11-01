import { describe, it, expect } from 'vitest';
import { accountEn } from '../../src/i18n/pages/account.en';
import { accountAr } from '../../src/i18n/pages/account.ar';

// Owner's real product_code is AURA_RETAIL (found by checking the site against the real Owner);
// RETAIL stays as an alias for older fixtures.
describe('account i18n', () => {
  it('product names resolve for the real Owner code and the alias', () => {
    expect(accountEn.home.package.productNames.AURA_RETAIL).toBe('Aura Retail');
    expect(accountEn.home.package.productNames.RETAIL).toBe('Aura Retail');
    expect(accountAr.home.package.productNames.AURA_RETAIL).toBe('أورا ريتيل');
    expect(accountAr.home.package.productNames.RETAIL).toBe('أورا ريتيل');
  });

  it('password reasons include same_as_old and a generic fallback in both languages', () => {
    expect(accountEn.setPassword.reasons.same_as_old).toBe('The new password must be different from the temporary one.');
    expect(accountAr.setPassword.reasons.same_as_old).toBe('يجب أن تختلف كلمة المرور الجديدة عن المؤقتة.');
    expect(accountEn.setPassword.reasons.generic).toBeTruthy();
    expect(accountAr.setPassword.reasons.generic).toBeTruthy();
  });
});
