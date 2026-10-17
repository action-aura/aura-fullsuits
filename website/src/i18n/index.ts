import { en, type Dict } from './en';
import { ar } from './ar';

export type Lang = 'ar' | 'en';
export const LANGS: readonly Lang[] = ['ar', 'en'];
const TABLE: Record<Lang, Dict> = { ar, en };

export function dict(lang: Lang): Dict {
  return TABLE[lang];
}

export function dir(lang: Lang): 'rtl' | 'ltr' {
  return lang === 'ar' ? 'rtl' : 'ltr';
}

export function isLang(v: unknown): v is Lang {
  return v === 'ar' || v === 'en';
}
