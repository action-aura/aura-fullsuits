import { test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Screenshots for the owner (English and Arabic, desktop and phone). Not part of the pass/fail suite: it only
// runs when SHOTS_DIR is set, e.g.  SHOTS_DIR=C:\...\s4\screenshots npx playwright test -c playwright.accounts.config.ts shots
const DIR = process.env.SHOTS_DIR;
const ON = 'http://127.0.0.1:8790';
const OWNER = 'http://127.0.0.1:8799';
const GOOD = 'Shop-pass-2026-ok';

test.skip(!DIR, 'set SHOTS_DIR to take screenshots');
test.beforeEach(async () => { await fetch(`${OWNER}/__reset`, { method: 'POST' }); });

const sizes = { desktop: { width: 1280, height: 860 }, phone: { width: 390, height: 844 } } as const;

async function shoot(page: Page, name: string) {
  fs.mkdirSync(DIR!, { recursive: true });
  await page.waitForTimeout(700); // fonts + the 0.6 s colour transition
  await page.screenshot({ path: path.join(DIR!, `${name}.png`), fullPage: true });
}

for (const [device, size] of Object.entries(sizes)) {
  for (const lang of ['en', 'ar'] as const) {
    test(`${device} ${lang}`, async ({ browser }) => {
      const ctx = await browser.newContext({ viewport: size, deviceScaleFactor: device === 'phone' ? 2 : 1 });
      const page = await ctx.newPage();
      const tag = `${lang}-${device}`;
      const email = lang === 'ar' ? 'ar@shop.test' : 'ready@shop.test';

      await page.goto(`${ON}/${lang}/account/sign-in/`);
      await shoot(page, `${tag}-1-sign-in`);
      await page.fill('#f-email', email);
      await page.fill('#f-password', 'wrong-one-123');
      await page.click('#acct-submit');
      await page.waitForSelector('#acct-msg:not([hidden])');
      await shoot(page, `${tag}-2-sign-in-wrong-password`);

      await page.goto(`${ON}/${lang}/account/forgot/`);
      await shoot(page, `${tag}-3-forgot`);

      // First sign-in with a temporary password: the forced change page.
      await page.goto(`${ON}/${lang}/account/sign-in/`);
      await page.fill('#f-email', 'temp@shop.test');
      await page.fill('#f-password', 'Temp-pass-2026');
      await page.click('#acct-submit');
      await page.waitForURL(/forced=1/);
      await shoot(page, `${tag}-4-forced-password-change`);
      await page.fill('#f-current', 'Temp-pass-2026');
      await page.fill('#f-next', 'short');
      await page.fill('#f-again', 'short');
      await page.click('#acct-submit');
      await page.waitForSelector('#acct-msg:not([hidden])');
      await shoot(page, `${tag}-5-password-too-short`);
      await ctx.clearCookies();

      await page.goto(`${ON}/${lang}/account/sign-in/`);
      await page.fill('#f-email', email);
      await page.fill('#f-password', GOOD);
      await page.click('#acct-submit');
      await page.waitForSelector('#acct-root:not([hidden])');
      await shoot(page, `${tag}-6-account-trial-running`);
      await page.goto(`${ON}/${lang}/account/downloads/`);
      await page.waitForSelector('#acct-root:not([hidden])');
      await shoot(page, `${tag}-7-downloads`);
      await ctx.clearCookies();

      await page.goto(`${ON}/${lang}/account/sign-in/`);
      await page.fill('#f-email', 'ended@shop.test');
      await page.fill('#f-password', GOOD);
      await page.click('#acct-submit');
      await page.waitForSelector('#acct-root:not([hidden])');
      await shoot(page, `${tag}-8-account-trial-ended`);
      await ctx.close();
    });
  }
}
