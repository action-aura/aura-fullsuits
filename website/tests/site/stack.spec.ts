import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Needs the REAL stack, not the astro dev server. Two modes, picked by STACK_URL:
// - LOCAL (default, STACK_URL unset or localhost/127.0.0.1): `npm run build`, `npm run d1:local`,
//   then `npm run pages:dev` (wrangler on :8788) must already be running.
//   `.dev.vars` must set ALLOW_NO_TURNSTILE=1, IP_SALT=<random> and NOTIFY_PROVIDER=none.
//   All of (a)-(d) run.
// - REMOTE (STACK_URL=https://<project>.pages.dev): only (b) runs. A pages.dev host must send
//   noindex, so (a) is local-only; with a real TURNSTILE_SECRET (c) and (d) cannot pass
//   unattended, so check them by hand on pages.dev.
// /api/demo is rate limited to 5 per 10 minutes per IP, so this spec makes at most 2 posts
// in total ((c) and (d)). Do not add more without raising that budget.
// The D1 row for (c) is confirmed by the main session, not here.

const STACK_URL = process.env.STACK_URL ?? 'http://localhost:8788';
const LOCAL = /localhost|127\.0\.0\.1/.test(STACK_URL);

test.use({ baseURL: STACK_URL });

const DIST = path.resolve('dist');
const CSP_NOISE = /Content Security Policy|Refused to/;
const VALID_NAME = 'Test Customer';
const VALID_PHONE = '0780069485';

function distPages(dir: string, out: string[] = []): string[] {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) distPages(full, out);
    else if (e.name === 'index.html') {
      const rel = path.relative(DIST, path.dirname(full)).split(path.sep).filter(Boolean).join('/');
      out.push(rel ? `/${rel}/` : '/');
    }
  }
  return out;
}

test('(a) /en/ sends a CSP and does not noindex the real host', async ({ request }) => {
  test.skip(!LOCAL, 'a pages.dev host must send noindex, so (a) is local-only');
  const res = await request.get('/en/');
  expect(res.status()).toBe(200);
  expect(res.headers()['content-security-policy'], 'CSP header present').toBeTruthy();
  expect(res.headers()['x-robots-tag']).toBeUndefined();
});

test('(b) every built page loads with zero CSP violations', async ({ page }) => {
  // ~18 pages, each booting the WebGL scene: the default 30s budget is per test, not per page.
  test.setTimeout(300_000);
  const pages = distPages(DIST).filter((p) => p === '/' || p.startsWith('/en/') || p.startsWith('/ar/'));
  expect(pages.filter((p) => p.startsWith('/en/')).length, 'en pages found in dist').toBeGreaterThan(0);
  expect(pages.filter((p) => p.startsWith('/ar/')).length, 'ar pages found in dist').toBeGreaterThan(0);

  const offenders: string[] = [];
  let current = '';
  page.on('console', (msg) => {
    if (CSP_NOISE.test(msg.text())) offenders.push(`${current}  ${msg.text()}`);
  });
  for (const p of pages) {
    current = p;
    // 'load' plus a settle: Turnstile and the deferred scene import keep the network busy, so networkidle never fires.
    await page.goto(p, { waitUntil: 'load' });
    await page.waitForTimeout(1500);
  }
  expect(offenders, `CSP violations:\n${offenders.join('\n')}`).toEqual([]);
});

test('(c) valid lead through the real Function shows the success panel', async ({ page }) => {
  test.skip(!LOCAL, 'with a real TURNSTILE_SECRET the post needs a solved challenge; check by hand on pages.dev');
  await page.goto('/en/contact/');
  await expect(page.locator('form#demo-form')).toBeVisible();
  await page.fill('#demo-form [name="name"]', VALID_NAME);
  await page.fill('#demo-form [name="phone"]', VALID_PHONE);
  await page.click('button#demo-send');
  await expect(page.locator('#demo-success')).toBeVisible({ timeout: 10000 });
});

test('(d) no-JS form POST gets a 303 to the sent page', async ({ request }) => {
  test.skip(!LOCAL, 'with a real TURNSTILE_SECRET a no-JS post gets #form-error; check by hand on pages.dev');
  const res = await request.post('/api/demo', {
    form: { name: VALID_NAME, phone: VALID_PHONE, lang: 'en' },
    headers: { accept: 'text/html' },
    maxRedirects: 0,
  });
  expect(res.status()).toBe(303);
  expect(res.headers()['location'] ?? '').toMatch(/\/en\/contact\/sent\/$/);
});
