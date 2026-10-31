import { test, expect, type Page } from '@playwright/test';
import crypto from 'node:crypto';
import fs from 'node:fs';
import AxeBuilder from '@axe-core/playwright';

const ON = 'http://127.0.0.1:8790';
const OFF = 'http://127.0.0.1:8791';
const BUILT_ON_RUNTIME_OFF = 'http://127.0.0.1:8792';
const OWNER = 'http://127.0.0.1:8799';
const COOKIE = '__Host-aura_acct';
const GOOD = 'Shop-pass-2026-ok';

async function ownerLog(): Promise<Array<Record<string, unknown>>> {
  return ((await (await fetch(`${OWNER}/__log`)).json()) as { log: Array<Record<string, unknown>> }).log;
}

async function signIn(page: Page, email: string, password = GOOD, lang: 'en' | 'ar' = 'en') {
  await page.goto(`${ON}/${lang}/account/sign-in/`);
  await page.fill('#f-email', email);
  await page.fill('#f-password', password);
  await page.click('#acct-submit');
}

// The session cookie is Secure. Chromium sends it to http://127.0.0.1 from the page, but the Playwright request
// client (page.request) does not, so tests that call the API by hand pass the browser's own cookie explicitly.
async function sessionCookieHeader(page: Page): Promise<string> {
  const c = (await page.context().cookies()).find((x) => x.name === COOKIE);
  if (!c) throw new Error('no session cookie in the browser: sign-in did not finish');
  return `${c.name}=${c.value}`;
}

test.beforeEach(async () => { await fetch(`${OWNER}/__reset`, { method: 'POST' }); });

test.describe('sign in', () => {
  test('a visitor with no session is sent from the account page to sign-in', async ({ page }) => {
    await page.goto(`${ON}/en/account/`);
    await expect(page).toHaveURL(/\/en\/account\/sign-in\/\?n=session/);
    await expect(page.locator('#acct-msg')).toContainText('session ended');
  });

  test('wrong password: one plain message, still on the page, no cookie', async ({ page, context }) => {
    await signIn(page, 'ready@shop.test', 'not-the-password');
    await expect(page.locator('#acct-msg')).toContainText('email or password is not right');
    await expect(page).toHaveURL(/sign-in\/$/);
    expect((await context.cookies()).find((c) => c.name === COOKIE)).toBeUndefined();
    await expect(page.locator('#f-password')).toHaveValue('');
  });

  test('unknown email gets the very same message', async ({ page }) => {
    await signIn(page, 'nobody@shop.test');
    await expect(page.locator('#acct-msg')).toContainText('email or password is not right');
  });

  test('lockout shows the rate-limit message', async ({ page }) => {
    await signIn(page, 'locked@shop.test');
    await expect(page.locator('#acct-msg')).toContainText('Too many attempts');
  });

  test('the session cookie is HttpOnly, Secure, SameSite=Lax, __Host- and unreadable by page script', async ({ page, context }) => {
    await signIn(page, 'ready@shop.test');
    await expect(page).toHaveURL(/\/en\/account\/$/);
    const cookie = (await context.cookies()).find((c) => c.name === COOKIE)!;
    expect(cookie, 'cookie set').toBeDefined();
    expect(cookie).toMatchObject({ httpOnly: true, secure: true, sameSite: 'Lax', path: '/' });
    expect(cookie.expires - Date.now() / 1000, 'short life').toBeLessThanOrEqual(3600 + 5);
    expect(await page.evaluate(() => document.cookie)).not.toContain('aura_acct');
    expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain(cookie.value);
  });
});

test.describe('forced password change', () => {
  test('temporary password: sign-in leads to the change page and nothing else opens until it is done', async ({ page }) => {
    await signIn(page, 'temp@shop.test', 'Temp-pass-2026');
    await expect(page).toHaveURL(/set-password\/\?forced=1/);
    await expect(page.locator('#acct-heading')).toHaveText('Choose a new password');
    // Trying to walk past it goes straight back.
    await page.goto(`${ON}/en/account/`);
    await expect(page).toHaveURL(/set-password\/\?forced=1/);
    await page.goto(`${ON}/en/account/downloads/`);
    await expect(page).toHaveURL(/set-password\/\?forced=1/);
  });

  test('the server also refuses a download link while the password is temporary', async ({ page }) => {
    await signIn(page, 'temp@shop.test', 'Temp-pass-2026');
    // Wait until the sign-in really finished (cookie set); otherwise the request below has no session and gets a 401.
    await expect(page).toHaveURL(/set-password\/\?forced=1/);
    const res = await page.request.post(`${ON}/api/account/downloads/authorize`, {
      headers: { origin: ON, 'x-aura-account': '1', 'content-type': 'application/json', cookie: await sessionCookieHeader(page) }, data: { offerId: 'win-1' },
    });
    expect(res.status()).toBe(403);
    // 403 alone cannot tell "Owner refused" from "our CSRF guard refused": prove the call really reached Owner, session-bound.
    expect((await ownerLog()).some((l) => l.action === 'downloads/authorize' && l.sessionBound === true)).toBe(true);
  });

  test('mismatch, weak, wrong-current and then success', async ({ page }) => {
    await signIn(page, 'temp@shop.test', 'Temp-pass-2026');
    await page.fill('#f-current', 'Temp-pass-2026');
    await page.fill('#f-next', 'A-brand-new-pass-77');
    await page.fill('#f-again', 'A-brand-new-pass-78');
    await page.click('#acct-submit');
    await expect(page.locator('#acct-msg')).toContainText('do not match');

    await page.fill('#f-next', 'short');
    await page.fill('#f-again', 'short');
    await page.click('#acct-submit');
    await expect(page.locator('#acct-msg')).toContainText('too short');

    await page.fill('#f-current', 'wrong-current-pass');
    await page.fill('#f-next', 'A-brand-new-pass-77');
    await page.fill('#f-again', 'A-brand-new-pass-77');
    await page.click('#acct-submit');
    await expect(page.locator('#acct-msg')).toContainText('current password is not right');

    await page.fill('#f-current', 'Temp-pass-2026');
    await page.click('#acct-submit');
    await expect(page).toHaveURL(/\/en\/account\/$/);
    await expect(page.locator('.acct-card').first()).toBeVisible();
    // The old password is dead; the new one signs in.
    await page.request.post(`${ON}/api/account/sign-out`, { headers: { origin: ON, 'x-aura-account': '1', 'content-type': 'application/json' }, data: {} });
    await signIn(page, 'temp@shop.test', 'Temp-pass-2026');
    await expect(page.locator('#acct-msg')).toContainText('not right');
    await signIn(page, 'temp@shop.test', 'A-brand-new-pass-77');
    await expect(page).toHaveURL(/\/en\/account\/$/);
  });

  test('an invitation link: token leaves the address bar at once, works once, then asks for a new link', async ({ page }) => {
    await page.goto(`${ON}/en/account/set-password/#t=good-invite-token-0123456789abcdef`);
    await expect(page.locator('#acct-heading')).toHaveText('Choose your password');
    expect(page.url(), 'token removed from the address bar').not.toContain('good-invite');
    await expect(page.locator('#row-current')).toBeHidden();
    await page.fill('#f-next', 'Invited-user-pass-1');
    await page.fill('#f-again', 'Invited-user-pass-1');
    await page.click('#acct-submit');
    await expect(page).toHaveURL(/sign-in\/\?n=password-saved/);
    await expect(page.locator('#acct-msg')).toContainText('password is saved');
    await signIn(page, 'temp@shop.test', 'Invited-user-pass-1');
    await expect(page).toHaveURL(/\/en\/account\/$/);

    await page.goto(`${ON}/en/account/set-password/#t=good-invite-token-0123456789abcdef`);
    await page.fill('#f-next', 'Another-pass-2026-x');
    await page.fill('#f-again', 'Another-pass-2026-x');
    await page.click('#acct-submit');
    await expect(page.locator('#acct-msg')).toContainText('expired or was already used');
    await expect(page.locator('#acct-ask-new')).toBeVisible();
  });
});

test.describe('account page', () => {
  test('shows package, trial days left, how to pay with the seller, and downloads', async ({ page }) => {
    await signIn(page, 'ready@shop.test');
    const body = page.locator('#acct-root');
    await expect(body).toContainText('Aura Retail');
    await expect(body).toContainText('Growth');
    await expect(body).toContainText('5 days left in your free trial');
    await expect(body).toContainText('7 November 2026');
    await expect(body).toContainText('Sami Al-Khatib');
    await expect(body.locator('a[href="tel:+962790001234"]')).toBeVisible();
    await expect(body.locator('a[href="https://wa.me/962790001234"]')).toBeVisible();
    await expect(body).toContainText('CliQ or bank transfer');
    await expect(body.getByRole('button', { name: 'Download' })).toHaveCount(2);
    await expect(body).toContainText('ready@shop.test');
  });

  test('last day and ended trial wording', async ({ page }) => {
    await signIn(page, 'last@shop.test');
    await expect(page.locator('.acct-trial')).toContainText('last day of your free trial');
    await page.request.post(`${ON}/api/account/sign-out`, { headers: { origin: ON, 'x-aura-account': '1', 'content-type': 'application/json' }, data: {} });
    await signIn(page, 'ended@shop.test');
    await expect(page.locator('.acct-trial')).toContainText('Your shop data is kept safe');
    await expect(page.locator('.acct-trial')).toHaveAttribute('data-state', 'ENDED');
  });

  test('sign out ends the session in the browser and on the next call', async ({ page, context }) => {
    await signIn(page, 'ready@shop.test');
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/sign-in\/\?n=signed-out/);
    await expect(page.locator('#acct-msg')).toContainText('signed out');
    expect((await context.cookies()).find((c) => c.name === COOKIE)).toBeUndefined();
    await page.goto(`${ON}/en/account/`);
    await expect(page).toHaveURL(/sign-in/);
  });

  test('account pages are never cached or indexed', async ({ page }) => {
    const res = await page.goto(`${ON}/en/account/sign-in/`);
    expect(res!.headers()['cache-control']).toBe('no-store');
    expect(res!.headers()['x-robots-tag']).toContain('noindex');
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex');
  });
});

test.describe('downloads', () => {
  test('a short-lived link streams the whole file, byte for byte, and is refused after 3 uses', async ({ page }) => {
    const sha = ((await (await fetch(`${OWNER}/__sha`)).json()) as { sha256: string }).sha256;
    await signIn(page, 'ready@shop.test');
    const downloadPromise = page.waitForEvent('download');
    await page.locator('.acct-download').first().getByRole('button', { name: 'Download' }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('AuraRetail-Setup-1.0.0-rc.17.exe');
    const path = await download.path();
    const bytes = fs.readFileSync(path);
    expect(bytes.length).toBe(3 * 1024 * 1024);
    expect(crypto.createHash('sha256').update(bytes).digest('hex')).toBe(sha);

    // Get one link by hand and use it: 3 uses work, the 4th is sent back to the downloads page.
    const auth = await page.request.post(`${ON}/api/account/downloads/authorize`, {
      headers: { origin: ON, 'x-aura-account': '1', 'content-type': 'application/json', cookie: await sessionCookieHeader(page) }, data: { offerId: 'apk-1', lang: 'en' },
    });
    const { url } = (await auth.json()) as { url: string };
    expect(url).toMatch(/^\/api\/account\/downloads\/fetch\?t=/);
    for (let i = 0; i < 3; i++) {
      const r = await page.request.get(ON + url);
      expect(r.status(), `use ${i + 1}`).toBe(200);
      expect(r.headers()['content-type']).toBe('application/vnd.android.package-archive');
      expect(r.headers()['content-disposition']).toBe('attachment; filename="AuraRetail-1.0.0-rc.17.apk"');
      expect((await r.body()).length).toBe(3 * 1024 * 1024);
    }
    const fourth = await page.request.get(ON + url, { maxRedirects: 0 });
    expect(fourth.status()).toBe(303);
    expect(fourth.headers()['location']).toBe('/en/account/downloads/?e=expired');
    await page.goto(ON + '/en/account/downloads/?e=expired');
    await expect(page.locator('#acct-msg')).toContainText('expired');
    await expect(page.locator('#dl-list .acct-download')).toHaveCount(2);
    await expect(page.locator('.acct-steps li')).toHaveCount(4);
  });

  test('a made-up or tampered link never reaches a file', async ({ request }) => {
    for (const q of ['t=nope', 't=' + 'a'.repeat(40), 't=../../etc/passwd']) {
      const r = await request.get(`${ON}/api/account/downloads/fetch?${q}`, { maxRedirects: 0 });
      expect(r.status(), q).toBe(303);
    }
  });
});

test.describe('forgot password', () => {
  test('same confirmation for a real and an unknown address', async ({ page }) => {
    for (const email of ['ready@shop.test', 'ghost@nowhere.test']) {
      await page.goto(`${ON}/en/account/forgot/`);
      await page.fill('#f-email', email);
      await page.click('#acct-submit');
      await expect(page.locator('#acct-msg')).toContainText('If that email has an account');
      await expect(page.locator('#acct-form')).toBeHidden();
    }
    const asked = (await ownerLog()).filter((l) => 'forgot' in l).map((l) => l.forgot);
    expect(asked).toEqual(['ready@shop.test', 'ghost@nowhere.test']);
  });
});

test.describe('arabic', () => {
  test('right to left, Arabic words, Western digits, same flow', async ({ page }) => {
    await page.goto(`${ON}/ar/account/sign-in/`);
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
    await expect(page.locator('html')).toHaveAttribute('lang', 'ar');
    await expect(page.locator('#acct-heading')).toHaveText('سجّل الدخول إلى حسابك في أورا');
    await page.fill('#f-email', 'ar@shop.test');
    await page.fill('#f-password', 'wrong-one-123');
    await page.click('#acct-submit');
    await expect(page.locator('#acct-msg')).toContainText('غير صحيحين');
    await page.fill('#f-password', GOOD);
    await page.click('#acct-submit');
    await expect(page).toHaveURL(/\/ar\/account\/$/);
    const root = page.locator('#acct-root');
    await expect(root).toContainText('بقي 5 أيام');
    await expect(root).toContainText('7 نوفمبر 2026');
    await expect(root).toContainText('أورا ريتيل');
    expect(await root.innerText()).not.toMatch(/[٠-٩]/);
    await expect(root.getByRole('button', { name: 'تنزيل' })).toHaveCount(2);
  });

  test('the language switch keeps you on the same account page', async ({ page }) => {
    await page.goto(`${ON}/en/account/forgot/`);
    await page.locator('.nav-links .nav-lang').click();
    await expect(page).toHaveURL(/\/ar\/account\/forgot\/$/);
  });
});

test.describe('accessibility', () => {
  for (const lang of ['en', 'ar'] as const) {
    test(`axe: sign-in, forgot, set-password and the account page (${lang})`, async ({ page }) => {
      for (const sub of ['sign-in', 'forgot', 'set-password']) {
        await page.goto(`${ON}/${lang}/account/${sub}/`);
        await page.waitForTimeout(300);
        const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
        expect(r.violations.map((v) => `${v.id} ${v.nodes[0]?.target}`), `${lang} ${sub}`).toEqual([]);
      }
      await signIn(page, lang === 'ar' ? 'ar@shop.test' : 'ready@shop.test', GOOD, lang);
      await expect(page.locator('#acct-root')).toBeVisible();
      const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      expect(r.violations.map((v) => `${v.id} ${v.nodes[0]?.target}`), `${lang} account`).toEqual([]);
    });
  }
});

test.describe('server-side guards, end to end', () => {
  const post = (request: any, headers: Record<string, string>, data: unknown = { email: 'ready@shop.test', password: GOOD }) =>
    request.post(`${ON}/api/account/sign-in`, { headers, data, maxRedirects: 0 });

  test('CSRF: a post without our origin or header never signs anyone in', async ({ request }) => {
    expect((await post(request, { 'content-type': 'application/json' })).status()).toBe(403);
    expect((await post(request, { 'content-type': 'application/json', origin: 'https://evil.example', 'x-aura-account': '1' })).status()).toBe(403);
    expect((await post(request, { 'content-type': 'application/json', origin: ON })).status()).toBe(403);
    expect((await post(request, { 'content-type': 'text/plain', origin: ON, 'x-aura-account': '1' })).status()).toBe(403);
    expect((await ownerLog()).filter((l) => l.action === 'sign-in')).toHaveLength(0);
    const ok = await post(request, { 'content-type': 'application/json', origin: ON, 'x-aura-account': '1' });
    expect(ok.status()).toBe(200);
    expect(JSON.stringify(await ok.json())).not.toMatch(/session|token/i);
  });

  test('GET on an API route is a 404, not a 405', async ({ request }) => {
    expect((await request.get(`${ON}/api/account/sign-in`)).status()).toBe(404);
    expect((await request.get(`${ON}/api/account/summary`)).status()).toBe(404);
  });

  test('every request Owner received carried a signature it accepted (none rejected)', async ({ page }) => {
    await signIn(page, 'ready@shop.test');
    await expect(page.locator('#acct-root')).toBeVisible();
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/signed-out/);
    const log = await ownerLog();
    expect(log.filter((l) => 'rejected' in l)).toEqual([]);
    expect(log.map((l) => l.action)).toEqual(expect.arrayContaining(['sign-in', 'summary', 'sign-out']));
    expect(log.every((l) => l.ip === '127.0.0.1')).toBe(true);
  });
});

test.describe('the feature flag', () => {
  test('OFF everywhere (today\'s public site): no link, no page, no API, not in the sitemap', async ({ request }) => {
    const home = await (await request.get(`${OFF}/en/`)).text();
    expect(home).not.toMatch(/\/account\//);
    expect(home).not.toContain('Sign in');
    for (const p of ['/en/account/', '/en/account/sign-in/', '/ar/account/', '/ar/account/downloads/', '/en/account/forgot/', '/en/account/set-password/']) {
      expect((await request.get(OFF + p)).status(), p).toBe(404);
    }
    const apiHeaders = { origin: OFF, 'x-aura-account': '1', 'content-type': 'application/json' };
    for (const a of ['sign-in', 'sign-out', 'summary', 'forgot', 'set-password', 'change-password', 'downloads/authorize']) {
      expect((await request.post(`${OFF}/api/account/${a}`, { headers: apiHeaders, data: { email: 'ready@shop.test', password: GOOD } })).status(), a).toBe(404);
    }
    expect((await request.get(`${OFF}/api/account/downloads/fetch?t=${'a'.repeat(30)}`, { maxRedirects: 0 })).status()).toBe(404);
    expect(await (await request.get(`${OFF}/sitemap-0.xml`)).text()).not.toContain('/account');
    expect((await ownerLog()).length, 'Owner was never contacted').toBe(0);
  });

  test('runtime switch OFF on a build that has the pages: pages and API still 404, Owner untouched', async ({ request }) => {
    for (const p of ['/en/account/', '/ar/account/sign-in/', '/en/account/downloads/']) {
      expect((await request.get(BUILT_ON_RUNTIME_OFF + p)).status(), p).toBe(404);
    }
    const r = await request.post(`${BUILT_ON_RUNTIME_OFF}/api/account/sign-in`, {
      headers: { origin: BUILT_ON_RUNTIME_OFF, 'x-aura-account': '1', 'content-type': 'application/json' }, data: { email: 'ready@shop.test', password: GOOD },
    });
    expect(r.status()).toBe(404);
    expect((await ownerLog()).length).toBe(0);
  });

  test('ON: the site shows a Sign in link in the nav and footer, and the account pages are out of the sitemap', async ({ request, page }) => {
    await page.goto(`${ON}/en/`);
    await expect(page.locator('.nav-links a.nav-account')).toHaveAttribute('href', '/en/account/');
    await expect(page.locator('footer a[href="/en/account/"]')).toHaveCount(1);
    expect(await (await request.get(`${ON}/sitemap-0.xml`)).text()).not.toContain('/account');
  });
});
