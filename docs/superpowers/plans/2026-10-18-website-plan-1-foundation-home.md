# Action Aura Website, Plan 1: Foundation + Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working Astro site in `website/` with Arabic and English Home pages, the language chooser, the 404 page, the 3D jewel with colour worlds, and an automated check harness. It must build and preview locally and be ready for a Cloudflare Pages preview.

**Architecture:** Astro 5 static output. Pages live under `src/pages/[lang]/` and get their strings from typed dictionaries, so TypeScript enforces Arabic/English key parity. Every piece of logic is a pure, unit-tested TypeScript function: language choice, WhatsApp links, SEO alternates, world mixing, jewel stations and tier choice. The DOM and three.js glue are thin modules on top. three.js r128 is self-hosted from npm and only loaded on the rich tier after `load`.

**Tech Stack:** Astro 5.18.2, @astrojs/sitemap 3.7.4, three 0.128.0, @fontsource/outfit 5.3.0, @fontsource/readex-pro 5.3.0, TypeScript 5.9.3, Vitest 3.2.7, @playwright/test 1.56.1. Python 3 (PIL) for the contrast tool.

**Spec:** `docs/superpowers/specs/2026-10-18-action-aura-website-design.md`. The visual rules live in `website/docs/design-system.md` (copied in Task 1). Executors read both.

**Plans in this series:**
1. Foundation + Home (this plan).
2. Product, About, FAQ, Contact pages, the demo form and its Pages Function.
3. Assets (lite stills, composited photos, OG images, clinic evidence), audits, Cloudflare deploy, Figma, launch.

## Execution model (owner's standing rule)

- **Fable thinks:** any design question that comes up mid-build goes to the design director and is answered as a spec delta.
- **`caveman:cavecrew-builder` executes:** each builder dispatch edits at most 2 files. Builders have **no shell**.
- **The main session:**
  - runs every shell step (npm, tests, builds, screenshots, git);
  - dispatches builders sequentially when they touch the same file;
  - verifies by looking at screenshots.
- Each task below marks which steps the main session runs, tagged **[main]**, and which go to a builder, tagged **[builder]** with its file list.
- All git and npm commands run in `C:\Users\MSI\Desktop\aura-fullsuits\.claude\worktrees\website`. Run git through PowerShell, because the RTK hook blocks Bash git in worktrees.

## Global Constraints

- Packages are pinned to exact versions: astro `5.18.2`, @astrojs/sitemap `3.7.4`, three `0.128.0`, @fontsource/outfit `5.3.0`, @fontsource/readex-pro `5.3.0`, typescript `5.9.3`, vitest `3.2.7`, @playwright/test `1.56.1`, @astrojs/check `0.9.10`.
- No UI framework and no CSS framework. The only third-party runtime code is three.js (self-hosted).
- Arabic is never letter-spaced, uppercased or faux-bolded. Use Western digits in both languages. Phone numbers sit in `<bdi dir="ltr">`.
- No em-dashes (U+2014) and no exclamation marks in any site copy.
- Available products are exactly Aura Retail and Aura Clinic. Everything else is "Coming / قريبًا".
- Never show prices, testimonials, customer counts, JoFotara/e-invoicing, iPhone, Android printing, a cash drawer or a 14-day grace period.
- WhatsApp is `https://wa.me/962780069485`. The disc is `#25D366` in every world. The phone is shown as `+962 78 006 9485`.
- Use logical CSS properties only (`inline-start/end`, `margin-inline`, `text-align: start`). The mark is never mirrored.
- Body text contrast is ≥ 4.5:1 against the rendered backdrop.
- `prefers-reduced-motion` gets stills and 300 ms fades only.
- URLs: `/ar/…` and `/en/…`, Latin slugs, trailing slash always, site origin `https://actionaura.me`.

## Review Focus

1. **A stored language value that is junk**, for example `"fr"`, `""` or `"AR"`, or `localStorage` throwing in private mode. The chooser must ignore it and fall back to the device language, then English. Pinned in Task 4.
2. **A device language list like `["en-US","ar-JO"]` or `["ar"]`.** The first supported entry wins, and `ar-*` maps to `ar`. Pinned in Task 4.
3. **A visitor loading the page already scrolled deep**, or resizing mid-glide. The jewel's station and the world mix are computed from the current scroll and never stay at the hero values. Pinned in Tasks 7 and 8 (pure functions take any scrollY).
4. **No WebGL2, a weak device, Save-Data or reduced motion.** The lite still is visible at the station and the page never shows an empty hole where the jewel should be. Pinned in Task 9 (tier tests) and Task 12 (screenshot in reduced motion).
5. **Text that would put an em-dash, an exclamation mark or a missing translation on screen.** The dictionary test fails the build. Pinned in Task 2.

---

### Task 1: Scaffold the Astro project in `website/`

**Files:**
- Delete: the September draft. That is `website/index.html`, `features.html`, `pricing.html`, `contact.html`, `README.md`, `css/`, `js/` and `assets/`. Git history keeps them.
- Create: `website/package.json`, `website/astro.config.mjs`, `website/tsconfig.json`, `website/.gitignore`, `website/vitest.config.ts`, `website/src/pages/en/index.astro` (temporary smoke page)
- Create: `website/docs/` (copied inputs)

**Interfaces:**
- Produces: `npm run build` outputs `website/dist/`. `npm test` runs Vitest over `tests/unit/**/*.test.ts`.

- [ ] **Step 1 [main]: Remove the draft and copy the inputs**

```powershell
cd C:\Users\MSI\Desktop\aura-fullsuits\.claude\worktrees\website
git rm -r -q website/index.html website/features.html website/pricing.html website/contact.html website/README.md website/css website/js website/assets
New-Item -ItemType Directory -Force website/docs/prototype | Out-Null
$J="C:\Users\MSI\.claude\jobs\b6c06c3b\tmp\website"
Copy-Item "$J\v2\design-system.md","$J\messaging.md","$J\seo-plan.md","$J\v2\research-and-concepts.md" website/docs/
Copy-Item "$J\v2\logo-worlds.html" website/docs/prototype/logo-worlds.html
```

- [ ] **Step 2 [builder: `website/package.json`, `website/astro.config.mjs`]: Write the config files**

`website/package.json`:
```json
{
  "name": "action-aura-website",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "astro dev",
    "build": "astro build",
    "preview": "astro preview --port 4321",
    "check": "astro check",
    "test": "vitest run",
    "test:site": "playwright test"
  },
  "dependencies": {
    "astro": "5.18.2",
    "@astrojs/sitemap": "3.7.4",
    "three": "0.128.0",
    "@fontsource/outfit": "5.3.0",
    "@fontsource/readex-pro": "5.3.0"
  },
  "devDependencies": {
    "@astrojs/check": "0.9.10",
    "typescript": "5.9.3",
    "vitest": "3.2.7",
    "@playwright/test": "1.56.1"
  }
}
```

`website/astro.config.mjs`:
```js
// Static site. Languages are plain route folders (/ar/, /en/) rather than
// Astro's i18n router: the root "/" is our own indexable language chooser,
// and the SEO plan forbids a server-side language redirect.
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://actionaura.me',
  trailingSlash: 'always',
  build: { format: 'directory' },
  integrations: [
    sitemap({
      i18n: { defaultLocale: 'en', locales: { en: 'en', ar: 'ar' } },
      filter: (page) => !page.includes('/404'),
    }),
  ],
});
```

- [ ] **Step 3 [builder: `website/tsconfig.json`, `website/vitest.config.ts`]: Write the TS and test configs**

`website/tsconfig.json`:
```json
{
  "extends": "astro/tsconfigs/strict",
  "include": ["src", "tests", ".astro/types.d.ts"],
  "exclude": ["dist"]
}
```

`website/vitest.config.ts`:
```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: { include: ['tests/unit/**/*.test.ts'], environment: 'node' },
});
```

- [ ] **Step 4 [builder: `website/.gitignore`, `website/src/pages/en/index.astro`]: Write the ignore file and the smoke page**

`website/.gitignore`:
```
node_modules/
dist/
.astro/
test-results/
playwright-report/
shots/
```

`website/src/pages/en/index.astro`:
```astro
---
---
<html lang="en"><head><meta charset="utf-8"><title>smoke</title></head><body><h1>smoke</h1></body></html>
```

- [ ] **Step 5 [main]: Install and build**

```powershell
cd website; npm install --no-fund --no-audit; npm run build
```
Expected: the build finishes with no errors and `dist/en/index.html` exists. If `npm install` reports a peer-dependency conflict, stop. Report the exact package pair and do not use `--force`.

- [ ] **Step 6 [main]: Commit**

```powershell
git add website/package.json website/package-lock.json website/astro.config.mjs website/tsconfig.json website/.gitignore website/vitest.config.ts website/src/pages/en/index.astro website/docs
git commit -m "feat(website): scaffold Astro 5 site, replace the September plain-HTML draft"
```

---

### Task 2: Typed dictionaries with an enforced parity test

**Files:**
- Create: `website/src/i18n/en.ts`, `website/src/i18n/ar.ts`, `website/src/i18n/index.ts`
- Test: `website/tests/unit/i18n.test.ts`

**Interfaces:**
- Produces:
  - `type Lang = 'ar' | 'en'`
  - `LANGS: readonly Lang[]`
  - `dict(lang: Lang): Dict` (the same nested shape for both languages)
  - `dir(lang: Lang): 'rtl' | 'ltr'`
  - `type Dict` (exported from `en.ts` as `DeepStrings<typeof en>`)

- [ ] **Step 1 [builder: `website/tests/unit/i18n.test.ts`]: Write the failing test**

```ts
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
      expect(v.includes('\u2014'), `${k} has an em-dash`).toBe(false);
      expect(/[!\u0021\uFF01]/.test(v), `${k} has "!"`).toBe(false);
    }
  });

  it('arabic uses western digits only', () => {
    for (const [k, v] of ar) expect(/[\u0660-\u0669\u06F0-\u06F9]/.test(v), `${k} has Arabic-Indic digits`).toBe(false);
  });

  it('never claims banned features or prices', () => {
    const banned = /(JoFotara|e-invoic|iPhone|\biOS\b|cash drawer|\bJOD\b|دينار|testimonial)/i;
    for (const [k, v] of [...en, ...ar]) expect(banned.test(v), `${k}: ${v}`).toBe(false);
  });

  it('dir is rtl for arabic only', () => {
    expect(dir('ar')).toBe('rtl');
    expect(dir('en')).toBe('ltr');
  });
});
```

Note: "Dinar with 3 decimals" belongs to the Retail page in Plan 2. The word "دينار" is banned here on purpose, so a price can never slip into the Home copy. Plan 2 narrows this rule when that line is added, with a comment saying why.

- [ ] **Step 2 [main]: Run it and watch it fail**

Run: `cd website; npx vitest run tests/unit/i18n.test.ts`
Expected: FAIL with "Cannot find module '../../src/i18n'".

- [ ] **Step 3 [builder: `website/src/i18n/en.ts`, `website/src/i18n/ar.ts`]: Write both dictionaries**

The shape is shown in `en.ts` below. `ar.ts` mirrors every key. The Arabic text comes from `website/docs/messaging.md` sections 2-3 and the design-system page copy. Where messaging.md has no Arabic line, write natural Levantine-friendly MSA with the same meaning and mark it in the file with a trailing comment `// AR: drafted, native check pending`.

`website/src/i18n/en.ts`:
```ts
// Every visible English string. ar.ts must have exactly these keys (the type
// below enforces it at `astro check`; tests/unit/i18n.test.ts enforces it at
// test time). Claims come only from docs/messaging.md (traced to content.py).
type DeepStrings<T> = { [K in keyof T]: T[K] extends string ? string : DeepStrings<T[K]> };

export const en = {
  meta: {
    home: {
      title: 'Action Aura | Retail Software from Amman, Jordan',
      description: 'Action Aura builds Aura Retail, POS and shop-management software that works offline and syncs your devices. Book a demo.',
    },
    notFound: { title: 'Page not found | Action Aura', description: 'This page is not here. Go back to Action Aura.' },
    chooser: { title: 'Action Aura | العربية or English', description: 'Choose your language: Arabic or English.' },
  },
  nav: {
    brand: 'Action Aura', retail: 'Aura Retail', clinic: 'Aura Clinic', gym: 'Aura Gym', about: 'About',
    demo: 'Book a demo', switchTo: 'العربية', menu: 'Menu', close: 'Close',
  },
  cta: {
    whatsapp: 'Chat on WhatsApp', demo: 'Book a free demo', call: 'Call us',
    price: 'Pricing depends on your shop. Ask us.',
    waText: 'Hello Action Aura, I would like to know more about Aura Retail.',
  },
  status: { available: 'Available now', coming: 'Coming' },
  home: {
    hero: {
      title: "Your shop keeps selling, even when the internet doesn't.",
      lead: 'Till, stock and books for shops and showrooms. It runs on the counter and on your phone, works offline, and syncs when you are back.',
    },
    company: {
      title: 'Action Aura', accent: 'Aura',
      lead: 'A Jordanian software company building systems that work the way businesses do.',
    },
    retail: {
      title: 'Aura Retail', accent: 'Retail',
      lead: 'The till that keeps selling offline. Barcode scanning, stock, receivables and payables, receipts in Arabic and English, and a phone that mirrors the counter.',
      link: 'See Aura Retail',
    },
    clinic: {
      title: 'Aura Clinic', accent: 'Clinic',
      lead: 'Appointments, patient records, treatment plans and billing, in the same calm system.',
      link: 'See Aura Clinic',
    },
    gym: {
      title: 'Aura Gym', accent: 'Gym',
      lead: 'Members, plans and check-ins, built the way gyms actually run.',
    },
    close: {
      title: 'Tell us about your shop.',
      lead: 'Our team in Amman installs, trains and supports.',
    },
  },
  footer: {
    tagline: 'Go Bigger · Go Smarter', products: 'Products', company: 'Company',
    privacy: 'Privacy', terms: 'Terms', faq: 'FAQ', city: 'Amman, Jordan', phone: '+962 78 006 9485',
  },
  notFound: { title: "This page isn't on the shelf.", back: 'Back to the start' },
  chooser: { heading: 'Choose your language', ar: 'العربية', en: 'English' },
  a11y: { skip: 'Skip to content', jewel: 'Action Aura mark' },
} as const;

export type Dict = DeepStrings<typeof en>;
```

`ar.a11y.jewel` is `'شعار أكشن أورا'`. The jewel canvas itself is `aria-hidden`; this string is announced once, next to it.

Each chapter in `home` (`company`, `retail`, `clinic`, `gym`) also carries an `accent` key: the one word of its title painted in the world accent colour. In `en` these are `'Aura'`, `'Retail'`, `'Clinic'` and `'Gym'`. In `ar` they are `'أورا'`, `'ريتيل'`, `'كلينك'` and `'جيم'`. Each must appear verbatim inside the same language's `title`. Add this test to `i18n.test.ts`:
```ts
it.each(LANGS)('%s chapter accents appear inside their titles', (lang) => {
  const h = dict(lang).home;
  for (const k of ['company', 'retail', 'clinic', 'gym'] as const) expect(h[k].title.includes(h[k].accent), `${lang} ${k}`).toBe(true);
});
```

`website/src/i18n/ar.ts` starts:
```ts
import type { Dict } from './en';

export const ar: Dict = {
  meta: {
    home: {
      title: 'أكشن أورا | برامج إدارة المحلات في الأردن',
      description: 'أكشن أورا شركة برمجيات في عمّان. اكتشف أورا ريتيل، برنامج كاشير وإدارة محلات يعمل بدون انترنت ويربط أجهزتك. احجز عرضاً تجريبياً.',
    },
    // …every key of en, in Arabic
  },
  // …
};
```
Fixed Arabic strings to use verbatim:
- `cta.whatsapp` 'راسلنا على واتساب'
- `cta.demo` 'احجز عرضًا مجانيًا'
- `cta.price` 'السعر حسب محلّك، اسألنا.'
- `status.available` 'متوفّر الآن'
- `status.coming` 'قريبًا'
- `home.hero.title` 'محلّك يبيع… حتى لو انقطع الإنترنت'
- `notFound.title` 'هذه الصفحة ليست على الرف.'
- `footer.phone` '+962 78 006 9485'
- `nav.switchTo` 'English'
- `footer.tagline` 'Go Bigger · Go Smarter' (the brand tagline stays Latin in both languages)

- [ ] **Step 4 [builder: `website/src/i18n/index.ts`]: Write the accessor**

```ts
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
```

- [ ] **Step 5 [main]: Run the tests and watch them pass**

Run: `npx vitest run tests/unit/i18n.test.ts`. Expected: PASS, 7 tests.

- [ ] **Step 6 [main]: Mutation-prove the guard**

Temporarily add `'x'` to the end of `en.home.close.title` and add `!`. The no-"!" test must fail. Revert. Then temporarily delete `ar.home.gym.lead`. The parity test must fail. Revert. Paste both red outputs into the task log.

- [ ] **Step 7 [main]: Commit**

`git add website/src/i18n website/tests/unit/i18n.test.ts; git commit -m "feat(website): typed ar/en dictionaries with parity, copy-rule and claims tests"`

---

### Task 3: Design tokens and base CSS

**Files:**
- Create: `website/src/styles/tokens.css`, `website/src/styles/base.css`

**Interfaces:**
- Produces:
  - CSS custom properties on `:root`, with world overrides on `[data-world="retail"|"clinic"|"gym"]`: `--field-a`, `--field-b`, `--field-c`, `--glow`, `--scrim`, `--accent`, `--ink`, `--ink-muted`, `--hair`, `--link`.
  - `--gutter`, `--ease-out`, `--ease-io`, `--ease-drawer`, `--wa`.
  - Classes `.wrap`, `.section`, `.copy`, `.lead`, `.ar-title`, `.hl`, `.visually-hidden`, `.reveal`.

- [ ] **Step 1 [builder: `website/src/styles/tokens.css`, `website/src/styles/base.css`]: Write both files**

`tokens.css` takes every value from `website/docs/design-system.md` section 2 and the phone overrides, in this order:
1. `:root` (master)
2. `[data-world="retail"]`
3. `[data-world="clinic"]` (light room, with `color-scheme: light`)
4. `[data-world="gym"]`
5. `@media (max-width: 899px)` overrides for `--ink-muted`

Then the motion tokens, `--wa: #25D366` and `--gutter: clamp(16px, 4.5vw, 64px)`. Use the table's exact hex and rgba strings. Fields are `--field-a/b/c` (the three gradient stops), `--glow` is the rgba string, and `--scrim` is the "r,g,b" triple.

`base.css`:
- the type scale from design-system section 3 as rules on `h1`, `h2`, `h3`, `.lead` and `body`, with `:lang(ar)` overrides for family, size, line-height and `letter-spacing: 0 !important; text-transform: none !important`;
- the section rhythm from section 4;
- `.reveal > *` fade-up per section 6 (Reveal), plus the reduced-motion version;
- `html { background: linear-gradient(180deg, var(--field-a), var(--field-b) 50%, var(--field-c)); color: var(--ink) }`;
- `overflow-x: clip` on `html` and `body`;
- `:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px }`;
- `.visually-hidden` (the standard clip pattern);
- logical properties only. No `left` or `right` keywords anywhere except inside `@media (prefers-reduced-motion)` (where none should be needed).

- [ ] **Step 2 [main]: Lint for banned properties**

Run: `Select-String -Path website/src/styles/*.css -Pattern '\b(margin|padding)-(left|right)\b|text-align:\s*(left|right)|letter-spacing' | Select-Object Line`
Expected: only `letter-spacing` lines. Each must be inside a Latin-only selector or be the `:lang(ar)` reset to 0. Any `-left` or `-right` hit gets sent back to the builder.

- [ ] **Step 3 [main]: Commit**

`git add website/src/styles; git commit -m "feat(website): design tokens per colour world and base type/rhythm CSS"`

---

### Task 4: Language choice, links and SEO helpers (pure functions)

**Files:**
- Create: `website/src/lib/lang.ts`, `website/src/lib/seo.ts`
- Test: `website/tests/unit/lang.test.ts`, `website/tests/unit/seo.test.ts`

**Interfaces:**
- Consumes: `Lang`, `isLang` from `src/i18n/index.ts`
- Produces:
  - `pickLang(stored: string | null, languages: readonly string[]): Lang`
  - `waLink(text: string): string`
  - `PHONE_TEL = 'tel:+962780069485'`
  - `pagePath(lang: Lang, slug: string): string` (slug `''` gives the home page, `'aura-retail'` gives a product page)
  - `alternates(slug: string): { ar: string; en: string; xDefault: string }` (absolute URLs)
  - `canonical(lang: Lang, slug: string): string`
  - `ORIGIN = 'https://actionaura.me'`

- [ ] **Step 1 [builder: `website/tests/unit/lang.test.ts`, `website/tests/unit/seo.test.ts`]: Write the failing tests**

`lang.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { pickLang, waLink, PHONE_TEL } from '../../src/lib/lang';

describe('pickLang', () => {
  it('honours a valid stored choice', () => expect(pickLang('ar', ['en-US'])).toBe('ar'));
  it('ignores junk stored values', () => {
    for (const junk of ['fr', '', 'AR', 'null', 'en-US']) expect(pickLang(junk, ['ar-JO'])).toBe('ar');
  });
  it('maps ar-* device languages to ar', () => expect(pickLang(null, ['ar-JO'])).toBe('ar'));
  it('first supported device language wins', () => {
    expect(pickLang(null, ['en-US', 'ar-JO'])).toBe('en');
    expect(pickLang(null, ['fr-FR', 'ar'])).toBe('ar');
  });
  it('falls back to en', () => {
    expect(pickLang(null, [])).toBe('en');
    expect(pickLang(null, ['fr-FR', 'de'])).toBe('en');
  });
});

describe('waLink', () => {
  it('builds an encoded wa.me link', () => {
    expect(waLink('مرحبا & hi')).toBe('https://wa.me/962780069485?text=' + encodeURIComponent('مرحبا & hi'));
  });
  it('omits text when empty', () => expect(waLink('')).toBe('https://wa.me/962780069485'));
  it('exposes the tel link', () => expect(PHONE_TEL).toBe('tel:+962780069485'));
});
```

`seo.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { pagePath, alternates, canonical, ORIGIN } from '../../src/lib/seo';

describe('seo helpers', () => {
  it('builds trailing-slash paths', () => {
    expect(pagePath('en', '')).toBe('/en/');
    expect(pagePath('ar', 'aura-retail')).toBe('/ar/aura-retail/');
  });
  it('alternates are absolute, reciprocal, and x-default is english', () => {
    expect(alternates('aura-clinic')).toEqual({
      ar: `${ORIGIN}/ar/aura-clinic/`, en: `${ORIGIN}/en/aura-clinic/`, xDefault: `${ORIGIN}/en/aura-clinic/`,
    });
  });
  it('canonical is self', () => expect(canonical('ar', '')).toBe(`${ORIGIN}/ar/`));
  it('rejects slugs with slashes or uppercase', () => {
    expect(() => pagePath('en', '/x')).toThrow();
    expect(() => pagePath('en', 'Aura')).toThrow();
  });
});
```

- [ ] **Step 2 [main]: Run them and watch them fail**

`npx vitest run tests/unit/lang.test.ts tests/unit/seo.test.ts`. Expected: FAIL, modules not found.

- [ ] **Step 3 [builder: `website/src/lib/lang.ts`, `website/src/lib/seo.ts`]: Implement**

`lang.ts`:
```ts
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
```

`seo.ts`:
```ts
import type { Lang } from '../i18n';

export const ORIGIN = 'https://actionaura.me';
const SLUG = /^[a-z0-9-]*$/;

export function pagePath(lang: Lang, slug: string): string {
  if (!SLUG.test(slug)) throw new Error(`bad slug: ${slug}`);
  return slug ? `/${lang}/${slug}/` : `/${lang}/`;
}

export function canonical(lang: Lang, slug: string): string {
  return ORIGIN + pagePath(lang, slug);
}

export function alternates(slug: string): { ar: string; en: string; xDefault: string } {
  return { ar: canonical('ar', slug), en: canonical('en', slug), xDefault: canonical('en', slug) };
}
```

- [ ] **Step 4 [main]: Run them and watch them pass**

Same command. Expected: PASS.

- [ ] **Step 5 [main]: Mutation-prove**

In `pickLang`, change `isLang(stored)` to `stored`. The "ignores junk" test must fail. Revert. Then delete `.toLowerCase()`. Add a case `pickLang(null, ['AR-jo'])` to the test first and expect `'ar'`; it must fail without the lowercasing. Keep that case. Revert the code.

- [ ] **Step 6 [main]: Commit**

`git add website/src/lib website/tests/unit; git commit -m "feat(website): language choice, WhatsApp link and SEO URL helpers with tests"`

---

### Task 5: Base layout with the full head (SEO, hreflang, fonts, JSON-LD)

**Files:**
- Create: `website/src/layouts/Base.astro`, `website/src/lib/jsonld.ts`
- Test: `website/tests/unit/jsonld.test.ts`

**Interfaces:**
- Consumes: `dict`, `dir`, `Lang` (Task 2); `canonical`, `alternates`, `ORIGIN` (Task 4)
- Produces:
  - `<Base lang slug title description world? noindex? jsonld?>`, which renders `<html lang dir data-world>`, the head, the skip link and a `<slot/>`
  - `organizationLd(lang: Lang): object`
  - `breadcrumbLd(lang: Lang, items: {name: string; slug: string}[]): object`

- [ ] **Step 1 [builder: `website/tests/unit/jsonld.test.ts`]: Write the failing test**

```ts
import { describe, it, expect } from 'vitest';
import { organizationLd, breadcrumbLd } from '../../src/lib/jsonld';

describe('json-ld', () => {
  it('organization has the real contact data and no ratings or offers', () => {
    const o = organizationLd('ar') as Record<string, unknown>;
    expect(o['@type']).toBe('Organization');
    expect(o.name).toBe('Action Aura');
    expect(o.alternateName).toBe('أكشن أورا');
    expect(o.telephone).toBe('+962780069485');
    expect(JSON.stringify(o)).not.toMatch(/aggregateRating|offers|review/);
  });
  it('breadcrumb positions are 1-based and urls absolute', () => {
    const b = breadcrumbLd('en', [{ name: 'Home', slug: '' }, { name: 'Aura Retail', slug: 'aura-retail' }]) as any;
    expect(b.itemListElement.map((i: any) => i.position)).toEqual([1, 2]);
    expect(b.itemListElement[1].item).toBe('https://actionaura.me/en/aura-retail/');
  });
});
```

- [ ] **Step 2 [main]: Run it and watch it fail.** `npx vitest run tests/unit/jsonld.test.ts`. Expected: FAIL.

- [ ] **Step 3 [builder: `website/src/lib/jsonld.ts`, `website/src/layouts/Base.astro`]: Implement**

`jsonld.ts`:
```ts
import type { Lang } from '../i18n';
import { ORIGIN, canonical } from './seo';

export function organizationLd(lang: Lang): object {
  return {
    '@context': 'https://schema.org', '@type': 'Organization',
    name: 'Action Aura', alternateName: 'أكشن أورا',
    url: `${ORIGIN}/${lang}/`, logo: `${ORIGIN}/icons/mark-512.png`,
    telephone: '+962780069485', email: 'info@actionaura.me',
    address: { '@type': 'PostalAddress', addressLocality: 'Amman', addressCountry: 'JO',
      streetAddress: 'Al-Rayyan Complex, Radio & Television St, Al-Muqabalain' },
  };
}

export function breadcrumbLd(lang: Lang, items: { name: string; slug: string }[]): object {
  return {
    '@context': 'https://schema.org', '@type': 'BreadcrumbList',
    itemListElement: items.map((it, i) => ({ '@type': 'ListItem', position: i + 1, name: it.name, item: canonical(lang, it.slug) })),
  };
}
```

`Base.astro`:
```astro
---
import '@fontsource/outfit/400.css';
import '@fontsource/outfit/500.css';
import '@fontsource/outfit/600.css';
import '@fontsource/outfit/700.css';
import '@fontsource/readex-pro/400.css';
import '@fontsource/readex-pro/500.css';
import '@fontsource/readex-pro/600.css';
import '../styles/tokens.css';
import '../styles/base.css';
import { dict, dir, type Lang } from '../i18n';
import { canonical, alternates } from '../lib/seo';

interface Props {
  lang: Lang; slug: string; title: string; description: string;
  world?: 'master' | 'retail' | 'clinic' | 'gym'; noindex?: boolean; jsonld?: object[];
}
const { lang, slug, title, description, world = 'master', noindex = false, jsonld = [] } = Astro.props;
const t = dict(lang);
const alt = alternates(slug);
const self = canonical(lang, slug);
const ogImage = `https://actionaura.me/og/${lang}-${slug || 'home'}.png`;
---
<html lang={lang} dir={dir(lang)} data-world={world}>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <title>{title}</title>
    <meta name="description" content={description} />
    {noindex ? <meta name="robots" content="noindex" /> : <link rel="canonical" href={self} />}
    {!noindex && <link rel="alternate" hreflang="ar" href={alt.ar} />}
    {!noindex && <link rel="alternate" hreflang="en" href={alt.en} />}
    {!noindex && <link rel="alternate" hreflang="x-default" href={alt.xDefault} />}
    <meta property="og:type" content="website" />
    <meta property="og:title" content={title} />
    <meta property="og:description" content={description} />
    <meta property="og:url" content={self} />
    <meta property="og:image" content={ogImage} />
    <meta property="og:locale" content={lang === 'ar' ? 'ar_JO' : 'en_US'} />
    <meta name="twitter:card" content="summary_large_image" />
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <link rel="icon" href="/favicon-32.png" sizes="32x32" type="image/png" />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
    <link rel="manifest" href="/site.webmanifest" />
    <meta name="theme-color" content="#0E1729" />
    {jsonld.map((o) => <script type="application/ld+json" set:html={JSON.stringify(o)} />)}
  </head>
  <body>
    <a class="visually-hidden" href="#main">{t.a11y.skip}</a>
    <slot />
  </body>
</html>
```
(The OG images, favicon files and manifest are produced in Plan 3. Until then they 404, and the Plan 3 link check is the gate for that.)

- [ ] **Step 4 [main]: Run the tests, then build**

`npx vitest run; npm run build`. Expected: PASS, and the build succeeds.

- [ ] **Step 5 [main]: Commit**

`git add website/src/layouts website/src/lib/jsonld.ts website/tests/unit/jsonld.test.ts; git commit -m "feat(website): base layout with canonical, hreflang, OG and JSON-LD"`

---

### Task 6: Shared UI components (Nav, Footer, CTAs, StatusPill)

**Files** (4 builder dispatches of 2 files each, sequential):
- Create: `website/src/components/WhatsAppPill.astro`, `website/src/components/DemoLink.astro`
- Create: `website/src/components/StatusPill.astro`, `website/src/components/Chapter.astro`
- Create: `website/src/components/Nav.astro`, `website/src/scripts/nav.ts`
- Create: `website/src/components/Footer.astro`, `website/src/styles/components.css`

**Interfaces:**
- Consumes: `dict`, `Lang` (Task 2); `waLink`, `PHONE_TEL` (Task 4); `pagePath` (Task 4)
- Produces:
  - `<WhatsAppPill lang text? label?>`
  - `<DemoLink lang>` (href `pagePath(lang,'contact')`)
  - `<StatusPill lang kind="available"|"coming">`
  - `<Chapter id lang world status? flip? title accentWord lead link?>` (renders `<section id data-w={world} class="chapter reveal">`)
  - `<Nav lang slug>` (sets `aria-current` on the current link; the menu sheet is toggled by `nav.ts`)
  - `<Footer lang>`
  - `components.css` (every component style, imported by `Base.astro`; the main session adds that one import line)

- [ ] **Step 1 [builder: `WhatsAppPill.astro`, `DemoLink.astro`]: Build the CTAs**

Both follow design-system section 7 exactly: padding, the 36px `#25D366` disc on the inline-start side with the white WhatsApp glyph at 20px, the label from `t.cta.whatsapp`, and `href={waLink(text ?? t.cta.waText)}`. Links get `rel="noopener"` and `target="_blank"`.

The glyph SVG path to use, verbatim (24×24 viewBox, `fill="#fff"`):
```
M12 2.2a9.7 9.7 0 0 0-8.4 14.6L2.3 21.7l5-1.3A9.7 9.7 0 1 0 12 2.2Zm0 17.7c-1.5 0-3-.4-4.2-1.2l-.3-.2-3 .8.8-2.9-.2-.3A8 8 0 1 1 12 19.9Zm4.4-6c-.2-.1-1.4-.7-1.7-.8-.2-.1-.4-.1-.5.1l-.8 1c-.1.2-.3.2-.5.1a6.6 6.6 0 0 1-3.3-2.9c-.2-.4.2-.4.7-1.3.1-.2 0-.3 0-.4l-.8-1.8c-.2-.5-.4-.4-.5-.4h-.5a.9.9 0 0 0-.7.3 2.8 2.8 0 0 0-.9 2.1 4.9 4.9 0 0 0 1 2.6 11.2 11.2 0 0 0 4.3 3.8c1.6.7 2.2.7 3 .6a2.5 2.5 0 0 0 1.7-1.2 2 2 0 0 0 .1-1.2c0-.1-.2-.2-.5-.3Z
```
`DemoLink` has an arrow SVG that flips in RTL (`transform: scaleX(-1)` under `:dir(rtl)`). On phones it becomes the translucent pill.

- [ ] **Step 2 [builder: `StatusPill.astro`, `Chapter.astro`]: Build the pill and the chapter block**

These follow design-system section 7, "Status pills" and "Chapter block". The pill shows both labels: `<span>{en label}</span> <span lang="ar">{ar label}</span>`, built from `dict('en').status` and `dict('ar').status`, with the filled or hollow dot. `Chapter` lays out its copy column on the inline-start side, or on the inline-end side when `flip`, using `grid-template-columns: minmax(0,46%) 1fr` and `grid-column: 2` for flip. It wraps the copy in `<div class="copy reveal">`, renders `h2` with `<span class="hl">{accentWord}</span>`, then the lead, then the optional link.

- [ ] **Step 3 [builder: `Nav.astro`, `nav.ts`]: Build the nav**

The nav follows design-system section 7, "Nav":
- Links: Aura Retail `pagePath(lang,'aura-retail')`, Aura Clinic `pagePath(lang,'aura-clinic')`, Aura Gym `pagePath(lang,'') + '#gym'` with a Coming pill, About `pagePath(lang,'about')`, the language switch (href = the same slug in the other language), and the Book a demo outline pill.
- `nav.ts` sets `data-chapter` on `<html>` from the section nearest the viewport centre, using an IntersectionObserver with `rootMargin: '-45% 0px -45% 0px'`.
- It toggles the phone sheet (button `aria-expanded`, `aria-controls`), closes on Escape, and traps no focus. The sheet is a full-screen `<dialog>`-free div with `hidden` toggled.
- Under reduced motion the sheet opens without the mask reveal.

- [ ] **Step 4 [builder: `Footer.astro`, `components.css`]: Build the footer and move styles**

The footer follows design-system section 7, "Footer". The Products column lists Aura Retail and Aura Clinic as links and Aura Gym with a Coming pill and no link. The Company column links About, FAQ, Book a demo, Privacy and Terms. The phone is wrapped as `<a href={PHONE_TEL}><bdi dir="ltr">{t.footer.phone}</bdi></a>`. `components.css` holds all styles for the six components. The components carry no `<style>` blocks; they use class names only, so the main session can audit one file.

- [ ] **Step 5 [main]: Wire the CSS import, build and lint**

Add `import '../styles/components.css';` after `base.css` in `Base.astro`. Run `npm run build` (expected: success). Repeat the banned-property lint from Task 3 Step 2 on `components.css`.

- [ ] **Step 6 [main]: Commit**

`git add website/src/components website/src/scripts/nav.ts website/src/styles/components.css website/src/layouts/Base.astro; git commit -m "feat(website): nav, footer, WhatsApp/demo CTAs, status pill and chapter block"`

---

### Task 7: Colour-world mixing (pure + DOM glue)

**Files:**
- Create: `website/src/scripts/worlds/mix.ts`, `website/src/scripts/worlds/apply.ts`
- Test: `website/tests/unit/worlds.test.ts`

**Interfaces:**
- Produces:
  - `type World = 'master' | 'retail' | 'clinic' | 'gym'`
  - `worldMix(sections: { top: number; height: number; world: World }[], scrollY: number, viewportH: number): Record<World, number>`. The weights are ≥ 0 and sum to 1.
  - `dominant(mix): World`
  - `startWorlds(root: HTMLElement): () => void`, which sets `data-world` to the dominant world, writes the per-layer opacity CSS vars `--w-master` … `--w-gym`, and returns a cleanup function.

The rule (from the prototype's fixed `applyDom`):
- The viewport centre `c = scrollY + viewportH/2` is mapped to section centres.
- Between two consecutive section centres, the weight blends with smoothstep from the earlier section's world to the later one's.
- Before the first centre, everything goes to the first world. After the last centre, everything goes to the last world.
- Sections with the same world add their weights together.

- [ ] **Step 1 [builder: `website/tests/unit/worlds.test.ts`]: Write the failing test**

```ts
import { describe, it, expect } from 'vitest';
import { worldMix, dominant } from '../../src/scripts/worlds/mix';

const S = [
  { top: 0, height: 900, world: 'master' as const },
  { top: 900, height: 900, world: 'retail' as const },
  { top: 1800, height: 900, world: 'clinic' as const },
];
const sum = (m: Record<string, number>) => Object.values(m).reduce((a, b) => a + b, 0);

describe('worldMix', () => {
  it('is fully the first world at the top', () => expect(worldMix(S, 0, 900).master).toBeCloseTo(1));
  it('is fully the section world when centred on it', () => expect(worldMix(S, 900, 900).retail).toBeCloseTo(1));
  it('is half and half midway between two centres', () => {
    const m = worldMix(S, 450, 900);
    expect(m.master).toBeCloseTo(0.5); expect(m.retail).toBeCloseTo(0.5);
  });
  it('always sums to 1 and is never negative, at any scroll including deep loads', () => {
    for (let y = -500; y <= 5000; y += 37) {
      const m = worldMix(S, y, 900);
      expect(sum(m)).toBeCloseTo(1);
      for (const v of Object.values(m)) expect(v).toBeGreaterThanOrEqual(0);
    }
  });
  it('past the last centre stays on the last world', () => expect(worldMix(S, 4000, 900).clinic).toBeCloseTo(1));
  it('dominant picks the largest weight', () => expect(dominant(worldMix(S, 1700, 900))).toBe('clinic'));
  it('handles no sections as master', () => expect(worldMix([], 0, 900).master).toBe(1));
});
```

- [ ] **Step 2 [main]: Run it and watch it fail.** Expected: module not found.

- [ ] **Step 3 [builder: `mix.ts`, `apply.ts`]: Implement**

`mix.ts`:
```ts
export type World = 'master' | 'retail' | 'clinic' | 'gym';
export const WORLDS: readonly World[] = ['master', 'retail', 'clinic', 'gym'];

const smooth = (t: number) => t * t * (3 - 2 * t);
const zero = (): Record<World, number> => ({ master: 0, retail: 0, clinic: 0, gym: 0 });

export function worldMix(
  sections: { top: number; height: number; world: World }[], scrollY: number, viewportH: number,
): Record<World, number> {
  const out = zero();
  if (sections.length === 0) { out.master = 1; return out; }
  const c = scrollY + viewportH / 2;
  const centres = sections.map((s) => s.top + s.height / 2);
  if (c <= centres[0]) { out[sections[0].world] = 1; return out; }
  const last = sections.length - 1;
  if (c >= centres[last]) { out[sections[last].world] = 1; return out; }
  for (let i = 0; i < last; i++) {
    if (c >= centres[i] && c <= centres[i + 1]) {
      const t = smooth((c - centres[i]) / (centres[i + 1] - centres[i]));
      out[sections[i].world] += 1 - t;
      out[sections[i + 1].world] += t;
      return out;
    }
  }
  out.master = 1;
  return out;
}

export function dominant(mix: Record<World, number>): World {
  return WORLDS.reduce((a, b) => (mix[b] > mix[a] ? b : a), 'master');
}
```

`apply.ts`:
```ts
import { worldMix, dominant, WORLDS, type World } from './mix';

// Each world layer takes ITS OWN share as opacity. (The prototype's first
// version applied a stacked-crossfade formula to semi-transparent scrims,
// which left the previous world's vignette at full strength: the grey band
// over Clinic. Never stack-compose these layers.)
export function startWorlds(root: HTMLElement): () => void {
  const sections = () => Array.from(document.querySelectorAll<HTMLElement>('[data-w]')).map((el) => ({
    top: el.offsetTop, height: el.offsetHeight, world: (el.dataset.w as World) ?? 'master',
  }));
  let cache = sections();
  let frame = 0;
  const update = () => {
    frame = 0;
    const mix = worldMix(cache, window.scrollY, window.innerHeight);
    for (const w of WORLDS) root.style.setProperty(`--w-${w}`, mix[w].toFixed(3));
    root.dataset.world = dominant(mix);
    root.dispatchEvent(new CustomEvent('worldmix', { detail: mix }));
  };
  const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
  const onResize = () => { cache = sections(); onScroll(); };
  addEventListener('scroll', onScroll, { passive: true });
  addEventListener('resize', onResize);
  update();
  return () => { removeEventListener('scroll', onScroll); removeEventListener('resize', onResize); cancelAnimationFrame(frame); };
}
```

- [ ] **Step 4 [main]: Run it and watch it pass, then mutation-prove**

Run: `npx vitest run tests/unit/worlds.test.ts` → PASS.

Then mutate: replace `smooth(...)` with `1` (a hard switch). The "half and half" test must fail. Revert.

- [ ] **Step 5 [main]: Commit**

`git add website/src/scripts/worlds website/tests/unit/worlds.test.ts; git commit -m "feat(website): scroll-linked colour-world mix with per-layer opacity"`

---

### Task 8: Jewel stations (pure)

**Files:**
- Create: `website/src/scripts/jewel/stations.ts`
- Test: `website/tests/unit/stations.test.ts`

**Interfaces:**
- Produces:
  - `type Station = { x: number; y: number; scale: number }` (x and y are viewport fractions)
  - `HOME_DESKTOP: Station[]` (6 entries, from design-system section 5: hero 0.73/0.54/1.0; company 0.66/0.52/1.14; retail 0.27/0.54/0.9; clinic 0.73/0.54/0.9; gym 0.27/0.54/0.9; close 0.73/0.58/0.76)
  - `HOME_PHONE: Station[]` (6 entries: x 0.5, y 0.5, scale 1, with the close at 0.9)
  - `mirror(st: Station): Station` (x → 1 − x)
  - `stationAt(stations: Station[], centres: number[], c: number, rtl: boolean): Station` (smoothstep-interpolated, clamped at both ends)

- [ ] **Step 1 [builder: `website/tests/unit/stations.test.ts`]: Write the failing test**

```ts
import { describe, it, expect } from 'vitest';
import { HOME_DESKTOP, HOME_PHONE, mirror, stationAt } from '../../src/scripts/jewel/stations';

const C = [450, 1350, 2250, 3150, 4050, 4950];

describe('stations', () => {
  it('home has six desktop and six phone stations', () => {
    expect(HOME_DESKTOP).toHaveLength(6); expect(HOME_PHONE).toHaveLength(6);
  });
  it('sits exactly on a station at its centre', () => expect(stationAt(HOME_DESKTOP, C, 2250, false)).toEqual(HOME_DESKTOP[2]));
  it('clamps before the first and after the last centre', () => {
    expect(stationAt(HOME_DESKTOP, C, -999, false)).toEqual(HOME_DESKTOP[0]);
    expect(stationAt(HOME_DESKTOP, C, 99999, false)).toEqual(HOME_DESKTOP[5]);
  });
  it('rtl mirrors x and never y or scale', () => {
    const s = stationAt(HOME_DESKTOP, C, 450, true);
    expect(s.x).toBeCloseTo(1 - HOME_DESKTOP[0].x); expect(s.y).toBe(HOME_DESKTOP[0].y); expect(s.scale).toBe(HOME_DESKTOP[0].scale);
  });
  it('midway is between both stations', () => {
    const s = stationAt(HOME_DESKTOP, C, 1800, false);
    expect(s.x).toBeLessThan(HOME_DESKTOP[1].x); expect(s.x).toBeGreaterThan(HOME_DESKTOP[2].x);
  });
  it('mirror is an involution', () => expect(mirror(mirror(HOME_DESKTOP[2]))).toEqual(HOME_DESKTOP[2]));
  it('desktop stations keep the 25% clear space horizontally', () => {
    for (const s of HOME_DESKTOP) { expect(s.x).toBeGreaterThanOrEqual(0.25); expect(s.x).toBeLessThanOrEqual(0.75); }
  });
});
```

- [ ] **Step 2 [main]: Run it and watch it fail.**

- [ ] **Step 3 [builder: `website/src/scripts/jewel/stations.ts`]: Implement**

```ts
export type Station = { x: number; y: number; scale: number };

// Values from docs/design-system.md section 5 (Home row). x/y are viewport
// fractions of the mark's centre; scale 1 = 58% of viewport height.
export const HOME_DESKTOP: Station[] = [
  { x: 0.73, y: 0.54, scale: 1.0 },  // hero
  { x: 0.66, y: 0.52, scale: 1.14 }, // company
  { x: 0.27, y: 0.54, scale: 0.9 },  // retail (copy flipped right)
  { x: 0.73, y: 0.54, scale: 0.9 },  // clinic
  { x: 0.27, y: 0.54, scale: 0.9 },  // gym (copy flipped right)
  { x: 0.73, y: 0.58, scale: 0.76 }, // close
];
export const HOME_PHONE: Station[] = [
  { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 1 },
  { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 0.9 },
];

export const mirror = (s: Station): Station => ({ x: 1 - s.x, y: s.y, scale: s.scale });
const smooth = (t: number) => t * t * (3 - 2 * t);

export function stationAt(stations: Station[], centres: number[], c: number, rtl: boolean): Station {
  const pick = (s: Station) => (rtl ? mirror(s) : s);
  if (c <= centres[0]) return pick(stations[0]);
  const last = Math.min(stations.length, centres.length) - 1;
  if (c >= centres[last]) return pick(stations[last]);
  for (let i = 0; i < last; i++) {
    if (c >= centres[i] && c <= centres[i + 1]) {
      const t = smooth((c - centres[i]) / (centres[i + 1] - centres[i]));
      const a = stations[i], b = stations[i + 1];
      return pick({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, scale: a.scale + (b.scale - a.scale) * t });
    }
  }
  return pick(stations[last]);
}
```

Note for the reviewer: `stationAt(…, 2250, false)` returns `HOME_DESKTOP[2]` exactly because `c === centres[2]` hits `t = 0` of the interval starting at index 2. The test's `toEqual` on an interpolated object relies on `a.x + 0` being exactly `a.x`, and it is.

- [ ] **Step 4 [main]: Run it and watch it pass; mutation-prove** (remove the `rtl` mirror, and the RTL test fails). Revert.

- [ ] **Step 5 [main]: Commit.** `git commit -m "feat(website): jewel station table and smoothstep station interpolation"`

---

### Task 9: Jewel tier choice and the three.js scene

**Files:**
- Create: `website/src/scripts/jewel/tier.ts`, test `website/tests/unit/tier.test.ts`
- Create: `website/src/scripts/jewel/scene.js` (ported from the prototype), `website/src/components/Jewel.astro`

**Interfaces:**
- Consumes: `Station`, `stationAt`, `HOME_*` (Task 8); the `worldmix` event and `World` (Task 7)
- Produces:
  - `pickTier(env: { reducedMotion: boolean; webgl2: boolean; deviceMemory?: number; saveData?: boolean; width: number; dpr: number }): 'rich' | 'lite' | 'still'`
  - `mountJewel(canvas: HTMLCanvasElement, opts: { phone: boolean }): { setMix(mix: Record<World,number>): void; setStation(s: Station): void; dispose(): void }` (from `scene.js`)
  - `<Jewel lang stations="home">`, which renders a fixed layer with the world stills (`<img>`, one per world, from `/jewel/still-<world>.webp`) and a `<canvas aria-hidden="true">`

Tier rule (design-system section 5, lite path):
- `reducedMotion` gives `'still'`.
- Otherwise, any of these gives `'lite'`: no WebGL2; `deviceMemory <= 4`; `saveData`; `width < 900 && dpr > 2.5`.
- Otherwise `'rich'`.

- [ ] **Step 1 [builder: `website/tests/unit/tier.test.ts`]: Write the failing test**

```ts
import { describe, it, expect } from 'vitest';
import { pickTier } from '../../src/scripts/jewel/tier';

const base = { reducedMotion: false, webgl2: true, deviceMemory: 8, saveData: false, width: 1440, dpr: 1 };

describe('pickTier', () => {
  it('rich on a capable desktop', () => expect(pickTier(base)).toBe('rich'));
  it('still under reduced motion even when capable', () => expect(pickTier({ ...base, reducedMotion: true })).toBe('still'));
  it.each([
    ['no webgl2', { webgl2: false }],
    ['4GB memory', { deviceMemory: 4 }],
    ['save-data', { saveData: true }],
    ['dense small screen', { width: 390, dpr: 3 }],
  ])('lite when %s', (_n, patch) => expect(pickTier({ ...base, ...patch })).toBe('lite'));
  it('unknown deviceMemory does not force lite', () => expect(pickTier({ ...base, deviceMemory: undefined })).toBe('rich'));
  it('a 390px phone at dpr 2 can be rich', () => expect(pickTier({ ...base, width: 390, dpr: 2 })).toBe('rich'));
});
```

- [ ] **Step 2 [main]: Run it and watch it fail.**

- [ ] **Step 3 [builder: `website/src/scripts/jewel/tier.ts`]: Implement**

```ts
export type Tier = 'rich' | 'lite' | 'still';
export function pickTier(env: {
  reducedMotion: boolean; webgl2: boolean; deviceMemory?: number; saveData?: boolean; width: number; dpr: number;
}): Tier {
  if (env.reducedMotion) return 'still';
  if (!env.webgl2) return 'lite';
  if (env.deviceMemory !== undefined && env.deviceMemory <= 4) return 'lite';
  if (env.saveData) return 'lite';
  if (env.width < 900 && env.dpr > 2.5) return 'lite';
  return 'rich';
}
```

- [ ] **Step 4 [main]: Run it and watch it pass; mutation-prove** (change `<= 4` to `< 4`, and the "4GB" case fails). Revert. Commit: `git commit -m "feat(website): jewel tier choice (rich/lite/still) with tests"`

- [ ] **Step 5 [builder: `website/src/scripts/jewel/scene.js`]: Port the scene from the prototype**

Source: `website/docs/prototype/logo-worlds.html`, everything inside `<script>` that builds the three.js scene. That covers: the renderer, PMREM environment from the procedural dome and cards, the geometry built from the kit numbers, materials, ring, node and halos, shadow, the palettes table, the light-room handling, `applyWorld`-equivalent colour lerp, the frame loop with the dt clamp `.1`, arrival, idle and Facet Catch.

Rewrite it as an ES module:
```js
import * as THREE from 'three';
// … ported scene code …
export function mountJewel(canvas, { phone }) { /* returns { setMix, setStation, dispose } */ }
```
Rules:
- **Delete** all DOM and scroll code (`applyDom`, section queries, reveal observers). The page glue drives `setMix` (world weights) and `setStation` (`{x,y,scale}` in viewport fractions). Those two calls replace the prototype's internal scroll reading.
- Keep every material, colour and light value exactly.
- The station drives the rig position: convert viewport fractions to world units using the camera's visible width and height at the rig depth (the prototype's `visW`/`visH` math). The scale multiplies the base size.
- Keep the phone switches (`phone ? …`), the pixel-ratio cap, pause on `visibilitychange`, and cancel the RAF loop in `dispose()`.
- No `console.log`.

- [ ] **Step 6 [builder: `website/src/components/Jewel.astro`]: Write the component and its page glue**

```astro
---
import { dict, type Lang } from '../i18n';
interface Props { lang: Lang }
const { lang } = Astro.props;
const t = dict(lang);
---
<div class="jewel" aria-hidden="true">
  {['master', 'retail', 'clinic', 'gym'].map((w) => (
    <img class={`jewel-still still-${w}`} src={`/jewel/still-${w}.webp`} alt="" width="900" height="1200" decoding="async" />
  ))}
  <canvas class="jewel-canvas"></canvas>
</div>
<span class="visually-hidden">{t.a11y.jewel}</span>
<script>
  import { pickTier } from '../scripts/jewel/tier';
  import { HOME_DESKTOP, HOME_PHONE, stationAt } from '../scripts/jewel/stations';

  const root = document.documentElement;
  const layer = document.querySelector<HTMLElement>('.jewel')!;
  const canvas = layer.querySelector<HTMLCanvasElement>('.jewel-canvas')!;
  const rtl = root.dir === 'rtl';
  const phone = matchMedia('(max-width: 899px)').matches;
  const stations = phone ? HOME_PHONE : HOME_DESKTOP;
  const gl2 = !!document.createElement('canvas').getContext('webgl2');
  const nav = navigator as Navigator & { deviceMemory?: number; connection?: { saveData?: boolean } };
  const tier = pickTier({
    reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches, webgl2: gl2,
    deviceMemory: nav.deviceMemory, saveData: nav.connection?.saveData, width: innerWidth, dpr: devicePixelRatio,
  });
  layer.dataset.tier = tier;

  const centres = () => Array.from(document.querySelectorAll<HTMLElement>('[data-w]')).map((el) => el.offsetTop + el.offsetHeight / 2);
  let cs = centres();
  const place = () => {
    const s = stationAt(stations, cs, scrollY + innerHeight / 2, rtl);
    layer.style.setProperty('--jx', String(s.x));
    layer.style.setProperty('--jy', String(s.y));
    layer.style.setProperty('--js', String(s.scale));
    return s;
  };
  addEventListener('resize', () => { cs = centres(); place(); });
  place();

  if (tier === 'rich') {
    addEventListener('load', async () => {
      const { mountJewel } = await import('../scripts/jewel/scene.js');
      const j = mountJewel(canvas, { phone });
      root.addEventListener('worldmix', (e) => j.setMix((e as CustomEvent).detail));
      const tick = () => j.setStation(place());
      addEventListener('scroll', tick, { passive: true });
      tick();
      layer.classList.add('is-live');
    }, { once: true });
  } else {
    addEventListener('scroll', place, { passive: true });
  }
</script>
```
Its CSS goes into `components.css`, written by the same builder as a second file only if it is under 2 files total. Otherwise the main session dispatches it separately:
- `.jewel{position:fixed;inset:0;pointer-events:none;z-index:0}`
- `.jewel-still{position:absolute;height:calc(58vh*var(--js,1));left:calc(var(--jx,.73)*100%);top:calc(var(--jy,.54)*100%);transform:translate(-50%,-50%)}`. `left` is the one documented exception to the logical-properties rule: x is already mirrored by `stationAt`, so `inset-inline-start` would mirror it twice. Say so in a comment.
- `.still-master{opacity:var(--w-master,1)}`, `.still-retail{opacity:var(--w-retail,0)}`, `.still-clinic{opacity:var(--w-clinic,0)}`, `.still-gym{opacity:var(--w-gym,0)}`
- `.jewel.is-live .jewel-still{opacity:0;transition:opacity 1.1s}`
- `.jewel-canvas{position:absolute;inset:0;width:100%;height:100%;opacity:0}`
- `.jewel.is-live .jewel-canvas{opacity:1;transition:opacity 1.1s}`

- [ ] **Step 7 [main]: Build and commit**

`npm run build` (expected: success; the three chunk is a separate lazy file). Check with `Get-ChildItem dist/_astro -Filter *.js | Sort Length -Desc | Select -First 3`: the three chunk must not be imported by the entry script. Then:

`git add website/src/scripts/jewel website/src/components/Jewel.astro website/src/styles/components.css; git commit -m "feat(website): 3D jewel ported from the prototype, lazy rich tier with still fallback"`

---

### Task 10: Pages: language chooser, Home (ar/en), 404

**Files:**
- Create: `website/src/pages/index.astro` (chooser), `website/src/pages/[lang]/index.astro` (Home)
- Create: `website/src/pages/404.astro`
- Delete: the smoke page `website/src/pages/en/index.astro`

**Interfaces:**
- Consumes: everything above

- [ ] **Step 1 [builder: `website/src/pages/index.astro`, `website/src/pages/404.astro`]: Chooser and 404**

The chooser is indexable:
- `<Base lang="en" slug="" …>`, but its canonical must be `https://actionaura.me/`. Pass a `canonicalOverride`: the main session adds this optional prop to `Base.astro` in Step 3 (`canonicalOverride?: string`, used instead of `self` when present), and its hreflang points at `/ar/` and `/en/`, with x-default as the root.
- Content: the mark, the heading in both languages, two large links.
- Inline script:
  ```js
  (()=>{let s=null;try{s=localStorage.getItem('aa-lang')}catch(e){}
  /* pickLang logic duplicated inline: chooser must work before any bundle loads */
  const L=(x)=>x==='ar'||x==='en';let l=L(s)?s:null;
  if(!l){for(const r of (navigator.languages||[navigator.language||''])){const b=String(r).toLowerCase().split('-')[0];if(L(b)){l=b;break}}}
  location.replace('/'+(l||'en')+'/')})();
  ```
  The links set the choice on click: `onclick="try{localStorage.setItem('aa-lang','ar')}catch(e){}"`.
- Add a `<noscript>` note that the links work as plain links. They already do.

`404.astro`: Master world, noindex, the bilingual heading (`dict('en').notFound.title` and `dict('ar').notFound.title`), two links (`/ar/`, `/en/`), and the Jewel centred via a `.jewel--center` modifier class (x .5, y .45, scale 1.3; the stations script is not used on this page).

- [ ] **Step 2 [builder: `website/src/pages/[lang]/index.astro`]: Home**

```astro
---
import Base from '../../layouts/Base.astro';
import Nav from '../../components/Nav.astro';
import Footer from '../../components/Footer.astro';
import Jewel from '../../components/Jewel.astro';
import Chapter from '../../components/Chapter.astro';
import WhatsAppPill from '../../components/WhatsAppPill.astro';
import DemoLink from '../../components/DemoLink.astro';
import { dict, LANGS, type Lang } from '../../i18n';
import { pagePath } from '../../lib/seo';
import { organizationLd } from '../../lib/jsonld';

export function getStaticPaths() { return LANGS.map((lang) => ({ params: { lang } })); }
const lang = Astro.params.lang as Lang;
const t = dict(lang);
---
<Base lang={lang} slug="" title={t.meta.home.title} description={t.meta.home.description} jsonld={[organizationLd(lang)]}>
  <Nav lang={lang} slug="" />
  <Jewel lang={lang} />
  <main id="main">
    <section id="hero" data-w="master" class="hero">
      <div class="copy reveal">
        <h1>{t.home.hero.title}</h1>
        <p class="lead">{t.home.hero.lead}</p>
        <div class="ctas"><WhatsAppPill lang={lang} /><DemoLink lang={lang} /></div>
      </div>
    </section>
    <Chapter id="aura" lang={lang} world="master" title={t.home.company.title} accentWord={t.home.company.accent} lead={t.home.company.lead} />
    <Chapter id="retail" lang={lang} world="retail" status="available" flip title={t.home.retail.title} accentWord={t.home.retail.accent} lead={t.home.retail.lead} link={{ href: pagePath(lang, 'aura-retail'), label: t.home.retail.link }} />
    <Chapter id="clinic" lang={lang} world="clinic" status="available" title={t.home.clinic.title} accentWord={t.home.clinic.accent} lead={t.home.clinic.lead} link={{ href: pagePath(lang, 'aura-clinic'), label: t.home.clinic.link }} />
    <Chapter id="gym" lang={lang} world="gym" status="coming" flip title={t.home.gym.title} accentWord={t.home.gym.accent} lead={t.home.gym.lead} />
    <section id="talk" data-w="master" class="close">
      <div class="copy reveal">
        <h2>{t.home.close.title}</h2>
        <p class="lead">{t.home.close.lead}</p>
        <div class="ctas"><WhatsAppPill lang={lang} /><DemoLink lang={lang} /></div>
        <p class="price-line">{t.cta.price}</p>
      </div>
    </section>
  </main>
  <Footer lang={lang} />
  <script>
    import { startWorlds } from '../../scripts/worlds/apply';
    startWorlds(document.documentElement);
  </script>
</Base>
```
`Chapter` highlights `accentWord` by splitting the title on its first occurrence. If the word is not found, it renders the title unhighlighted and never duplicates text. Task 2's accent test guarantees the word is found.

- [ ] **Step 3 [main]: Add `canonicalOverride` to Base, delete the smoke page, build**

Edit `Base.astro`: add `canonicalOverride?: string` and `xDefaultOverride?: string` to `Props`. Use `canonicalOverride` for `self`, and `xDefaultOverride` for the x-default `href`, when present. The chooser passes `https://actionaura.me/` for both. Then:
```powershell
git rm -q website/src/pages/en/index.astro; cd website; npm run check; npm run build
```
Expected:
- `astro check` reports 0 errors (this proves the ar/en dictionary types match).
- The build emits `dist/index.html`, `dist/ar/index.html`, `dist/en/index.html` and `dist/404.html`.

- [ ] **Step 4 [main]: Commit**

`git add -A website/src; git commit -m "feat(website): language chooser, ar/en Home with chapters and jewel, 404"`

---

### Task 11: Site checks harness (Playwright over the built site)

**Files:**
- Create: `website/playwright.config.ts`, `website/tests/site/head.spec.ts`

**Interfaces:**
- Produces: `npm run test:site`, which builds nothing itself, serves `dist/` via `astro preview` and runs the checks.

- [ ] **Step 1 [builder: `website/playwright.config.ts`, `website/tests/site/head.spec.ts`]: Write the checks**

`playwright.config.ts`:
```ts
import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: 'tests/site',
  webServer: { command: 'npm run preview', url: 'http://localhost:4321/en/', reuseExistingServer: true, timeout: 60_000 },
  use: { baseURL: 'http://localhost:4321' },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
```

`head.spec.ts`:
```ts
import { test, expect } from '@playwright/test';

const PAGES = ['/ar/', '/en/'];

test.describe('every page head', () => {
  for (const p of PAGES) {
    test(`${p} has lang, dir, title, description, canonical, reciprocal hreflang, og`, async ({ page }) => {
      await page.goto(p);
      const lang = p.split('/')[1];
      await expect(page.locator('html')).toHaveAttribute('lang', lang);
      await expect(page.locator('html')).toHaveAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr');
      expect((await page.title()).length).toBeGreaterThan(10);
      const desc = await page.locator('meta[name=description]').getAttribute('content');
      expect(desc && desc.length).toBeGreaterThan(50);
      await expect(page.locator('link[rel=canonical]')).toHaveAttribute('href', `https://actionaura.me${p}`);
      const other = lang === 'ar' ? 'en' : 'ar';
      await expect(page.locator(`link[hreflang=${other}]`)).toHaveAttribute('href', `https://actionaura.me/${other}/`);
      await expect(page.locator('link[hreflang=x-default]')).toHaveAttribute('href', 'https://actionaura.me/en/');
      await expect(page.locator('meta[property="og:image"]')).toHaveCount(1);
    });
  }

  test('titles and descriptions are unique across pages', async ({ page }) => {
    const seen = new Set<string>();
    for (const p of PAGES) {
      await page.goto(p);
      const key = (await page.title()) + '|' + (await page.locator('meta[name=description]').getAttribute('content'));
      expect(seen.has(key), `${p} duplicates another page`).toBe(false);
      seen.add(key);
    }
  });

  test('every <img> has an alt attribute', async ({ page }) => {
    for (const p of PAGES) {
      await page.goto(p);
      const missing = await page.locator('img:not([alt])').count();
      expect(missing, p).toBe(0);
    }
  });

  test('chooser sends an arabic device to /ar/ and ignores junk storage', async ({ browser }) => {
    const ctx = await browser.newContext({ locale: 'ar-JO' });
    const page = await ctx.newPage();
    await page.addInitScript(() => { try { localStorage.setItem('aa-lang', 'fr'); } catch {} });
    await page.goto('/');
    await page.waitForURL('**/ar/');
    await ctx.close();
  });

  test('page has the WhatsApp link with the right number in both languages', async ({ page }) => {
    for (const p of PAGES) {
      await page.goto(p);
      const href = await page.locator('a[href^="https://wa.me/962780069485"]').first().getAttribute('href');
      expect(href).toContain('wa.me/962780069485');
    }
  });
});
```

- [ ] **Step 2 [main]: Install the browser and run**

```powershell
cd website; npx playwright install chromium; npm run build; npm run test:site
```
Expected: all pass. A failure here means the code is wrong, not the test. Fix the code through a builder.

- [ ] **Step 3 [main]: Mutation-prove two checks**

1. Remove the `hreflang=x-default` line from `Base.astro`, rebuild and run. The x-default assertion must fail. Restore.
2. In the chooser script, replace `L(s)?s:null` with `s`. The junk-storage test must fail (it lands on `/fr/`). Restore.

- [ ] **Step 4 [main]: Commit**

`git add website/playwright.config.ts website/tests/site; git commit -m "test(website): built-site head/hreflang/alt/chooser checks over astro preview"`

---

### Task 12: Visual verification (screenshots + contrast), then review

**Files:**
- Create: `website/tests/site/shots.spec.ts`, `website/tools/contrast.py`

- [ ] **Step 1 [builder: `website/tests/site/shots.spec.ts`, `website/tools/contrast.py`]: Write the shot and contrast tools**

`shots.spec.ts`:
- For each of `/ar/` and `/en/`, at widths 320, 390, 768, 1024 and 1440 (height 900), in normal and `reducedMotion: 'reduce'` contexts:
  - scroll to each `[data-w]` section centre;
  - wait 3000 ms;
  - screenshot to `shots/<lang>-<w>-<motion>-<id>.png`;
  - then inject `main,nav,footer{visibility:hidden}` and screenshot again to `…-bg.png`;
  - write the computed `color` of the section's `h2`/`h1` and `.lead` plus their bounding boxes to `shots/<same>.json`.
- It asserts only `document.documentElement.scrollWidth <= innerWidth` (no horizontal scroll) and zero console errors.

`contrast.py` (Python 3 + PIL):
- For every `*.json`, it loads the `-bg.png` and, for each text box, computes the WCAG contrast of the text colour (alpha-composited over each pixel) against every pixel inside the box.
- It prints `OK`/`LOW` with the min and the 2nd percentile.
- It exits non-zero if any min < 4.5.

Port the math from `C:\Users\MSI\.claude\jobs\b6c06c3b\tmp\website\v2\shots\_shoot.py` (`lum`, `contrast`, `measure`). Read that file, keep the formulas and drop the Playwright parts.

- [ ] **Step 2 [main]: Run and look**

```powershell
cd website; npm run build; npx playwright test tests/site/shots.spec.ts; py -3.14 tools/contrast.py shots
```
Expected:
- The specs pass and `contrast.py` exits 0.
- **Then the main session opens a contact sheet of the 1440 and 390 shots for both languages and looks at every one.** It checks: the jewel is at its station and never behind text; worlds have no grey haze; the Arabic layout is mirrored; the mark is not mirrored; the reduced-motion shots show the still, not an empty space.
- Any defect goes to the design director (Fable) as a screenshot plus a description, comes back as a spec delta, and is executed by a builder. Then re-run this step.

Known gap for this plan: the jewel stills (`/jewel/still-*.webp`) are produced in Plan 3. Until then the reduced-motion shots will show no jewel. Record this as **expected-red** in the task log with the shot names. Do not count it as passing. Plan 3 Task "render stills" closes it.

- [ ] **Step 3 [main]: Independent review**

Dispatch `feature-dev:code-reviewer` (fresh context) over `git diff feat/money-fixes...feat/website -- website/`. Ask it to refute three things: the parity guarantee, the station and world math on deep loads, and the honesty rules in the copy. Fix confirmed findings via builders. Re-run Tasks 2, 4, 7, 8 and 9's tests and Task 11.

- [ ] **Step 4 [main]: Commit and report**

`git add website/tests/site/shots.spec.ts website/tools/contrast.py; git commit -m "test(website): screenshot + contrast harness for every world and width"`

Report to the owner in plain words, with the contact sheet attached.
