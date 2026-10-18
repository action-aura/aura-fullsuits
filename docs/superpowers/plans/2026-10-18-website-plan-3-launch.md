# Action Aura website, Plan 3: launch assets and deploy readiness

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship every asset, job and check the site still lacks so that it can go live the moment the owner opens their Cloudflare session. The list: the jewel stills, the favicon set, share images, robots/headers/llms, the 24-month deletion job, the email notification, analytics, CI, audits, photos and the deploy runbook.

**Architecture:** The site stays Astro static with one Pages Function. New build-time tools live in `website/tools/` and run with Playwright's Chromium against the built `dist/`. Their outputs are committed into `website/public/`. The deletion job is a separate, tiny Cloudflare Worker (`website/retention/`) with a cron trigger, bound to the same D1 database. Pages Functions cannot run on a schedule.

**Tech Stack:**
- Astro 5.18.2 and TypeScript 5.9.3.
- Vitest 3.2.7 and @playwright/test 1.56.1.
- Added in this plan, all pinned exactly, each released 14 or more days before 2026-10-18:
  - wrangler 4.145.0 (dev)
  - worker-mailer 1.2.1
  - @axe-core/playwright 4.13.0 (dev)
- lighthouse 13.5.0 runs through npx only and is not a dependency.

**Spec:** `docs/superpowers/specs/2026-10-18-action-aura-website-design.md` (sections 5, 7, 8, 9, 10, 11, 13). The binding design doc is `website/docs/design-system.md`.

## Global Constraints

- **Execution model (owner rule):**
  - Fable THINKS: design decisions and spec deltas.
  - cavecrew-builder EXECUTES: at most 2 files per dispatch, no shell.
  - The main session runs every command and verifies.
- **No prices, testimonials, iOS, JoFotara/e-invoicing, cash drawer or Android printing claims** in any copy, including `llms.txt`, OG cards and alt text.
- **Copy rules (unchanged from Plans 1-2):** no em-dash, no "!", Western digits only.
- **Mark rules:** the mark is never mirrored in RTL. The flat mark is used below 64 px.
- **"Invisible unless opted in":** analytics, email notification and Turnstile render or run only when their env var is set. With nothing set, the site still builds and works.
- **Secrets:** never in the repo.
  - `.dev.vars` is gitignored.
  - Real values (`TURNSTILE_SECRET`, `IP_SALT`, `SMTP_PASS`, analytics token) are set by the owner in the Cloudflare dashboard during the gated deploy step.
- **No DNS change, no Cloudflare account action, no publish** without the owner present and saying yes at that step (spec section 10).
- **Engineering standard:**
  - Mutation-prove every new guard.
  - Never weaken a test.
  - Run the real artefact (the built site, wrangler dev) before calling a task done.
  - Commit with an explicit path list only (never a whole directory).
- **Paths:**
  - Worktree root: `C:\Users\MSI\Desktop\aura-fullsuits\.claude\worktrees\website`.
  - Site root: `website/` inside it.
  - All commands run from `website/` unless stated otherwise.
- **Commands:**
  - `astro check`: `npx -y node@22.12.0 node_modules/astro/astro.js check`, because Node 20.18 crashes with ERR_REQUIRE_ESM.
  - Unit tests: `npx vitest run`.
  - Build: `npm run build`.

## Review Focus

1. **Still and live mark misaligned.** On the lite tier, or when the live scene fades in over the still, the mark must not jump. The still's mark must sit where the canvas mark sits. Pinned by Task 3 Step 5: a pixel-box comparison test.
2. **Arabic OG card text.** Letters must be joined, words in right-to-left order, and the text not clipped. Pinned by Task 4 Step 5: an eye check of the contact sheet, plus a test that every card exists at 1200x630.
3. **`*.pages.dev` preview indexed by Google.** The preview host must send `X-Robots-Tag: noindex` and the real domain must not. Pinned by Task 1 (test of the `_headers` rule) and Task 9 (served-headers check under wrangler).
4. **Deletion job removes the wrong rows, or none.** A lead aged 23 months must survive and one aged 25 months must go. Pinned by Task 6: both-direction sqlite check plus a mutation.
5. **Security headers break the form.** CSP must not block Turnstile, the beacon, the form post or the WhatsApp hand-off. Pinned by Task 9: real pages served with `_headers` and zero CSP console violations.

---

### Task 1: Static launch files (robots, headers, llms, manifest)

**Files:**
- Create: `website/public/robots.txt`
- Create: `website/public/_headers`
- Create: `website/public/llms.txt`
- Create: `website/public/site.webmanifest`
- Test: `website/tests/unit/launch-files.test.ts`

**Interfaces:**
- Produces: `/site.webmanifest`, which references `/icon-192.png` and `/icon-512.png` (created in Task 2).

- [ ] **Step 1: Write the failing test** `website/tests/unit/launch-files.test.ts`:

```ts
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
    expect(l).not.toMatch(/\u2014|!/);
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
```

- [ ] **Step 2: Run it to make sure it fails.** Run `npx vitest run tests/unit/launch-files.test.ts`. Expected: it FAILS with ENOENT on `public/robots.txt`.

- [ ] **Step 3: Create the four files.**

`website/public/robots.txt`:
```
User-agent: *
Allow: /
Disallow: /api/

Sitemap: https://actionaura.me/sitemap-index.xml
```

`website/public/_headers` (Cloudflare Pages syntax):
```
# The *.pages.dev preview must never be indexed; the real domain must be.
https://:project.pages.dev/*
  X-Robots-Tag: noindex

/*
  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com https://static.cloudflareinsights.com; frame-src https://challenges.cloudflare.com; connect-src 'self' https://cloudflareinsights.com https://challenges.cloudflare.com; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
  X-Frame-Options: DENY
  Strict-Transport-Security: max-age=31536000; includeSubDomains

/_astro/*
  Cache-Control: public, max-age=31536000, immutable
```

(Ruling, recorded in the ledger: `script-src` keeps `'unsafe-inline'`. Astro inlines small scripts and the theme-class bootstrap. A static site cannot carry per-request nonces, and hash upkeep on every build is a trap. Cost if wrong: weaker XSS defence, mitigated by having no user-generated HTML anywhere.)

`website/public/llms.txt`: write the content from `website/docs/messaging.md` and `website/docs/clinic-claims.md`, using SHIPPED claims only. Exact text:
```
# Action Aura

> Action Aura is a software company in Amman, Jordan. It builds Arabic-first systems for shops and clinics that keep working when the internet drops.

## Products

- [Aura Retail](https://actionaura.me/en/aura-retail/): a till, stock and accounts system for shops and showrooms. It runs on the shop's Windows computer and on an Android phone, keeps both in sync over the shop's own network, and keeps selling without internet. Barcode scanners and receipt printers connect to the shop computer. Amounts are kept to the fils, with three decimal places.
- [Aura Clinic](https://actionaura.me/en/aura-clinic/): patient files, appointments, tooth charts and patient accounts for dental clinics. It runs on the clinic's Windows computer, with an Android phone app that syncs with it, and works offline.

## Coming

- Aura Gym, and further Aura platforms, are in development and not available yet.

## Contact

- WhatsApp or phone: +962 78 006 9485
- Email: info@actionaura.me
- Office: Al-Rayyan Complex, Amman, Jordan
- Book a free demo: https://actionaura.me/en/contact/

## Arabic

- https://actionaura.me/ar/
```

`website/public/site.webmanifest`:
```json
{
  "name": "Action Aura",
  "short_name": "Action Aura",
  "start_url": "/",
  "display": "browser",
  "background_color": "#0E1729",
  "theme_color": "#0E1729",
  "icons": [
    { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

- [ ] **Step 4: Run the test.** Run `npx vitest run tests/unit/launch-files.test.ts`. Expected: PASS.

- [ ] **Step 5: Mutation-prove the noindex rule.** Move `X-Robots-Tag: noindex` under `/*`. The "preview host only" test must go red. Restore it.

- [ ] **Step 6: Build and confirm the files are copied.** Run `npm run build`, then `ls dist/robots.txt dist/_headers dist/llms.txt dist/site.webmanifest`. Expected: all four exist.

- [ ] **Step 7: Commit**
```bash
git add website/public/robots.txt website/public/_headers website/public/llms.txt website/public/site.webmanifest website/tests/unit/launch-files.test.ts
git commit -m "feat(website): robots, security headers with pages.dev noindex, llms.txt, web manifest"
```

---

### Task 2: Favicon set from the brand kit's flat mark

**Files:**
- Create: `website/tools/make-icons.mjs`
- Create (generated, committed): `website/public/favicon.svg`, `favicon-32.png`, `apple-touch-icon.png`, `icon-192.png`, `icon-512.png`
- Modify: `website/tests/unit/launch-files.test.ts` (append)

**Interfaces:**
- Consumes: `products/retail/frontend/brand/aura-app-icon.svg` (512x512 navy rounded square) and `aura-mark-flat-dark.svg` (flat mark for dark grounds).
- Produces: the five files above. `Base.astro` already links `favicon.svg`, `favicon-32.png` and `apple-touch-icon.png`. Task 1's manifest links `icon-192.png` and `icon-512.png`.

- [ ] **Step 1: Append the failing test** to `launch-files.test.ts`:

```ts
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
```

- [ ] **Step 2: Run it to make sure it fails.** Run `npx vitest run tests/unit/launch-files.test.ts`. Expected: the favicon tests FAIL with ENOENT.

- [ ] **Step 3: Build the favicon SVG.** The builder reads `aura-mark-flat-dark.svg` and writes `public/favicon.svg`, laid out like this:
  - A 512x512 viewBox.
  - `<rect width="512" height="512" rx="112" fill="#0E1729"/>`. Same tile as the app icon. The fill is the site's `--theme` navy, as used in the `Base.astro` `theme-color`.
  - The flat mark's paths, inside a `<g>` scaled and centred so that the mark's bounding box fills 68% of the tile height.
  - Copy the paths verbatim. No gradients or filters.

  Then write `website/tools/make-icons.mjs`:

```js
// Rasterises public/favicon.svg to the PNG sizes the head and manifest link.
// Run from website/: node tools/make-icons.mjs
import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const svg = fs.readFileSync(path.resolve('public/favicon.svg'), 'utf8');
const sizes = { 'favicon-32.png': 32, 'apple-touch-icon.png': 180, 'icon-192.png': 192, 'icon-512.png': 512 };
const browser = await chromium.launch();
for (const [file, n] of Object.entries(sizes)) {
  const page = await browser.newPage({ viewport: { width: n, height: n }, deviceScaleFactor: 1 });
  await page.setContent(`<html><body style="margin:0;background:transparent">${svg.replace('<svg', `<svg width="${n}" height="${n}"`)}</body></html>`);
  await page.screenshot({ path: path.resolve('public', file), omitBackground: true, clip: { x: 0, y: 0, width: n, height: n } });
  await page.close();
}
await browser.close();
console.log('icons written');
```

- [ ] **Step 4: Run the tool and the test.** Run `node tools/make-icons.mjs`, then `npx vitest run tests/unit/launch-files.test.ts`. Expected: `icons written`, and the tests PASS. Look at `public/favicon-32.png` and `apple-touch-icon.png` by eye. The A must be clean and recognisable at 32 px.

- [ ] **Step 5: Commit**
```bash
git add website/tools/make-icons.mjs website/public/favicon.svg website/public/favicon-32.png website/public/apple-touch-icon.png website/public/icon-192.png website/public/icon-512.png website/tests/unit/launch-files.test.ts
git commit -m "feat(website): favicon set from the flat brand mark"
```

---

### Task 3: Jewel stills (lite and reduced-motion tiers)

**Files:**
- Spec delta (Fable, job tmp): `C:\Users\MSI\.claude\jobs\b6c06c3b\tmp\website\spec-delta-stills.md`
- Create: `website/tools/render-stills.mjs`
- Create (generated, committed): `website/public/jewel/still-master.webp`, `still-retail.webp`, `still-clinic.webp`, `still-gym.webp`
- Modify, only if the delta requires it: `website/src/components/Jewel.astro` (img width/height) and `website/src/styles/components.css` (`.jewel-still` box)
- Test: `website/tests/site/behaviour.spec.ts` (append)

**Interfaces:**
- Consumes:
  - the `scene.js` `mountJewel(canvas, { phone })`, with `setMix` and `setStation`;
  - the `.jewel-still` CSS box: height `58vh * --js`, centred at `--jx`/`--jy`;
  - the root `worldmix` event and `root.__auraMix`.
- Produces: `/jewel/still-<world>.webp` for the worlds master, retail, clinic and gym.

- [ ] **Step 1: Fable writes the stills delta.** It must specify:
  - (a) The capture method: drive the built site's live canvas with Playwright; set the mix via `root.__auraMix` plus a dispatched `worldmix` event; screenshot with `omitBackground: true`.
  - (b) The exact capture viewport, the station, and the clip box in CSS px, chosen so that the still's mark box equals the canvas mark box at the same `--jx/--jy/--js`.
  - (c) The still's pixel size and aspect. Today `Jewel.astro` declares 900x1200, which is narrower than the ring (7.66/9.14 of the height plus the ring overhang). If the frame must change, the delta gives the new width/height and the CSS change.
  - (d) The WebP quality and a size budget: 120 KB or less per still.
  - (e) The pose and lighting: the hero pose, at rest, no spin.
  - (f) The acceptance check for Step 5, as numbers.

  Fable may edit only `website/docs/design-system.md` (section 5) to match.

- [ ] **Step 2: Write the failing alignment test.** This is Review Focus 1. Append to `behaviour.spec.ts`; the exact code comes from the delta. Its shape:
  - Load `/en/` at 1440x900 twice: once forced to the still tier (`reducedMotion: 'reduce'`) and once rich.
  - Compute the mark's opaque-pixel bounding box in each screenshot: diff with `.jewel` hidden, as in `measure-stations.mjs` from the last delta.
  - Assert that the boxes agree to within the delta's tolerance (default 16 px on every edge).

  Run it. Expected: it FAILS, because the stills 404 and the still box is empty.

- [ ] **Step 3: The builder writes `tools/render-stills.mjs`** to the delta, plus the Jewel/CSS changes if the delta has any.

- [ ] **Step 4: Run the tool and the checks.** Run `npm run build`, then `node tools/render-stills.mjs`, then `npm run build` again. Then run `npx playwright test tests/site/behaviour.spec.ts --workers=1`. Expected:
  - four files under `public/jewel/`, each 120 KB or less;
  - the alignment test PASSES.

- [ ] **Step 5: Mutation-prove the test.** Offset the capture clip by 40 px and re-render one still. The alignment test must go red. Restore the clip and re-render.

- [ ] **Step 6: Re-run the reduce-tier contrast.** Run `npx playwright test tests/site/shots.spec.ts -g "reduce" --workers=2`, then `py -3.14 tools/contrast.py shots`. Expected: 0 failing. This closes the reduce-tier expected-red from Plan 1.

- [ ] **Step 7: Commit** the tool, the four webp files, the spec file and any Jewel/CSS edits, each by explicit path:
```
feat(website): jewel stills for lite and reduced-motion tiers
```

---

### Task 4: Share (OG) images, one per page and language

**Files:**
- Spec delta (Fable, job tmp): `C:\Users\MSI\.claude\jobs\b6c06c3b\tmp\website\spec-delta-og.md`
- Create: `website/tools/og/card.html`
- Create: `website/tools/make-og.mjs`
- Create (generated, committed): `website/public/og/{ar,en}-{home,aura-retail,aura-clinic,about,faq,contact,privacy,terms}.png` (16 files)
- Modify: `website/tests/unit/launch-files.test.ts` (append)

**Interfaces:**
- Consumes:
  - the built `dist/**/index.html`: `og:title`, `<html lang>`, `data-world`, and the `og:image` file name;
  - `public/jewel/still-<world>.webp` (Task 3);
  - the fonts from `node_modules/@fontsource/{outfit,readex-pro}`.
- Produces: the 16 PNGs whose URLs `Base.astro` already emits as `https://actionaura.me/og/<lang>-<slug>.png`.

- [ ] **Step 1: Fable writes the OG card delta.** It must specify:
  - the 1200x630 layout per world;
  - where the jewel still sits;
  - the title type: Outfit for EN, Readex Pro for AR, with sizes and a two-line maximum;
  - RTL handling for AR: `dir="rtl"` on the card, and the mark is not mirrored;
  - the wordmark;
  - the safe area (no text within 60 px of an edge);
  - the exact `card.html` markup and CSS;
  - the query parameters `make-og.mjs` passes (`title`, `lang`, `world`).

  It must also cover how `make-og.mjs` reads `dist/` (via `<meta property="og:title">` and `<meta property="og:image">`). This means no TypeScript import and a single source of truth.

- [ ] **Step 2: Append the failing test.**

```ts
describe('OG images', () => {
  const slugs = ['home', 'aura-retail', 'aura-clinic', 'about', 'faq', 'contact', 'privacy', 'terms'];
  it.each(['ar', 'en'].flatMap((l) => slugs.map((s) => `og/${l}-${s}.png`)))('%s is 1200x630', (f) => {
    expect(pngSize(f)).toEqual([1200, 630]);
  });
});
```

  Run it. Expected: it FAILS with ENOENT.

- [ ] **Step 3: The builder writes `tools/og/card.html` and `tools/make-og.mjs`** to the delta. One builder dispatch covers both files.

- [ ] **Step 4: Generate and test.** Run `npm run build`, then `node tools/make-og.mjs`, then `npx vitest run tests/unit/launch-files.test.ts`. Expected: 16 files, and the tests PASS.

- [ ] **Step 5: Eye check.** This is Review Focus 2. Build a contact sheet of all 16 cards in job tmp and look at it. The Arabic must be joined, in right-to-left order and not clipped. The world colours must match each page. Any defect goes back to Fable as a delta amendment.

- [ ] **Step 6: Commit** the tool, `card.html`, the 16 PNGs and the test:
```
feat(website): per-page share images in both languages
```

---

### Task 5: Remove the Plan-3 test exemptions

**Files:**
- Modify: `website/tests/site/links.spec.ts` (delete `EXPECTED_MISSING_UNTIL_PLAN_3` and its `continue`)
- Modify: `website/tests/site/shots.spec.ts` (delete `EXPECTED_MISSING` and its use in `ignorable`; keep the GPU-noise filter)

**Interfaces:**
- Consumes: all of Tasks 1-4's files.

- [ ] **Step 1: The builder deletes both exemption lists.** This is one dispatch, two files.
- [ ] **Step 2: Run the crawl.** Run `npm run build`, then `npx playwright test tests/site/links.spec.ts tests/site/head.spec.ts --workers=2`. Expected: PASS, with no missing internal link or asset.
- [ ] **Step 3: Mutation-prove the crawl.** Rename `public/og/en-faq.png`, then build and run `links.spec`. It must go red and name `/og/en-faq.png`. Restore the file.

  The crawl must follow `og:image` as an asset. If it does not, that is a defect in `links.spec`: add `meta[property="og:image"]` to its asset selectors in this same task.
- [ ] **Step 4: Commit** both spec files:
```
test(website): Plan-3 assets now required; crawl fails on any missing asset
```

---

### Task 6: 24-month deletion job (LAUNCH BLOCKER)

**Files:**
- Create: `website/retention/wrangler.toml`
- Create: `website/retention/index.ts`
- Create: `website/tests/unit/retention.test.ts`
- Create: `website/tools/check_retention_sql.py`

**Interfaces:**
- Consumes: the D1 `leads` table from `migrations/0001_leads.sql`. `created_at` is TEXT `datetime('now')` in UTC.
- Produces: `export const RETENTION_SQL: string` and `export default { scheduled(...) }`.

- [ ] **Step 1: Write the failing unit test** `tests/unit/retention.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import worker, { RETENTION_SQL } from '../../retention/index';

describe('retention worker', () => {
  it('deletes leads older than 24 months, nothing else', () => {
    expect(RETENTION_SQL).toBe("DELETE FROM leads WHERE created_at < datetime('now','-24 months')");
  });
  it('runs the statement once per scheduled tick and reports the count', async () => {
    const calls: string[] = [];
    const env = { DB: { prepare: (sql: string) => ({ run: async () => { calls.push(sql); return { meta: { changes: 3 } }; } }) } };
    const out = await worker.scheduled({} as ScheduledController, env as any, {} as ExecutionContext);
    expect(calls).toEqual([RETENTION_SQL]);
    expect(out).toEqual({ deleted: 3 });
  });
});
```

  Run `npx vitest run tests/unit/retention.test.ts`. Expected: it FAILS because the module is not found.

- [ ] **Step 2: Write the worker.** `website/retention/index.ts`:

```ts
// Scheduled deletion of demo leads older than 24 months. The Privacy page promises this
// retention; Pages Functions cannot run on a schedule, so this tiny Worker shares the D1
// binding and runs daily. Logs the count so a silent no-op is distinguishable from "ran".
export const RETENTION_SQL = "DELETE FROM leads WHERE created_at < datetime('now','-24 months')";

type Env = { DB: D1Database };

export default {
  async scheduled(_c: ScheduledController, env: Env, _ctx: ExecutionContext): Promise<{ deleted: number }> {
    const r = await env.DB.prepare(RETENTION_SQL).run();
    const deleted = Number(r.meta?.changes ?? 0);
    console.log(`retention: deleted ${deleted} lead(s) older than 24 months`);
    return { deleted };
  },
};
```

  `website/retention/wrangler.toml`:

```toml
# Daily deletion of leads older than 24 months (Privacy page promise).
# Deployed separately from the Pages project: `npx wrangler deploy -c retention/wrangler.toml`.
# database_id must equal the Pages project's D1 id (set at the gated deploy step).
name = "action-aura-lead-retention"
main = "index.ts"
compatibility_date = "2026-09-01"

[triggers]
crons = ["17 3 * * *"]

[[d1_databases]]
binding = "DB"
database_name = "aura-leads"
database_id = "SET-AT-DEPLOY"
```

- [ ] **Step 3: Write the both-direction check against real SQLite.** This is Review Focus 4. `website/tools/check_retention_sql.py`:

```python
"""Runs RETENTION_SQL from retention/index.ts against a real SQLite table:
a lead 23 months old must survive, one 25 months old must be deleted.
D1 is SQLite, so datetime() semantics are identical. Run: py -3.14 tools/check_retention_sql.py"""
import re, sqlite3, sys, pathlib
src = pathlib.Path('retention/index.ts').read_text(encoding='utf-8')
sql = re.search(r'RETENTION_SQL = "([^"]+)"', src).group(1)
db = sqlite3.connect(':memory:')
db.executescript(pathlib.Path('migrations/0001_leads.sql').read_text(encoding='utf-8'))
cols = [r[1] for r in db.execute('PRAGMA table_info(leads)')]
def add(age):
    vals = {c: None for c in cols if c != 'id'}
    vals.update(created_at=db.execute(f"SELECT datetime('now','{age}')").fetchone()[0], name='t', phone='+962790000000', lang='en', ip_hash='x')
    db.execute(f"INSERT INTO leads ({','.join(vals)}) VALUES ({','.join('?'*len(vals))})", list(vals.values()))
add('-23 months'); add('-25 months'); add('-1 days')
db.execute(sql)
left = sorted(r[0] for r in db.execute('SELECT created_at FROM leads'))
ok = len(left) == 2
print('kept', left, 'OK' if ok else 'FAIL'); sys.exit(0 if ok else 1)
```

- [ ] **Step 4: Run both checks.** Run `npx vitest run tests/unit/retention.test.ts`, then `py -3.14 tools/check_retention_sql.py`. Expected: PASS, and `kept [...] OK`, with 2 rows kept.

- [ ] **Step 5: Mutation-prove both directions.**
  - Change `-24 months` to `-24 days`. The python check prints FAIL, because the 23-month lead is wrongly deleted. The unit test also goes red.
  - Change `<` to `>`. The python check prints FAIL.
  - Restore after each change.

- [ ] **Step 6: Commit**
```
feat(website): daily 24-month lead deletion worker (privacy promise)
```

---

### Task 7: Email notification over SMTP (Namecheap Private Email)

**Ruling (spec section 8 "decided after a check"):** Cloudflare's `send_email` binding needs Email Routing enabled on the zone. That replaces the MX records and would break the live `info@` mailbox, which is hosted at Namecheap Private Email. So the route is SMTP through worker-mailer 1.2.1 (pinned, published 2025-11-15) to `mail.privateemail.com:465` (TLS). The webhook provider stays.

**Files:**
- Modify: `website/functions/_lib/notify.ts`
- Modify: `website/functions/env.d.ts`
- Modify: `website/package.json` (dependency `"worker-mailer": "1.2.1"`; the main session runs `npm install`)
- Test: `website/tests/unit/notify.test.ts` (create)

**Interfaces:**
- Produces:
  - `NOTIFY_PROVIDER` may be `'none' | 'webhook' | 'smtp'`;
  - new env `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `NOTIFY_TO`.
  - `notify()` keeps its signature and its never-throws contract.

- [ ] **Step 1: Write the failing test** `tests/unit/notify.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
const send = vi.fn();
const connect = vi.fn(async () => ({ send, close: vi.fn() }));
vi.mock('worker-mailer', () => ({ WorkerMailer: { connect } }));
import { notify } from '../../functions/_lib/notify';

const lead = { name: 'Sami', phone: '+962790000000', businessType: 'shop', businessName: '', branches: '', message: '', lang: 'ar' } as any;
const smtpEnv = { NOTIFY_PROVIDER: 'smtp', SMTP_HOST: 'mail.privateemail.com', SMTP_PORT: '465', SMTP_USER: 'info@actionaura.me', SMTP_PASS: 'x', NOTIFY_TO: 'info@actionaura.me' } as any;

beforeEach(() => { send.mockReset(); connect.mockClear(); });

describe('notify smtp', () => {
  it('sends one mail to NOTIFY_TO with the lead in the body', async () => {
    send.mockResolvedValue(undefined);
    expect(await notify(smtpEnv, lead, 7)).toEqual({ ok: true });
    expect(connect).toHaveBeenCalledWith(expect.objectContaining({ host: 'mail.privateemail.com', port: 465, secure: true, credentials: { username: 'info@actionaura.me', password: 'x' } }));
    const msg = send.mock.calls[0][0];
    expect(msg.to).toBe('info@actionaura.me');
    expect(msg.subject).toContain('#7');
    expect(msg.text).toContain('+962790000000');
  });
  it('reports, never throws, when the SMTP server fails', async () => {
    send.mockRejectedValue(new Error('auth failed'));
    expect(await notify(smtpEnv, lead, 7)).toEqual({ ok: false, error: 'auth failed' });
  });
  it('refuses to send with missing credentials', async () => {
    expect(await notify({ ...smtpEnv, SMTP_PASS: '' }, lead, 7)).toEqual({ ok: false, error: 'smtp config missing' });
    expect(connect).not.toHaveBeenCalled();
  });
  it('provider none still sends nothing', async () => {
    expect(await notify({ NOTIFY_PROVIDER: 'none' } as any, lead, 7)).toEqual({ ok: true });
    expect(connect).not.toHaveBeenCalled();
  });
});
```

  Run `npx vitest run tests/unit/notify.test.ts`. Expected: FAIL with "unknown provider".

- [ ] **Step 2: Install the dependency.** Add `"worker-mailer": "1.2.1"` to `dependencies`, then run `npm install --save-exact worker-mailer@1.2.1`. Confirm that `package-lock.json` resolves 1.2.1.

- [ ] **Step 3: Implement the provider in `notify.ts`.** Keep the existing none and webhook branches unchanged, and add this before the webhook check:

```ts
  if (env.NOTIFY_PROVIDER === 'smtp') {
    if (!env.SMTP_HOST || !env.SMTP_USER || !env.SMTP_PASS || !env.NOTIFY_TO) return { ok: false, error: 'smtp config missing' };
    try {
      const { WorkerMailer } = await import('worker-mailer');
      const mailer = await WorkerMailer.connect({
        host: env.SMTP_HOST,
        port: Number(env.SMTP_PORT || 465),
        secure: true,
        credentials: { username: env.SMTP_USER, password: env.SMTP_PASS },
        authType: 'plain',
      });
      await mailer.send({
        from: { name: 'Action Aura website', email: env.SMTP_USER },
        to: env.NOTIFY_TO,
        subject: `Demo request #${id}: ${lead.name}`,
        text: [`Name: ${lead.name}`, `Phone: ${lead.phone}`, `Business: ${lead.businessType || '-'} ${lead.businessName || ''}`.trim(), `Branches: ${lead.branches || '-'}`, `Language: ${lead.lang}`, '', lead.message || ''].join('\n'),
      });
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
  }
```

  Add the five optional string env fields to `functions/env.d.ts`.

- [ ] **Step 4: Run the tests.** Run `npx vitest run`, then the `astro check` command, then `npm run build`. Expected: all green, and the build does not bundle worker-mailer into the static output.

- [ ] **Step 5: Mutation-prove the guards.**
  - Delete the `smtp config missing` guard. The third test must go red.
  - Remove the try/catch. The second test must go red.
  - Restore after each change.

- [ ] **Step 6: Commit** `notify.ts`, `env.d.ts`, `package.json`, `package-lock.json` and `notify.test.ts`:
```
feat(website): SMTP lead notification via Namecheap Private Email (opt-in)
```

---

### Task 8: Cookieless analytics beacon (opt-in)

**Files:**
- Modify: `website/src/layouts/Base.astro` (before `</body>`)
- Modify: `website/tests/site/head.spec.ts` (append)

**Interfaces:**
- Consumes: the build-time env `PUBLIC_CF_BEACON_TOKEN`.
- Produces: the Cloudflare Web Analytics beacon script, only when the token is set.

- [ ] **Step 1: Append the failing test** to `head.spec.ts`. It reads the built `dist/en/index.html` and `dist/ar/index.html` and asserts:
  - no `static.cloudflareinsights.com` when the build ran without the token;
  - and, for a build with `PUBLIC_CF_BEACON_TOKEN=test-token`, exactly one `<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{"token":"test-token"}'>`.

  The second case runs as a separate `npm run build` in the main session, with the env set, before running the spec with `BEACON_EXPECT=test-token`. The test reads `process.env.BEACON_EXPECT` to choose which assertion applies.

- [ ] **Step 2: Implement in `Base.astro`.**

```astro
---
const beacon = import.meta.env.PUBLIC_CF_BEACON_TOKEN;
---
{beacon && <script defer is:inline src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon={JSON.stringify({ token: beacon })}></script>}
```

  Merge the frontmatter line into the existing frontmatter. Do not add a second fence.

- [ ] **Step 3: Run both builds and the spec,** with and without the token. Expected: PASS in both modes.

- [ ] **Step 4: Commit**
```
feat(website): opt-in cookieless Cloudflare Web Analytics beacon
```

---

### Task 9: Run the real stack locally (Pages Function, D1, `_headers`)

**Files:**
- Modify: `website/package.json`:
  - devDependency `"wrangler": "4.145.0"`;
  - scripts `"pages:dev": "wrangler pages dev dist --port 8788"` and `"d1:local": "wrangler d1 migrations apply aura-leads --local"`.
- Modify: `website/.gitignore` (add `.dev.vars` and `.wrangler/`)
- Create: `website/tests/site/stack.spec.ts`

**Interfaces:**
- Consumes:
  - `functions/api/demo.ts`;
  - `migrations/0001_leads.sql`;
  - `public/_headers`;
  - `.dev.vars`, created by the main session with `ALLOW_NO_TURNSTILE=1`, a random `IP_SALT`, and `NOTIFY_PROVIDER=none`.

- [ ] **Step 1: Write `stack.spec.ts`.** It targets `http://localhost:8788` through its own `test.use({ baseURL })`, and checks four things:
  - (a) `GET /en/` sends `Content-Security-Policy` and does NOT send `X-Robots-Tag`.
  - (b) Every page in `dist`, in both languages, loads with zero console messages matching `/Content Security Policy|Refused to/`. This is Review Focus 5.
  - (c) The form on `/en/contact/` is filled with a valid lead and submitted through the real Function. Then `#demo-success` is visible, and the main session confirms the row with `npx wrangler d1 execute aura-leads --local --command "SELECT name, phone FROM leads ORDER BY id DESC LIMIT 1"`.
  - (d) A no-JS POST of form fields with `Accept: text/html` gets a 303 to `/en/contact/sent/`.

- [ ] **Step 2: Bring the stack up and run the spec.** Run these in order:
  1. `npm install --save-exact -D wrangler@4.145.0`
  2. `npm run build`
  3. `npm run d1:local`
  4. Start `npm run pages:dev` detached, with Start-Process and a log file in job tmp.
  5. `npx playwright test tests/site/stack.spec.ts --workers=1`

  Expected: PASS, and the D1 row is present.

- [ ] **Step 3: Mutation-prove the CSP check.** Remove `https://challenges.cloudflare.com` from `script-src`. Set `PUBLIC_TURNSTILE_SITEKEY` to Cloudflare's published always-pass test sitekey `1x00000000000000000000AA`. Rebuild. Check (b) must go red on `/en/contact/`. Restore.

- [ ] **Step 4: Stop wrangler, then commit** `package.json`, `package-lock.json`, `.gitignore` and `stack.spec.ts`:
```
test(website): real Pages Function + D1 + headers under wrangler pages dev
```

---

### Task 10: CI workflow for the website

**Files:**
- Create: `.github/workflows/website.yml` (repository root). This is a new file, so it does not collide with the shared `ci.yml`.

- [ ] **Step 1: Write the workflow.**

```yaml
name: website
on:
  push:
    paths: ['website/**', '.github/workflows/website.yml']
  pull_request:
    paths: ['website/**', '.github/workflows/website.yml']
jobs:
  site:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: website } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '22.12.0', cache: npm, cache-dependency-path: website/package-lock.json }
      - run: npm ci
      - run: npx vitest run
      - run: npx astro check
      - run: npm run build
      - run: npx playwright install --with-deps chromium
      - run: npx playwright test tests/site/head.spec.ts tests/site/links.spec.ts tests/site/form.spec.ts
      - run: python3 tools/check_retention_sql.py
```

- [ ] **Step 2: Validate it locally.**
  - Run `npx -y yaml-lint@1.7.0 .github/workflows/website.yml`, or parse it with python `yaml.safe_load` if that is unavailable.
  - Run the same command list locally with Node 22.12 through `npx -y node@22.12.0`.

  Expected: every step passes.

- [ ] **Step 3: Commit, push the branch, and read the run.** Run `gh run list --workflow website.yml --branch feat/website --limit 1`. Expected: success. A red run is fixed in this task, never skipped.

---

### Task 11: Accessibility and performance audits

**Files:**
- Modify: `website/package.json` (devDependency `"@axe-core/playwright": "4.13.0"`)
- Create: `website/tests/site/a11y.spec.ts`

- [ ] **Step 1: Write `a11y.spec.ts`.** It runs on every built page, in both languages, at 390 and 1440:
  - `new AxeBuilder({ page }).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze()`;
  - expect `violations` to equal `[]`. On failure, print each violation's id and its target selectors.
  - Keyboard path on `/en/contact/` and `/ar/contact/`: Tab from the top reaches the skip link, the nav and every form field in DOM order. Focus is visible: the outline width is greater than 0 on each stop.
- [ ] **Step 2: Run it.** Every violation is fixed in the owning file (one builder per file pair), never suppressed. Re-run until it is clean.
- [ ] **Step 3: Run Lighthouse** on the lite tier with mobile throttling, for `/ar/`, `/ar/aura-retail/` and `/en/contact/`, against `npm run preview`:
  `npx -y lighthouse@13.5.0 http://localhost:4321/ar/ --preset=perf --form-factor=mobile --throttling-method=simulate --output=json --output-path=<jobtmp>/lh-ar.json --chrome-flags="--headless"`.
  Record LCP, CLS, TBT and the scores in the ledger. Targets (spec section 6): LCP under 2.5 s and CLS under 0.1. TBT stands in for INP: under 200 ms. Any miss goes to Fable for a delta, then builders, then a re-measure.
- [ ] **Step 4: Run `/seo-audit`** (skill) on the built site, and the schema validation of the JSON-LD output. Findings are fixed or ruled in the ledger.
- [ ] **Step 5: Commit** `a11y.spec.ts`, `package.json`, `package-lock.json` and every fix, by explicit paths:
```
test(website): axe WCAG 2.2 AA + keyboard path; perf measured
```

---

### Task 12: Photos (composited real screens), designed by Fable

**Files:**
- Spec delta (Fable, job tmp): `spec-delta-photos.md`
- Create: `website/tools/composite_screens.py` (spec section 6)
- Create (generated): `website/src/assets/photos/*.jpg`
- Modify: the page files the delta names (at most 2 per builder)

- [ ] **Step 1: Fable decides** whether photos appear, and if so where. It reviews the five mood photos in `C:\Users\MSI\.claude\jobs\b6c06c3b\tmp\agy-img\aura_*.jpg` against the design system and the honesty rules. The photos are illustrative: no claim that they show customers. For each placement it names the page, the section, the screen shot (from `src/assets/shots/`), the corner points of the blank screen in the photo, and the caption or alt text in both languages. "No photos" is an allowed decision; it is then recorded and Steps 2-4 are skipped.
- [ ] **Step 2: The builder writes `composite_screens.py`.** It uses a Pillow perspective transform from the four corner points, with exact numbers from the delta. The main session runs it.
- [ ] **Step 3: The builders place `PhotoFrame`** per the delta.
- [ ] **Step 4: Verify.** Run the build, then shots of the touched pages, then contrast; then look at the result. Commit.

---

### Task 13: Deploy runbook and launch checklist (prepared, not executed)

**Files:**
- Create: `website/docs/deploy.md`

- [ ] **Step 1: Write `deploy.md`.** It is a numbered runbook for one owner-present session, with each step marked [owner] or [team] and a verification after each step:
  1. [owner] `npx wrangler login` (browser).
  2. [team] `wrangler d1 create aura-leads`. Paste the id into `wrangler.toml` and `retention/wrangler.toml`, then run `wrangler d1 migrations apply aura-leads --remote`.
  3. [owner+team] Create the Pages project from the GitHub repo, with branch `feat/website`, root directory `website` (or `functions/` is not found), build command `npm run build`, output `dist`, and env `NODE_VERSION=22.12.0`.
  4. [owner] Create a Turnstile widget for `actionaura.me` and the pages.dev host. Set `PUBLIC_TURNSTILE_SITEKEY` (build env) and the `TURNSTILE_SECRET` secret.
  5. [owner] Set the secret `IP_SALT`: 32 or more random bytes, generated with `node -e "console.log(crypto.randomBytes(32).toString('hex'))"`.
  6. [owner] Notification: either `NOTIFY_PROVIDER=smtp` plus `SMTP_HOST=mail.privateemail.com`, `SMTP_PORT=465`, `SMTP_USER`, the `SMTP_PASS` secret and `NOTIFY_TO=info@actionaura.me`, or leave it `none`.
  7. [owner] Web Analytics: create a site and set `PUBLIC_CF_BEACON_TOKEN`.
  8. [team] `npx wrangler deploy -c retention/wrangler.toml`. Verify with `wrangler tail` on the next cron, or trigger it with `--test-scheduled` locally.
  9. [team] Smoke-test on the `*.pages.dev` URL:
     - run the `stack.spec.ts` checks against it;
     - `curl -I` shows `X-Robots-Tag: noindex`;
     - a real form post lands in D1;
     - a test mail arrives if smtp is on.
  10. [owner, GATED] Point DNS. Either move it to Cloudflare DNS, after copying every record and running `scripts/ops/check_mail_dns.py` before and after, or use the Namecheap ALIAS route. Then `curl -I https://actionaura.me/` must show no `X-Robots-Tag`.
  11. [team] Search Console and Bing: submit `sitemap-index.xml`.
  12. [owner] Google Business Profile.
  13. [owner] Mailbox copies of leads are deleted after 24 months, by hand or with a mailbox rule. This is the privacy promise for mailbox copies.

  It also lists the open owner inputs: the native Arabic reader, the lawyer review of Privacy/Terms, office hours, phone screenshots (plug the phone in) and a real POS photo.

- [ ] **Step 2: Fold the launch checklist** from the `launch` skill into `deploy.md` as a final "Before announcing" section.

- [ ] **Step 3: Commit** `deploy.md`:
```
docs(website): deploy runbook and launch checklist
```

---

### Task 14: Final verification, review and Figma

- [ ] **Step 1: Full screenshot run.** Run it detached, with the `run-shots2.cmd` pattern and a log in job tmp: all pages, AR and EN, at 320, 390, 768, 1024 and 1440 (spec section 9 lists 320, so add 320 to `CORE_WIDTHS` in `shots.spec.ts` in this step), rich and reduce. Then run `contrast.py`. Expected: 0 failing. Build contact sheets and look at them.
- [ ] **Step 2: Independent whole-branch review.** Run it on the most capable model, against Plan 3's diff (BASE `bf12d6af`). Then do ONE fix wave, one scoped re-review, and adjudicate what remains, following the SDD final-review rules.
- [ ] **Step 3: Figma.** Push the approved home, Retail, Clinic and contact pages (EN and AR, 1440 and 390) to the owner's Figma via `generate_figma_design`, using the `figma-generate-design` skill. The Starter plan rations calls, so batch them.
- [ ] **Step 4: Back up.** Push `feat/website` and report to the owner in plain words, with the contact sheets and the Rulings list.
