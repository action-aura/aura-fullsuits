import { describe, it, expect } from 'vitest';
import { dict, dir, LANGS } from '../../src/i18n';

function leaves(o: unknown, path = ''): Array<[string, string]> {
  if (typeof o === 'string') return [[path, o]];
  if (Array.isArray(o)) return o.flatMap((v, i) => leaves(v, `${path}[${i}]`));
  return Object.entries(o as Record<string, unknown>).flatMap(([k, v]) => leaves(v, path ? `${path}.${k}` : k));
}

describe('dictionaries', () => {
  const en = leaves(dict('en'));
  const ar = leaves(dict('ar'));

  it('have identical key paths', () => {
    expect(ar.map(([k]) => k).sort()).toEqual(en.map(([k]) => k).sort());
  });

  it.each(LANGS)('%s has no empty strings', (lang) => {
    for (const [k, v] of leaves(dict(lang))) expect(v.trim(), k).not.toBe('');
  });

  it.each(LANGS)('%s has no em-dash and no exclamation mark', (lang) => {
    for (const [k, v] of leaves(dict(lang))) {
      expect(v.includes('—'), `${k} has an em-dash`).toBe(false);
      expect(/[!!！]/.test(v), `${k} has "!"`).toBe(false);
    }
  });

  it('arabic uses western digits only', () => {
    for (const [k, v] of ar) expect(/[٠-٩۰-۹]/.test(v), `${k} has Arabic-Indic digits`).toBe(false);
  });

  it('never claims banned features or prices', () => {
    const banned = /(JoFotara|e-invoic|iPhone|\biOS\b|cash drawer|JOD\b|\bJD\b|دينار|د\.أ|testimonial|14[- ]day|grace period|customers? trust|\d[\d,.]*\+?\s*(shops|customers|clients|stores)\b|[\d٠-٩][\d٠-٩,.]*\+?\s*(عميل|زبون|محل|متجر)|Android\s*print|جوفوترة|جو فوترة|الفوترة الإلكترونية|فوترة إلكترونية|آيفون|ايفون|درج النقود|درج الكاش|فترة سماح|آراء العملاء|طباعة.{0,20}أندرويد)/i;
    // The live till on the home hero shows EXAMPLE item prices with a currency label (owner approved 2026-10-23, and the
    // footnote says the prices are illustrative). Exactly these three keys carry the currency; every other string,
    // including all other keys under home.till, stays banned. This can no longer catch a currency word added to those
    // three keys, so it is not a place to put a real product price.
    const TILL_CURRENCY_KEYS = new Set(['home.till.currency', 'home.till.liveTotal', 'home.till.liveDone']);
    for (const [k, v] of [...en, ...ar]) {
      if (TILL_CURRENCY_KEYS.has(k)) continue;
      expect(banned.test(v), `${k}: ${v}`).toBe(false);
    }
  });

  it('dir is rtl for arabic only', () => {
    expect(dir('ar')).toBe('rtl');
    expect(dir('en')).toBe('ltr');
  });

  it.each(LANGS)('%s chapter accents appear inside their titles', (lang) => {
    const h = dict(lang).home;
    for (const k of ['company', 'retail', 'clinic', 'gym'] as const) expect(h[k].title.includes(h[k].accent), `${lang} ${k}`).toBe(true);
  });
});
