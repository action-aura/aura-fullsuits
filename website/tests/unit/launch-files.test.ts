import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const pub = (f: string) => fs.readFileSync(path.resolve('public', f), 'utf8');

describe('robots.txt', () => {
  it('allows the site, blocks the API, points at the sitemap index', () => {
    const r = pub('robots.txt');
    expect(r).toMatch(/^User-agent: \*$/m);
    expect(r).toMatch(/^Disallow: \/api\/$/m);
    expect(r).toMatch(/^Sitemap: https:\/\/actionaura\.me\/sitemap-index\.xml$/m);
    expect(r).not.toMatch(/^Disallow: \/$/m);
  });
});

describe('_headers', () => {
  const h = pub('_headers');
  // Split into { pattern: [header lines] } blocks: a pattern line starts at column 0.
  const blocks: Record<string, string[]> = {};
  let cur = '';
  for (const line of h.split(/\r?\n/)) {
    if (!line.trim() || line.startsWith('#')) continue;
    if (!/^\s/.test(line)) { cur = line.trim(); blocks[cur] = []; } else blocks[cur].push(line.trim());
  }
  it('noindexes the pages.dev preview host only', () => {
    expect(blocks['https://:project.pages.dev/*']).toContain('X-Robots-Tag: noindex');
    expect((blocks['/*'] ?? []).some((l) => l.startsWith('X-Robots-Tag'))).toBe(false);
  });
  it('sends the security headers on every path', () => {
    const all = blocks['/*'] ?? [];
    for (const name of ['Content-Security-Policy', 'X-Content-Type-Options', 'Referrer-Policy', 'Permissions-Policy', 'X-Frame-Options', 'Strict-Transport-Security']) {
      expect(all.some((l) => l.startsWith(name + ':')), name).toBe(true);
    }
  });
  it('CSP allows exactly the third parties the site uses', () => {
    const csp = (blocks['/*'] ?? []).find((l) => l.startsWith('Content-Security-Policy:'))!;
    expect(csp).toContain('https://challenges.cloudflare.com');
    expect(csp).toContain('https://static.cloudflareinsights.com');
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("form-action 'self'");
  });
  it('caches hashed build assets forever', () => {
    expect(blocks['/_astro/*']).toContain('Cache-Control: public, max-age=31536000, immutable');
  });
});

describe('llms.txt', () => {
  const l = pub('llms.txt');
  it('names both products and the contact route', () => {
    expect(l).toMatch(/^# Action Aura$/m);
    expect(l).toContain('https://actionaura.me/en/aura-retail/');
    expect(l).toContain('https://actionaura.me/en/aura-clinic/');
    expect(l).toContain('+962 78 006 9485');
  });
  it('makes no banned claim', () => {
    expect(l).not.toMatch(/\bJD\s?\d|\d+\s?JOD|price[sd]?\s*(from|start)|iOS|iPhone app|JoFotara|e-invoic|cash drawer|Android print|testimonial/i);
    expect(l).not.toMatch(/—|!/);
  });
});

describe('site.webmanifest', () => {
  const m = JSON.parse(pub('site.webmanifest'));
  it('has name, theme colour and both icon sizes', () => {
    expect(m.name).toBe('Action Aura');
    expect(m.theme_color).toBe('#0E1729');
    expect(m.icons.map((i: { sizes: string }) => i.sizes).sort()).toEqual(['192x192', '512x512']);
  });
});

const pngSize = (f: string): [number, number] => {
  const b = fs.readFileSync(path.resolve('public', f));
  expect(b.subarray(1, 4).toString('latin1')).toBe('PNG');
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
};
describe('favicon set', () => {
  it.each([
    ['favicon-32.png', 32], ['apple-touch-icon.png', 180], ['icon-192.png', 192], ['icon-512.png', 512],
  ])('%s is %ipx square', (f, n) => { expect(pngSize(f)).toEqual([n, n]); });
  it('favicon.svg is the flat mark on the navy tile (no 3D gradients)', () => {
    const s = pub('favicon.svg');
    expect(s).toMatch(/^<svg[^>]+viewBox="0 0 512 512"/);
    expect(s).toContain('#0E1729');
    expect(s).not.toMatch(/linearGradient|radialGradient|filter/);
  });
});
