export const LIMITS = { name: 80, businessName: 120, message: 1000 } as const;
export type LeadError = 'nameRequired' | 'nameLong' | 'phoneRequired' | 'phoneInvalid' | 'tooLong';
export type Lead = {
  name: string; phone: string; businessType: 'shop' | 'clinic' | 'other' | ''; businessName: string;
  branches: '1' | '2-3' | 'more' | ''; message: string; lang: 'ar' | 'en';
};
export type LeadInput = Record<string, unknown>;

const str = (v: unknown): string => (typeof v === 'string' ? v : '');

// Arabic keyboards type Arabic-Indic (U+0660..9) or Persian (U+06F0..9) digits; store Western digits only.
export function normalizeDigits(s: string): string {
  return s.replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660))
          .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06F0));
}

// Jordan mobiles: 07[789]XXXXXXX, 7[789]XXXXXXX, (00|+)9627[789]XXXXXXX -> +9627XXXXXXXX.
// Jordan landlines: 0[2-6]XXXXXXX -> +962[2-6]XXXXXXX.
// Anything else must be a plausible E.164 number: + then 8 to 15 digits, first digit 1-9.
export function normalizePhone(raw: string): string | null {
  const s = normalizeDigits(raw).replace(/[\s\-().]/g, '');
  if (!s) return null;
  const jo = s.match(/^(?:\+962|00962|0)?(7[789]\d{7})$/);
  if (jo) return `+962${jo[1]}`;
  const land = s.match(/^(?:\+962|00962|0)?([2-6]\d{7})$/);
  if (land) return '+962' + land[1];
  const intl = s.replace(/^00/, '+');
  if (/^\+[1-9]\d{7,14}$/.test(intl) && !intl.startsWith('+962')) return intl;
  return null;
}

export function validateLead(input: LeadInput):
  | { ok: true; lead: Lead }
  | { ok: false; errors: Partial<Record<keyof Lead, LeadError>> } {
  const errors: Partial<Record<keyof Lead, LeadError>> = {};
  // Single-line fields: CR/LF in name reaches the notification Subject header verbatim (header injection).
  const oneLine = (v: unknown): string => str(v).replace(/[\u0000-\u001F\u007F]+/g, ' ').trim();
  const name = oneLine(input.name);
  const phoneRaw = str(input.phone).trim();
  const businessName = oneLine(input.businessName);
  const message = str(input.message).trim();
  if (!name) errors.name = 'nameRequired';
  else if (name.length > LIMITS.name) errors.name = 'nameLong';
  const phone = phoneRaw ? normalizePhone(phoneRaw) : null;
  if (!phoneRaw) errors.phone = 'phoneRequired';
  else if (!phone) errors.phone = 'phoneInvalid';
  if (businessName.length > LIMITS.businessName) errors.businessName = 'tooLong';
  if (message.length > LIMITS.message) errors.message = 'tooLong';
  if (Object.keys(errors).length) return { ok: false, errors };
  const bt = str(input.businessType);
  const br = str(input.branches);
  return {
    ok: true,
    lead: {
      name, phone: phone!, businessName, message,
      businessType: bt === 'shop' || bt === 'clinic' || bt === 'other' ? bt : '',
      branches: br === '1' || br === '2-3' || br === 'more' ? br : '',
      lang: str(input.lang) === 'ar' ? 'ar' : 'en',
    },
  };
}
