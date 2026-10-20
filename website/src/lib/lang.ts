import { isLang, type Lang } from '../i18n';

const WA_NUMBER = '962780069485';
export const PHONE_TEL = 'tel:+962780069485';

// Stored value wins only if it is exactly 'ar' or 'en'; anything else (old
// formats, other sites' junk, 'AR') is ignored rather than trusted.
export function pickLang(stored: string | null, languages: readonly string[]): Lang {
  if (isLang(stored)) return stored;
  for (const raw of languages) {
    const base = raw.toLowerCase().split('-')[0];
    if (isLang(base)) return base;
  }
  return 'en';
}

export function waLink(text: string): string {
  const base = `https://wa.me/${WA_NUMBER}`;
  return text ? `${base}?text=${encodeURIComponent(text)}` : base;
}

// The company's Instagram profile (owner, 2026-10-21). Also listed as sameAs in the Organization JSON-LD.
export const INSTAGRAM = 'https://www.instagram.com/actionaura.me/';
