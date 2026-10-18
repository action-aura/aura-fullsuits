import { test, expect, type Page, type Route } from '@playwright/test';

const PHONE_AR = '٠٧٨٠٠٦٩٤٨٥';
const NAME = 'Test Customer';

// Fake the Pages Function. Returns a live counter so tests can assert
// "no request was made" / "the request ran", not just an outcome.
async function fakeDemo(page: Page, handler: (route: Route) => Promise<void> | void) {
  const hits = { count: 0 };
  await page.route('**/api/demo', async (route) => {
    hits.count += 1;
    await handler(route);
  });
  return hits;
}

const json = (status: number, body: unknown) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(body),
});

async function open(page: Page) {
  await page.goto('/en/contact/');
  await expect(page.locator('form#demo-form')).toBeVisible();
}

async function fillValid(page: Page) {
  await page.fill('#demo-form [name="name"]', NAME);
  await page.fill('#demo-form [name="phone"]', PHONE_AR);
}

test('empty submit shows name and phone errors and sends nothing', async ({ page }) => {
  const hits = await fakeDemo(page, (route) => route.fulfill(json(200, { ok: true })));
  await open(page);
  await page.click('button#demo-send');
  await expect(page.locator('#name-error')).not.toHaveText('');
  await expect(page.locator('#phone-error')).not.toHaveText('');
  // Give a wrongly-fired fetch time to land before asserting none did.
  await page.waitForTimeout(500);
  expect(hits.count).toBe(0);
  await expect(page.locator('#demo-success')).toBeHidden();
});

test('Arabic-Indic phone digits are accepted and 200 shows focused success panel', async ({ page }) => {
  const hits = await fakeDemo(page, (route) => route.fulfill(json(200, { ok: true })));
  await open(page);
  await fillValid(page);
  await page.click('button#demo-send');
  await expect(page.locator('#demo-success')).toBeVisible();
  await expect(page.locator('#demo-success')).toBeFocused();
  expect(hits.count).toBe(1);
});

test('429 shows the error box and keeps typed values', async ({ page }) => {
  const hits = await fakeDemo(page, (route) => route.fulfill(json(429, { ok: false, error: 'rate' })));
  await open(page);
  await fillValid(page);
  await page.click('button#demo-send');
  await expect(page.locator('#demo-error')).toBeVisible();
  await expect(page.locator('#demo-form [name="name"]')).toHaveValue(NAME);
  await expect(page.locator('#demo-form [name="phone"]')).toHaveValue(PHONE_AR);
  await expect(page.locator('#demo-success')).toBeHidden();
  expect(hits.count).toBe(1);
});

test('aborted request shows error box with WhatsApp link and keeps values', async ({ page }) => {
  const hits = await fakeDemo(page, (route) => route.abort());
  await open(page);
  await fillValid(page);
  await page.click('button#demo-send');
  await expect(page.locator('#demo-error')).toBeVisible();
  await expect(page.locator('#demo-error a[href^="https://wa.me/"]')).toBeVisible();
  await expect(page.locator('#demo-form [name="name"]')).toHaveValue(NAME);
  await expect(page.locator('#demo-form [name="phone"]')).toHaveValue(PHONE_AR);
  await expect(page.locator('button#demo-send')).toBeEnabled();
  expect(hits.count).toBe(1);
});

test('no-JS: /contact/sent/ shows its heading and /contact/#form-error shows the error block', async ({ browser }) => {
  const ctx = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await ctx.newPage();
    await page.goto('http://localhost:4321/en/contact/sent/');
    await expect(page.locator('#sent-title')).toBeVisible();
    // No hash: the :target rule must keep the block hidden, else the hash check below proves nothing.
    await page.goto('http://localhost:4321/en/contact/');
    await expect(page.locator('#form-error')).not.toBeVisible();
    await page.goto('http://localhost:4321/en/contact/#form-error');
    await expect(page.locator('#form-error')).toBeVisible();
  } finally {
    await ctx.close();
  }
});

test('send button is disabled while the request is pending', async ({ page }) => {
  const hits = await fakeDemo(page, async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.fulfill(json(200, { ok: true }));
  });
  await open(page);
  await fillValid(page);
  await page.click('button#demo-send');
  await expect(page.locator('button#demo-send')).toBeDisabled();
  expect(hits.count).toBe(1);
  await expect(page.locator('#demo-success')).toBeVisible({ timeout: 5000 });
});
