import { test, expect } from '@playwright/test';

// The production origin: canonical, hreflang and og:image URLs are absolute on it, so they are checked
// against the local build as if same-origin (a missing share image or alternate page is a broken link).
const PROD = 'https://actionaura.me';
const ORIGIN = 'http://local.invalid';
const SKIP_SCHEMES = /^(mailto:|tel:|https:\/\/wa\.me|javascript:|data:)/i;

function extract(html: string): string[] {
  const out: string[] = [];
  for (const m of html.matchAll(/<meta\s[^>]*(?:property|name)="(?:og:image|twitter:image)"[^>]*>/gi)) {
    const c = m[0].match(/\scontent\s*=\s*"([^"]*)"/i);
    if (c) out.push(c[1]);
  }
  for (const m of html.matchAll(/<(?:a|link|img|script|source)\b[^>]*>/gi)) {
    const tag = m[0];
    for (const a of tag.matchAll(/\s(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')/gi)) {
      out.push(a[1] ?? a[2]);
    }
    if (/^<source\b/i.test(tag)) {
      const s = tag.match(/\ssrcset\s*=\s*(?:"([^"]*)"|'([^']*)')/i);
      const first = (s?.[1] ?? s?.[2] ?? '').trim().split(/[\s,]+/)[0];
      if (first) out.push(first);
    }
  }
  return out;
}

test('every same-origin link and asset resolves; every #fragment has a target', async ({ request, baseURL }) => {
  const base = new URL(baseURL ?? 'http://localhost:4321');
  const isPagePath = (p: string) => p.startsWith('/ar/') || p.startsWith('/en/');

  const linkedFrom = new Map<string, Set<string>>(); // url (path+search) -> pages linking to it
  const htmlByPage = new Map<string, string>();
  const fragments: { page: string; target: string; id: string }[] = [];
  const queue = ['/ar/', '/en/'];
  const queued = new Set(queue);

  while (queue.length) {
    const pagePath = queue.shift()!;
    const res = await request.get(pagePath);
    const html = res.ok() ? await res.text() : '';
    htmlByPage.set(pagePath, html);
    if (!res.ok()) {
      (linkedFrom.get(pagePath) ?? linkedFrom.set(pagePath, new Set()).get(pagePath)!).add('(crawl start)');
      continue;
    }
    for (const raw of extract(html)) {
      const ref = raw.trim();
      if (!ref || SKIP_SCHEMES.test(ref)) continue;
      let u: URL;
      try { u = new URL(ref.startsWith(PROD + '/') ? ref.slice(PROD.length) : ref, new URL(pagePath, base)); } catch { continue; }
      if (u.origin !== base.origin) continue;
      if (u.pathname.startsWith('/api/')) continue;
      const key = u.pathname + u.search;
      if (!linkedFrom.has(key)) linkedFrom.set(key, new Set());
      linkedFrom.get(key)!.add(pagePath);
      if (u.hash.length > 1 && u.hash !== '#main') {
        fragments.push({ page: pagePath, target: u.pathname, id: decodeURIComponent(u.hash.slice(1)) });
      }
      if (isPagePath(u.pathname) && !queued.has(u.pathname) && u.pathname.endsWith('/')) {
        queued.add(u.pathname);
        queue.push(u.pathname);
      }
    }
  }

  const broken: string[] = [];
  for (const [url, from] of linkedFrom) {
    const res = await request.get(url, { maxRedirects: 0 });
    const s = res.status();
    if (s < 200 || s >= 400) broken.push(`${s} ${url}  <- ${[...from].join(', ')}`);
  }

  const badFragments: string[] = [];
  for (const f of fragments) {
    let html = htmlByPage.get(f.target);
    if (html === undefined) continue; // target not a crawled page
    const esc = f.id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (!new RegExp(`\\sid\\s*=\\s*["']${esc}["']`).test(html)) {
      badFragments.push(`#${f.id} missing on ${f.target}  <- ${f.page}`);
    }
  }

  expect(broken, `broken URLs:\n${broken.join('\n')}`).toEqual([]);
  expect(badFragments, `dangling fragments:\n${badFragments.join('\n')}`).toEqual([]);
  expect(htmlByPage.size, 'crawl reached pages').toBeGreaterThanOrEqual(16);
});
