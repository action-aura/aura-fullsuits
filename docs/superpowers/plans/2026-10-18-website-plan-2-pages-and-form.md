# Action Aura Website, Plan 2: Pages + Demo Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Aura Retail, Aura Clinic, About + Partners, FAQ, Book a demo / Contact, Privacy and Terms pages in Arabic and English, and add the demo form. The form validates in the browser, posts to a Cloudflare Pages Function, the function stores each lead in D1 first, and only then notifies.

**Architecture:**
- **Copy.** Page copy lives in per-page dictionary modules (`src/i18n/<page>/{en,ar}.ts`), which are merged into the existing typed `en`/`ar` dictionaries, so the parity type and the parity test cover every page.
- **Shared components.** Product pages share one `ProductPage` layout, built from new components: ShotFrame, PhotoFrame, DayRail, FeatureGrid and CtaBlock.
- **Jewel.** The jewel gets per-page-type station tables. These are pure and unit-tested.
- **Form logic.** One pure `validateLead()` module is shared by the browser form and the Pages Function, and it is unit-tested.
- **Pages Function.** The function is a thin, unit-tested handler over that module. Its guards are: Turnstile, honeypot, rate limit, store-before-notify.

**Tech Stack:** As in Plan 1 (Astro 5.18.2, Vitest 3.2.7, @playwright/test 1.56.1), plus Cloudflare Pages Functions (TypeScript) and Cloudflare D1. There are no new runtime npm dependencies. The Pages Function's types come from a local `functions/env.d.ts`, not from `@cloudflare/workers-types`, so nothing new needs installing.

**Spec:** `docs/superpowers/specs/2026-10-18-action-aura-website-design.md`. Visual rules come from `website/docs/design-system.md`, Retail claims from `website/docs/messaging.md`, and Clinic claims from `website/docs/clinic-claims.md` (SHIPPED rows only, using its wording).

## Execution model
The same model as Plan 1:
- **Fable** answers design questions.
- **`caveman:cavecrew-builder`** writes the code, at most 2 files per dispatch, with no shell.
- **The main session** runs every shell step, verifies by looking at screenshots, and mutation-proves every guard.
- git runs through PowerShell.

## Global Constraints
- Everything in Plan 1's Global Constraints still applies verbatim: pinned versions, no framework, Arabic typography rules, no em-dash and no "!", only Retail and Clinic are available, the banned-claims list, WhatsApp `https://wa.me/962780069485` with the true-green disc, logical properties, contrast ≥ 4.5:1, reduced motion, `/ar/` and `/en/` with trailing slashes.
- Clinic claims must come from the SHIPPED rows of `website/docs/clinic-claims.md` and use its wording. In particular, write "own appointments, treatments and invoices", never "own patients". Never claim iOS, online booking, WhatsApp or SMS reminders, report export, or e-invoicing submission.
- Retail claims must come from `website/docs/messaging.md` §4. Ruling from the Plan 1 review: the line "receipts in Arabic and English" is cut. "Dinar to 3 decimals" is written as fils, with no "دينار" or "JOD" word, because the claims test bans currency words so that prices never slip in.
- The form keeps only these fields: name, phone, business type, business name, branches, message, lang, plus a salted IP hash for rate limiting. The Privacy page states exactly this list.
- No tracking cookies. The Turnstile widget is the only third-party script, and it loads only on the contact page.

## Review Focus
1. **Phone typed with Arabic-Indic digits** (٠٧٨…), spaces, dashes or a `+962` prefix. It is normalised and accepted, and stored as Western digits. Pinned in Task 2.
2. **Double submit, rapid resubmits or a bot flood.** The button is disabled while sending, and the server rate-limits per IP hash (5 per 10 minutes returns 429). Pinned in Tasks 2, 7 and 9.
3. **The network or the function fails.** The form shows a bilingual error, keeps every typed value, and offers WhatsApp instead. Pinned in Task 7 (UI test with the route aborted).
4. **JavaScript disabled.** The form still posts natively. The function answers form-encoded posts with 303 to `/<lang>/contact/?sent=1`, which shows the success state. Pinned in Tasks 6 and 7.
5. **Hostile input**: HTML in fields, oversized fields, a filled honeypot, a missing Turnstile token. Length caps apply. Values are stored as plain text and HTML-escaped in any notification. A filled honeypot gets a fake 200 and nothing is stored. A missing token gets 403. Pinned in Task 6.

---

### Task 1: Dictionary modules for every new page (+ Plan 1 carry-overs)

**Files:**
- Create: `src/i18n/pages/retail.en.ts`, `src/i18n/pages/retail.ar.ts`
- Create: `src/i18n/pages/clinic.en.ts`, `src/i18n/pages/clinic.ar.ts`
- Create: `src/i18n/pages/about.en.ts`, `src/i18n/pages/about.ar.ts`
- Create: `src/i18n/pages/faq.en.ts`, `src/i18n/pages/faq.ar.ts`
- Create: `src/i18n/pages/contact.en.ts`, `src/i18n/pages/contact.ar.ts`
- Create: `src/i18n/pages/legal.en.ts`, `src/i18n/pages/legal.ar.ts` (privacy + terms)
- Modify: `src/i18n/en.ts`, `src/i18n/ar.ts`. Import each page module and add it under `pages: { retail, clinic, about, faq, contact, legal }`. Add the meta entries for the 7 pages, plus `a11y.nav` ('Main' / 'القائمة الرئيسية') and `chooser.noscript` (the existing chooser noscript sentence, in en and ar).
- Modify: `src/components/Nav.astro` (`aria-label={t.a11y.nav}`) and `src/pages/index.astro` (noscript from dict).

**Interfaces:**
- Produces: `dict(lang).pages.<page>.*`, `dict(lang).meta.<page>.{title,description}` for retail, clinic, about, faq, contact, privacy and terms; `dict(lang).a11y.nav`; `dict(lang).chooser.noscript`.

Content of each module, in shape order:

- **retail**
  - `hero{status,title,accent,lead}`. Title: "The till that keeps selling offline." Accent: "offline".
  - `rail{title, moments[6]{title,line,shot,alt}}`. The moments are, in order: open the till, scan and sell, internet drops, phone mirrors the counter, end of day, the books.
    - `shot` is a screenshot key; the valid keys are listed in Task 3.
    - `line` is ≤ 20 words, and only shipped behaviour (messaging §4).
  - `features{title, items[6]{title,line}}`, from messaging §4.
  - `works{title,line}`. Hardware: barcode scanners, receipt printers on the shop computer. No drawer claim.
  - `jordan{title, items[4]}`: fils to 3 decimals, 16% tax by default, CliQ, full Arabic RTL.
  - `run{title,line}`, from messaging "What you need to run it".
  - `cta{title,lead}`.
- **clinic**: the same shape as retail. `rail.moments[5]`: book the appointment, open the patient file, chart and plan the treatment, the follow-up sheet and payment, end of day reports. `features.items[6]` and `jordan` are replaced by `privacy{title,items[3]}`: encrypted on the clinic PC, roles and permissions, works offline. Every line traces to a SHIPPED row of clinic-claims.md. Add a trailing comment `// claims: clinic-claims.md row N` per line.
- **about**: `hero{title:"One mark, one way of working.", lead}`, `story{title, paragraphs[2-3]}` (content.py about_body), `values{title, items[5]}`, `how{title, steps[5]}` (discovery, setup, data migration, training, support), `products{title, retail, clinic, gym, more}`, `partners{title, lead, areas[4]}` (content.py partners).
  - Gym and "more" carry Coming.
  - Aura Platforms is "in development, features revealed at launch".
  - AI and Meta are worded exactly as in messaging §5.
- **faq**: `title`, `lead`, `items[12]{q,a}`. Questions:
  1. Does it work without internet?
  2. Is it in Arabic?
  3. What do I need to run it?
  4. Which barcode scanners and printers?
  5. Can I see the shop from my phone?
  6. Several branches?
  7. Is my data safe?
  8. How much does it cost? Answer: "Pricing depends on your shop. Ask us."
  9. Do you install and train?
  10. Can you move my data from another system?
  11. Is Aura Clinic for dental clinics? (SHIPPED features only.)
  12. What about Aura Gym? (Coming.)

  Every answer uses only allowed claims.
- **contact**: `title`, `lead`, `form{name,phone,phoneHelp,type,typeOptions{shop,clinic,other},businessName,branches,branchesOptions{one,few,many},message,optional,send,sendWa,sending,success,errorNetwork,errorServer,errorRate,errors{nameRequired,nameLong,phoneRequired,phoneInvalid,tooLong}}`, `direct{title,whatsapp,call,email,office,hours}`.
  - The `hours` value is the literal text "Sunday to Thursday, 9:00 to 17:00", marked `// OWNER: confirm hours`. Ruling: if unconfirmed at launch, the line is removed, not invented. Plan 3 owns the launch checklist.
- **legal**: `privacy{title,updated,draftNote,sections[]{h,p[]}}` and `terms{…same}`.
  - `draftNote`: "Draft. A lawyer must review this text before launch." / "مسودة. يجب أن يراجع محامٍ هذا النص قبل الإطلاق."
  - The Privacy sections state: the exact stored fields (see Global Constraints), why, retention of 24 months, Cloudflare as host and processor, the cookieless analytics, that there are no tracking cookies, that Turnstile is an anti-abuse check, and the contact for deletion requests (info@actionaura.me).
  - Terms covers website use only (not the software licence): content accuracy, no prices offered here, links, Jordanian law.

- [ ] **Step 1 [builder ×6, parallel, disjoint pairs]: write each page's `en` and `ar` module pair.** Arabic lines without a source carry `// AR: drafted, native check pending`. Each module exports `export const retailEn = {…} as const;` / `export const retailAr: typeof retailEn extends infer T ? DeepStrings<T> : never = {…}`. Use `import type { DeepStrings } from '../types';`. Note: Step 2 creates `src/i18n/types.ts` with `export type DeepStrings<T> = …` moved out of en.ts.
- [ ] **Step 2 [builder: `src/i18n/types.ts`, `src/i18n/en.ts`]: move `DeepStrings` into `types.ts`, import the 6 en modules into `en.ts` under `pages`, and add the meta, a11y.nav and chooser.noscript keys.**
- [ ] **Step 3 [builder: `src/i18n/ar.ts`, `src/components/Nav.astro`]: do the same for ar, then use `t.a11y.nav` in Nav.** Then a builder handles `src/pages/index.astro`: noscript text from `dict('en').chooser.noscript` and `dict('ar').chooser.noscript`.
- [ ] **Step 4 [main]: run** `npx vitest run tests/unit/i18n.test.ts` (parity, copy rules and banned claims over every new string) and `npx -y node@22.12.0 node_modules/astro/astro.js check`. Expected: PASS and 0 errors.
- [ ] **Step 5 [main]: mutation-prove that the new copy is covered.** Add "iOS app" to `faq.en` item 1's answer. The banned test must fail. Restore. Commit: `feat(website): per-page ar/en dictionaries for every Plan 2 page`.

---

### Task 2: Shared lead validation (pure) with tests

**Files:**
- Create: `src/lib/lead.ts`
- Test: `tests/unit/lead.test.ts`

**Interfaces:**
- Produces:
  - `type LeadInput = Record<string, unknown>`
  - `type Lead = { name: string; phone: string; businessType: 'shop'|'clinic'|'other'|''; businessName: string; branches: '1'|'2-3'|'more'|''; message: string; lang: 'ar'|'en' }`
  - `type LeadError = 'nameRequired'|'nameLong'|'phoneRequired'|'phoneInvalid'|'tooLong'`
  - `validateLead(input: LeadInput): { ok: true; lead: Lead } | { ok: false; errors: Partial<Record<keyof Lead, LeadError>> }`
  - `normalizeDigits(s: string): string`
  - `normalizePhone(s: string): string | null` (Western digits, `+962XXXXXXXXX` for Jordan mobiles, `+<intl>` otherwise, null if invalid)
  - `LIMITS = { name: 80, businessName: 120, message: 1000 }`

- [ ] **Step 1 [builder: `tests/unit/lead.test.ts`]: write the failing test**

```ts
import { describe, it, expect } from 'vitest';
import { validateLead, normalizePhone, normalizeDigits, LIMITS } from '../../src/lib/lead';

describe('normalizeDigits', () => {
  it('maps Arabic-Indic and Persian digits to Western', () => expect(normalizeDigits('٠٧٨٠٠٦٩٤٨٥ ۱۲')).toBe('0780069485 12'));
});

describe('normalizePhone', () => {
  it.each([
    ['0780069485', '+962780069485'], ['078 006 9485', '+962780069485'], ['+962 78-006-9485', '+962780069485'],
    ['00962780069485', '+962780069485'], ['٠٧٨٠٠٦٩٤٨٥', '+962780069485'], ['780069485', '+962780069485'],
    ['+971501234567', '+971501234567'],
  ])('%s -> %s', (raw, want) => expect(normalizePhone(raw)).toBe(want));
  it.each(['', '12', '07800', '0680069485', 'abc', '+0123456789', '+1234567890123456'])('rejects %s', (raw) => expect(normalizePhone(raw)).toBeNull());
});

describe('validateLead', () => {
  const good = { name: '  Ahmad  ', phone: '٠٧٨٠٠٦٩٤٨٥', businessType: 'shop', businessName: '', branches: '1', message: '', lang: 'ar' };
  it('accepts a minimal valid lead and trims/normalises', () => {
    const r = validateLead(good);
    expect(r.ok).toBe(true);
    if (r.ok) { expect(r.lead.name).toBe('Ahmad'); expect(r.lead.phone).toBe('+962780069485'); expect(r.lead.lang).toBe('ar'); }
  });
  it('requires name and phone', () => {
    const r = validateLead({ ...good, name: ' ', phone: '' });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors).toEqual({ name: 'nameRequired', phone: 'phoneRequired' });
  });
  it('rejects an invalid phone', () => {
    const r = validateLead({ ...good, phone: '12345' });
    expect(!r.ok && r.errors.phone).toBe('phoneInvalid');
  });
  it('caps lengths', () => {
    const r = validateLead({ ...good, name: 'x'.repeat(LIMITS.name + 1), message: 'y'.repeat(LIMITS.message + 1) });
    expect(!r.ok && r.errors).toEqual({ name: 'nameLong', message: 'tooLong' });
  });
  it('coerces unknown enum values to empty, never trusts them', () => {
    const r = validateLead({ ...good, businessType: '<script>', branches: '99', lang: 'fr' });
    expect(r.ok && [r.lead.businessType, r.lead.branches, r.lead.lang]).toEqual(['', '', 'en']);
  });
  it('treats non-string fields as empty', () => {
    const r = validateLead({ ...good, name: 42 as unknown as string });
    expect(!r.ok && r.errors.name).toBe('nameRequired');
  });
  it('keeps HTML as plain text (escaping is the renderer job)', () => {
    const r = validateLead({ ...good, businessName: '<b>Shop</b>' });
    expect(r.ok && r.lead.businessName).toBe('<b>Shop</b>');
  });
});
```

- [ ] **Step 2 [main]: run it and watch it fail** (module not found).
- [ ] **Step 3 [builder: `src/lib/lead.ts`]: implement**

```ts
export const LIMITS = { name: 80, businessName: 120, message: 1000 } as const;
export type LeadError = 'nameRequired' | 'nameLong' | 'phoneRequired' | 'phoneInvalid' | 'tooLong';
export type Lead = {
  name: string; phone: string; businessType: 'shop' | 'clinic' | 'other' | ''; businessName: string;
  branches: '1' | '2-3' | 'more' | ''; message: string; lang: 'ar' | 'en';
};
export type LeadInput = Record<string, unknown>;

const str = (v: unknown): string => (typeof v === 'string' ? v : '');

// Arabic keyboards type Arabic-Indic (U+0660..9) or Persian (U+06F0..9) digits; store Western digits only.
export function normalizeDigits(s: string): string {
  return s.replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660))
          .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06F0));
}

// Jordan mobiles: 07[789]XXXXXXX, 7[789]XXXXXXX, (00|+)9627[789]XXXXXXX -> +9627XXXXXXXX.
// Anything else must be a plausible E.164 number: + then 8 to 15 digits, first digit 1-9.
export function normalizePhone(raw: string): string | null {
  const s = normalizeDigits(raw).replace(/[\s\-().]/g, '');
  if (!s) return null;
  const jo = s.match(/^(?:\+962|00962|0)?(7[789]\d{7})$/);
  if (jo) return `+962${jo[1]}`;
  const intl = s.replace(/^00/, '+');
  if (/^\+[1-9]\d{7,14}$/.test(intl) && !intl.startsWith('+962')) return intl;
  return null;
}

export function validateLead(input: LeadInput):
  | { ok: true; lead: Lead }
  | { ok: false; errors: Partial<Record<keyof Lead, LeadError>> } {
  const errors: Partial<Record<keyof Lead, LeadError>> = {};
  const name = str(input.name).trim();
  const phoneRaw = str(input.phone).trim();
  const businessName = str(input.businessName).trim();
  const message = str(input.message).trim();
  if (!name) errors.name = 'nameRequired';
  else if (name.length > LIMITS.name) errors.name = 'nameLong';
  const phone = phoneRaw ? normalizePhone(phoneRaw) : null;
  if (!phoneRaw) errors.phone = 'phoneRequired';
  else if (!phone) errors.phone = 'phoneInvalid';
  if (businessName.length > LIMITS.businessName) errors.businessName = 'tooLong';
  if (message.length > LIMITS.message) errors.message = 'tooLong';
  if (Object.keys(errors).length) return { ok: false, errors };
  const bt = str(input.businessType);
  const br = str(input.branches);
  return {
    ok: true,
    lead: {
      name, phone: phone!, businessName, message,
      businessType: bt === 'shop' || bt === 'clinic' || bt === 'other' ? bt : '',
      branches: br === '1' || br === '2-3' || br === 'more' ? br : '',
      lang: str(input.lang) === 'ar' ? 'ar' : 'en',
    },
  };
}
```

- [ ] **Step 4 [main]: run it and watch it pass.**
- [ ] **Step 5 [main]: mutation-prove two guards.**
  - Delete the `normalizeDigits` call in `normalizePhone`. The Arabic-Indic case fails.
  - Change `7[789]` to `7\d`. The `0680…` rejection still passes, so add the case `'0750069485'` to the rejects list first; it must fail under the mutant.

  Restore after each. Commit: `feat(website): shared lead validation with Arabic-digit phone normalisation`.

---

### Task 3: Station tables per page type + screenshot assets

**Files:**
- Modify: `src/scripts/jewel/stations.ts`. Add `PRODUCT_DESKTOP`, `ABOUT_DESKTOP`, `CONTACT_DESKTOP` and `READ_DESKTOP`, plus `stationsFor(kind: 'home'|'product'|'about'|'contact'|'read', phone: boolean): Station[] | 'none'`.
- Test: `tests/unit/stations.test.ts` (extend)
- Create: `src/assets/shots/` (copied, see Step 4) and `src/lib/shots.ts`

**Interfaces:**
- Produces:
  - `stationsFor(kind, phone)`
  - `SHOTS: Record<ShotKey, { en: ImageMetadata; ar: ImageMetadata | null; alt: { en: string; ar: string } }>`
  - `ShotKey = 'pos'|'dashboard'|'reports'|'trialBalance'|'products'|'customers'|'posNight'`

Stations (design-system §5), as `{x,y,scale}` desktop arrays with one entry per `[data-w]` section, in page order:

| Kind | Sections | Stations |
|---|---|---|
| product | hero, rail, features, photos, works, cta | `[ {.73,.54,1}, {.76,.5,.7}, {.76,.5,.7}, {.76,.5,.7}, {.76,.5,.7}, {.73,.56,.9} ]` |
| about | hero, story, products, partners | `[ {.5,.36,1.2}, {.75,.52,.8}, {.75,.52,.8}, {.75,.52,.8} ]` |
| contact | form | `[ {.75,.62,.9} ]` |
| read | — | `'none'`. Privacy, Terms and FAQ use a lite still (Task 4) and no jewel stations. |

Phones:
- product: hero only, then `'hide-after-hero'`. Ruling: implement as stations `[ {.5,.5,1}, …rest with scale 0 ]`. Scale 0 hides the mark, and the scene and still treat `scale<=0.01` as hidden, so the API stays a Station array.
- about: centred, apex 40%. Stations `[ {.5,.4,1}, {.5,.4,1}, … ]`.
- contact: `[ {.5,.15,.4} ]` (small, above the form).

- [ ] **Step 1 [builder: `tests/unit/stations.test.ts`]: add tests.**
  - `stationsFor('product', false)` has 6 entries, starting with hero `{0.73,0.54,1}`.
  - `stationsFor('read', true)` is `'none'`.
  - Every desktop x lies in [0.25, 0.75] except the about hero (0.5).
  - Phone product stations after the hero have `scale === 0`.
  - `stationsFor('home', …)` returns the existing HOME tables (same reference).
- [ ] **Step 2 [main]: run it and watch it fail.** **Step 3 [builder: `stations.ts`]: implement.** **[main]: run it and watch it pass.**
- [ ] **Step 4 [main]: copy the screenshots.** Copy `Desktop\Aura Brand Kit\5 App screenshots\*.png` to `website/src/assets/shots/` with kebab names: `pos-en.png`, `pos-ar.png`, `dashboard-en.png`, `dashboard-ar.png`, `reports-en.png`, `trial-balance-en.png`, `products-en.png`, `customers-en.png`, `pos-night-en.png`.
  - Astro converts them to AVIF/WebP at build.
  - Ruling: keys with no Arabic shot render the English shot on Arabic pages, captioned "Screen shown in English" / "الشاشة معروضة بالإنجليزية". This is honest, and the fix is a Plan 3 capture.
- [ ] **Step 5 [builder: `src/lib/shots.ts`]: write the typed map** using `import posEn from '../assets/shots/pos-en.png'` and so on. Bilingual alt text names the screen: "Aura Retail till, sale in progress" / "كاشير أورا ريتيل أثناء عملية بيع".
- [ ] **Step 6 [main]: run the build and commit.** Commit message: `feat(website): station tables per page type and the real Retail screenshots`.

---

### Task 4: Product-page components

**Files** (builder pairs, sequential where CSS is shared):
- Create: `src/components/ShotFrame.astro`, `src/components/PhotoFrame.astro`
- Create: `src/components/DayRail.astro`, `src/scripts/rail.ts`
- Create: `src/components/FeatureGrid.astro`, `src/components/CtaBlock.astro`
- Create: `src/styles/pages.css` (every style for the new components and pages; imported by Base after components.css)
- Modify: `src/components/Jewel.astro`. Add the prop `stations?: 'home'|'product'|'about'|'contact'|'read'` (default 'home'). It uses `stationsFor`. `'read'` renders only the master still, top-right at 28% height on desktop and nothing on phones, with no canvas and no scroll logic. Scale ≤ 0.01 sets `opacity:0` on both the still and the canvas (via a `--jhide` var).

**Interfaces:**
- `<ShotFrame shot={ShotKey} lang caption? ratio?="16/10">` renders the double bezel and an `<Image>` from `astro:assets`, using the Arabic shot when present (else the English one with the caption above), with `loading="lazy"`.
- `<PhotoFrame src={ImageMetadata} alt caption ratio?="3/2">` renders the same bezel. The caption is required ("Illustration: …" / "صورة توضيحية: …").
- `<DayRail lang moments={[{title,line,shot}]}>` renders `<ol class="rail" tabindex="0" aria-label>` with scroll-snap. `rail.ts` makes ArrowLeft and ArrowRight (mirrored in RTL) move one card.
- `<FeatureGrid items={[{title,line,shot?}]}>` renders a bento of exactly 6 or 5 cells. At most 2 cells carry a ShotFrame.
- `<CtaBlock lang title lead>` renders a section `data-w` with the WhatsApp pill, the demo link and the price line.

- [ ] **Step 1 [builder: ShotFrame + PhotoFrame]**, **Step 2 [builder: DayRail + rail.ts]**, **Step 3 [builder: FeatureGrid + CtaBlock]**. Use class names only, with no `<style>`. Each reply lists its classes.
- [ ] **Step 4 [builder: `src/styles/pages.css`, `src/layouts/Base.astro`]**: write every style, following design-system §7 (rail, bezel, bento, caption) and §4 rhythm, and add `import '../styles/pages.css'` to Base.
- [ ] **Step 5 [builder: Jewel.astro]**: add the stations prop as above.
- [ ] **Step 6 [main]**: build, then run the banned-property lint on pages.css. Commit: `feat(website): product-page components (rail, frames, bento, CTA block)`.

---

### Task 5: Pages: Aura Retail, Aura Clinic, About, FAQ, Privacy, Terms

**Files:**
- Create: `src/layouts/ProductPage.astro`. Props are `lang`, `slug`, `world`, `copy` (a page dict) and `shots`. It renders: Nav, Jewel (stations product), hero, DayRail, FeatureGrid, photos (if any), works/jordan or privacy, run, CtaBlock and Footer. Every section is `data-w={world}`.
- Create: `src/pages/[lang]/aura-retail/index.astro` and `src/pages/[lang]/aura-clinic/index.astro`. These are thin: getStaticPaths, then ProductPage with `world="retail"` or `"clinic"`, plus BreadcrumbList and SoftwareApplication JSON-LD.
  - SoftwareApplication has: name, applicationCategory "BusinessApplication", operatingSystem ("Windows, Android" for both), inLanguage `["ar","en"]` and publisher. It has NO offers and NO rating.
  - Add `softwareLd(lang, name, description)` to `src/lib/jsonld.ts`, with a unit test asserting no offers, aggregateRating or review fields.
- Create: `src/pages/[lang]/about/index.astro` (Jewel stations about; the products row uses StatusPill), `src/pages/[lang]/faq/index.astro` (Read layout plus FAQPage JSON-LD via `faqLd(lang, items)` in jsonld.ts, unit-tested: `@type` FAQPage, one Question per item, acceptedAnswer text equal to the dict answer), `src/pages/[lang]/privacy/index.astro` and `src/pages/[lang]/terms/index.astro` (Read layout with the visible draft note box).
- Create: `src/layouts/ReadPage.astro`. Prose at 65ch, h2/h3 rhythm, Jewel stations read, Nav, Footer.

- [ ] **Step 1 [builder: `src/lib/jsonld.ts`, `tests/unit/jsonld.test.ts`]**: add `softwareLd` and `faqLd` with their tests. [main]: watch the tests fail before the code exists, then pass, then mutation-prove: add `offers:{}` and the test fails.
- [ ] **Step 2 [builder: ProductPage.astro + ReadPage.astro]**, **Step 3 [builder: retail + clinic pages]**, **Step 4 [builder: about + faq pages]**, **Step 5 [builder: privacy + terms pages]**.
- [ ] **Step 6 [main]**: `astro check` under Node 22 (0 errors), the build, and every page present in `dist/{ar,en}/{aura-retail,aura-clinic,about,faq,privacy,terms}/index.html`. Commit: `feat(website): Retail, Clinic, About, FAQ, Privacy and Terms pages in ar/en`.

---

### Task 6: The demo Pages Function (store first, then notify)

**Files:**
- Create: `functions/env.d.ts`. Minimal local types: `D1Database` (prepare/bind/run/first) and `PagesFunction`-like `EventContext<Env>` with `request` and `env`. Only what is used.
- Create: `functions/api/demo.ts`, `functions/_lib/notify.ts`
- Create: `migrations/0001_leads.sql`, `wrangler.toml` (pages_build_output_dir "dist"; d1_databases binding "DB", database_name "aura-leads", database_id placeholder `"SET-IN-PLAN-3"` with a comment). Ruling: the real id comes from the Cloudflare account in Plan 3, so this file names that.
- Test: `tests/unit/demo-function.test.ts`

**Interfaces:**
- `onRequestPost(ctx: { request: Request; env: Env }): Promise<Response>`.
- Env:
  - `{ DB: D1Database; TURNSTILE_SECRET?: string; IP_SALT: string; ALLOW_NO_TURNSTILE?: string; NOTIFY_PROVIDER?: 'none'|'webhook'; NOTIFY_WEBHOOK_URL?: string }`
  - Ruling: the notify provider is pluggable. 'none' stores only. 'webhook' POSTs a JSON summary to a URL, for example an email-forwarding service the owner picks in Plan 3. Real email is chosen and wired in Plan 3. Cost if wrong: leads are not mailed until Plan 3, but none are lost, because D1 holds them.
- `notify(env, lead, id): Promise<{ ok: boolean; error?: string }>`.

Behaviour, in order:
1. Parse the body as JSON (`application/json`) or form (`application/x-www-form-urlencoded` / multipart). Anything else gives 415.
2. A filled honeypot (`website` field non-empty) gives 200 `{ok:true}`. Nothing is stored or notified.
3. Turnstile:
   - When `TURNSTILE_SECRET` is set, POST `secret`, `response` (the `cf-turnstile-response` field) and `remoteip` to `https://challenges.cloudflare.com/turnstile/v0/siteverify`. A failure gives 403 `{ok:false,error:'challenge'}`.
   - When the secret is unset and `ALLOW_NO_TURNSTILE !== '1'`, give 500 `{ok:false,error:'config'}`. This fails closed.
4. `validateLead`. Failure gives 422 `{ok:false,errors}`.
5. `ipHash = sha256(IP_SALT + (request.headers.get('CF-Connecting-IP') ?? ''))` as hex. Rate limit: `SELECT COUNT(*) AS n FROM leads WHERE ip_hash=? AND created_at > datetime('now','-10 minutes')`. If n ≥ 5, give 429.
6. `INSERT INTO leads (created_at, name, phone, business_type, business_name, branches, message, lang, ip_hash) VALUES (datetime('now'),?,?,?,?,?,?,?,?)`. Get the id from `meta.last_row_id`.
7. `notify(...)`. On failure, `UPDATE leads SET notify_error=? WHERE id=?`. The response is still success, because the lead is safe.
8. Respond. The `Accept` header decides the format, not the content-type, because the JS form also sends FormData.
   - If `Accept` includes `application/json`: reply with JSON, either 200 `{ok:true}` or the error status with a JSON body.
   - Otherwise (a plain no-JS form post): reply 303 with Location `/${lead.lang}/contact/?sent=1`. A validation error gets 303 to `/${lang}/contact/?error=1`.

`migrations/0001_leads.sql`:
```sql
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  name TEXT NOT NULL,
  phone TEXT NOT NULL,
  business_type TEXT NOT NULL DEFAULT '',
  business_name TEXT NOT NULL DEFAULT '',
  branches TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  lang TEXT NOT NULL DEFAULT 'en',
  ip_hash TEXT NOT NULL,
  notify_error TEXT
);
CREATE INDEX IF NOT EXISTS leads_ip_time ON leads (ip_hash, created_at);
```

- [ ] **Step 1 [builder: `tests/unit/demo-function.test.ts`]: write failing tests with a fake D1.** The fake records SQL plus binds; `first()` returns `{n: <configurable>}` and `run()` returns `{meta:{last_row_id: 7}}`. `globalThis.fetch` is mocked for Turnstile and the webhook. Cases:
  - (a) valid JSON lead + Turnstile success gives 200, exactly one INSERT, and the phone is stored normalised.
  - (b) honeypot filled gives 200 and zero DB calls.
  - (c) Turnstile failure gives 403 and no INSERT.
  - (d) no secret and no allow flag gives 500 and no INSERT.
  - (e) rate count 5 gives 429 and no INSERT.
  - (f) a validation error gives 422 with errors.phone.
  - (g) form-encoded valid gives 303 to `/ar/contact/?sent=1` when lang=ar.
  - (h) webhook notify throws: still 200, and an UPDATE with notify_error is issued AFTER the INSERT (assert order).
  - (i) text/plain gives 415.
  - (j) the INSERT happens before any notify fetch (order check: the store-first guarantee).
  - (k) a form-encoded body with `accept: application/json` gives 200 JSON, not 303.
- [ ] **Step 2 [main]: run it and watch it fail.** **Step 3 [builder: `functions/api/demo.ts`, `functions/_lib/notify.ts`]**: implement as specified. `notify` 'webhook' sends `{id, name, phone, businessType, businessName, branches, message, lang}` as JSON with `content-type: application/json`. It never embeds raw HTML; any consumer must escape. **[builder: `functions/env.d.ts`, `wrangler.toml`]** + **[main: create `migrations/0001_leads.sql`]**.
- [ ] **Step 4 [main]: run it and watch it pass. Mutation-prove**, restoring after each:
  1. Swap INSERT and notify order: case (j) fails.
  2. Skip the honeypot check: case (b) fails.
  3. Treat a missing secret as pass: case (d) fails.
  4. `>= 5` changed to `> 5`: case (e) fails.
- [ ] **Step 5 [main]: commit** `feat(website): demo Pages Function: store in D1 first, Turnstile, honeypot, rate limit`.

---

### Task 7: Contact page + DemoForm (browser)

**Files:**
- Create: `src/components/DemoForm.astro`, `src/scripts/demo-form.ts`
- Create: `src/pages/[lang]/contact/index.astro` (Jewel stations contact). It has two columns per design-system §8 and shows success when `?sent=1`.
- Test: `tests/site/form.spec.ts`

**Behaviour:**
- `<form method="post" action="/api/demo">` posts natively without JS.
- Hidden fields: `lang`, and the honeypot `website` (`tabindex="-1" autocomplete="off"`, visually hidden, `aria-hidden`).
- The Turnstile widget div is `class="cf-turnstile" data-sitekey={import.meta.env.PUBLIC_TURNSTILE_SITEKEY ?? ''}`. The Turnstile script tag is rendered only when the sitekey is non-empty. Ruling: until Plan 3 sets the key, local builds have no widget, and the function then needs `ALLOW_NO_TURNSTILE=1`, set only in local dev.
- `demo-form.ts`:
  - Validates on blur and on submit with `validateLead`, showing errors from dict `contact.form.errors` under each field (`aria-describedby`, `aria-invalid`).
  - On submit it prevents default, disables the button and sets "sending", then `fetch('/api/demo', {method:'POST', body: FormData})` with header `accept: application/json`.
    - 200: replace the form with the success panel and move focus to it.
    - 422: show the field errors.
    - 429: errorRate.
    - Any other status, or a network error: errorNetwork, with the typed values kept, the button re-enabled and the WhatsApp link shown.
  - "Send and open WhatsApp" does the same, then opens the `waLink` with a prefilled summary in the page language after a 200.
- The function answers `accept: application/json` with JSON even for form bodies (Task 6, behaviour 8 and case (k)).

- [ ] **Step 1 [builder: `tests/site/form.spec.ts`]**: Playwright tests that use `page.route('**/api/demo', …)` to fake the function:
  - empty submit shows two errors (name, phone) and makes no request;
  - Arabic-Indic phone digits are accepted;
  - a 200 shows the success panel with focus on it;
  - a 429 shows errorRate and keeps the values;
  - an aborted route shows errorNetwork, keeps the values and shows the WhatsApp link;
  - `/en/contact/?sent=1` shows the success panel without JS (use a context with `javaScriptEnabled:false`);
  - the button is disabled while the request is pending (delay the route by 1 s).
- [ ] **Step 2 [main]: run it and watch it fail** (no page). **Step 3 [builder: DemoForm.astro + demo-form.ts]**. **[builder: contact page + `pages.css` form styles]**.
- [ ] **Step 4 [main]: run it and watch it pass. Mutation-prove**, restoring after each:
  1. Remove the button disabling: the pending test fails.
  2. Clear the field values on error: the 429 test fails.
- [ ] **Step 5 [main]: commit** `feat(website): contact page and demo form with no-JS fallback`.

---

### Task 8: Site-wide checks extended to every page

**Files:**
- Modify: `tests/site/head.spec.ts`. Set `PAGES` to all 16 language pages: `/ar/`, `/en/` and each of the 7 slugs in both languages.
- Create: `tests/site/links.spec.ts`. It crawls every page from both home pages and requests every same-origin `href` and `src`.
  - Expected-missing until Plan 3, listed in an explicit array: `/jewel/`, `/og/`, `/favicon`, `/apple-touch-icon`, `/site.webmanifest`.
  - Any other non-2xx/3xx fails, with a list of the broken URLs.
  - Every `#fragment` link must match an element id on its target page.
- Modify: `tests/site/shots.spec.ts`. Cover all pages at 390 and 1440 (full + reduce), plus 768 and 1024 for Home, Retail and Contact only. This keeps the run under 30 minutes.

- [ ] **Step 1 [builder ×2]**: links.spec.ts; then head.spec + shots.spec.
- [ ] **Step 2 [main]**: build, then run head and links (detached via a `.cmd` if longer than 2 minutes), then shots (detached), then `contrast.py`. Expected: head and links pass. Contrast: every LOW row must be in the reduce tier with its cause logged as the missing Plan 3 stills; any LOW in the full tier is a real defect.
- [ ] **Step 3 [main]: mutation-prove the link check.** Change the footer FAQ href to `/en/faqs/`. links.spec must fail naming it. Restore.
- [ ] **Step 4 [main]: look at the contact sheets.** Do this for every page at 1440 and 390, both languages. Defects go to Fable, then come back as a spec delta for the builder. Commit.

---

### Task 9: Independent review + finish

- [ ] **Step 1 [main]**: dispatch `feature-dev:code-reviewer` (model opus) over `git diff 37142809..HEAD -- website/ ':!website/package-lock.json'` with the spec, this plan, `clinic-claims.md` and the ledger. Ask it to refute:
  - every product sentence is traceable;
  - the function's store-first and fail-closed guarantees;
  - the form's no-JS path;
  - RTL on every new page;
  - the a11y of the rail and the form.
- [ ] **Step 2 [main]**: one fix wave (builders), one scoped re-review, and mutation proofs for each new guard.
- [ ] **Step 3 [main]**: the ledger "Rulings I made" list. Then `git push origin feat/website` (the owner approved backups to this branch on 2026-10-18). Report to the owner in plain words with contact sheets.
