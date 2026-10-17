# Action Aura website: design spec

Date: 2026-10-18. Status: draft for owner review. Branch: `feat/website`.

This spec records what the public website at `actionaura.me` must be, which
decisions the owner has already made, and how it gets built and checked. The
visual language is defined separately in `website/docs/design-system.md`,
written by the design director, and that document is binding. Research and
copy sources are listed in section 12.

## 1. Purpose and success

Action Aura needs a public face. Most visitors will be **owners of small
shops in Jordan**. The next largest group is **businesses with several
branches**. One section speaks to **investors and partners**.

A visitor should leave having done one of three things. The main one is
**chatting on WhatsApp**. The other two are **booking a free demo** and
**calling**.

The site succeeds if all of these hold:

- A shop owner on a mid-range Android phone understands what Aura Retail
  does in 10 seconds and can reach WhatsApp in one tap from any screen.
- It ranks for Arabic and English searches about cashier software in
  Jordan. Both languages are indexed as separate pages.
- It feels premium and alive: a 3D jewel version of the mark and colour
  worlds that change per product. It still loads fast on weak phones.
- It never claims something the products do not do.

## 2. Decisions already made by the owner (2026-10-16 to 2026-10-18)

| Topic | Decision |
|---|---|
| Frame | Company site (Action Aura) with Aura Retail in the spotlight |
| Audience order | Small shop owners, then multi-branch businesses, then investors and partners |
| Visitor actions | WhatsApp (primary), book a free demo (form), tap to call |
| Language | Separate Arabic (RTL) and English pages; the root picks by device language |
| Prices | Never shown. The site says "Pricing depends on your shop. Ask us." |
| Testimonials | None until a real customer gives one |
| Free trial | Not offered (the app runs unlicensed) |
| Look | Luxurious, charismatic, moving; nothing basic. The 3D mark lives in the background as a jewel; colour changes per product world |
| Products available | **Aura Retail** and **Aura Clinic** only |
| Products coming | Aura Gym, Aura Platforms, the WhatsApp ordering assistant and anything else; all marked "coming" |
| Build approach | Astro static site (chosen over plain files for per-language indexing and shared layout) |
| Team process | Fable thinks and specifies; cavecrew builders execute; the main session plans and verifies |

**Open, with defaults the build will use unless the owner says otherwise:**

- Hero headline: option A, "محلّك يبيع… حتى لو انقطع الإنترنت" / "Your shop
  keeps selling, even when the internet doesn't."
- The wording "free demo" is used.
- The Gym/"coming" world uses garnet (#8B1E3F / #C8324B / #F5A3A8).
- The WhatsApp disc stays true WhatsApp green (#25D366) in every world.
- Fonts: Outfit (English) and Readex Pro (Arabic), the faces the apps
  already use.

## 3. Pages

Every page exists at `/ar/…` and `/en/…` with identical Latin slugs.

| Page | Path | Notes |
|---|---|---|
| Language chooser | `/` | Indexable. Two big links, then a one-time client-side redirect by `navigator.language` that is remembered. No server redirect (see the SEO plan). |
| Home | `/ar/`, `/en/` | Hero with the jewel; three everyday benefits; the Retail world; the Clinic world; a multi-branch section; the company line; "coming next" (Gym, Platforms); closing CTA band |
| Aura Retail | `/…/aura-retail/` | "A day in the shop": selling, stock, money owed both ways, end of day, branches and devices, made for Jordan, what you need to run it |
| Aura Clinic | `/…/aura-clinic/` | Available product. Every claim must be verified against the DentaCare code (section 4) |
| About + Partners | `/…/about/` | Story, vision, values, how we work, then future products as "coming" and partnership areas |
| Book a demo / Contact | `/…/contact/` | Form, WhatsApp, call, email, office map link |
| Privacy | `/…/privacy/` | Draft text that needs a lawyer before launch |
| 404 | `/404.html` | Bilingual; links home in both languages |

Not in scope: prices, a blog, testimonials, a Gym product page, customer
logins. The old September draft in `website/` (plain HTML) is replaced; git
history keeps it.

## 4. Content and honesty rules

- **Sources of truth for claims.** For Retail and the company:
  `Desktop\Action Aura Company Profile\source\content.py`, which was checked
  against the code on 2026-10-04. For Clinic: the DentaCare repository
  (`Desktop\clinic`). A clinic claims list must be extracted and checked
  against that code before the Clinic page is written.
- The messaging brief (`messaging.md`) supplies the voice, the headline
  options and the page outlines. Its section 8 "check before live" list
  applies.
- **Never claim:**
  - JoFotara / e-invoicing compliance
  - an iPhone app
  - a live Gym or Aura Platforms
  - prices, customer counts, awards or ratings
  - "Meta partner" (allowed wording: "integrated with Meta platforms")
  - printing on Android
  - an automatic cash drawer
  - a 14-day offline grace period
- **AI-generated photos are mood pictures only.** No caption may imply a
  real customer. Every POS or phone screen in them shows the **real Aura
  app**, composited onto the blank screen, using Arabic screenshots on
  Arabic pages.
- Western digits everywhere. Phone numbers sit in `<bdi dir="ltr">`.
- No em-dashes in site copy. No exclamation marks.

## 5. Visual and motion

`website/docs/design-system.md` is binding. It covers per-world tokens,
type, spacing, components, page layouts, the jewel rules and the motion
signature. The live reference is the prototype at
`https://claude.ai/artifact/Lsgtmf8NCMyzq7sUJ5Y6ZX`; its source is in the
job folder and is copied to `website/docs/prototype/`.

The 3D mark comes in three tiers:

1. **Rich**: three.js r128, built from the brand kit's construction
   numbers.
2. **Lite**: a pre-rendered AVIF/WebP still with a CSS tilt. Used for
   `Save-Data`, `deviceMemory` ≤ 4 GB, no WebGL2, or slow connections.
3. **Reduced motion**: a static still with 300 ms fades.

The kit's flat mark is used below 64 px. The mark is never mirrored in
RTL.

## 6. Architecture

```
website/                       Astro project (static output)
  astro.config.mjs             i18n (ar, en), trailingSlash: "always", sitemap
  src/i18n/ar.ts, en.ts        every visible string, same key paths in both
  src/layouts/Base.astro       html lang/dir, hreflang, canonical, JSON-LD, fonts
  src/components/              Nav, WhatsAppPill, DemoLink, StatusPill, Chapter,
                               DayRail, ShotFrame, PhotoFrame, DemoForm, Footer, Jewel
  src/scripts/jewel.ts         three.js scene, loaded after `load` on the rich tier only
  src/scripts/worlds.ts        colour-world switching (CSS custom properties via
                               scroll-driven animation, with an IntersectionObserver fallback)
  src/pages/index.astro        the language chooser
  src/pages/[lang]/…           pages from section 3
  src/assets/shots/, photos/   real screenshots + composited mood photos (astro:assets → AVIF/WebP)
  functions/api/demo.ts        Cloudflare Pages Function for the demo form
  tools/composite_screens.py   puts real screenshots onto the blank screens in the photos
  tests/                       unit + Playwright (section 9)
  docs/design-system.md, docs/prototype/
```

- No UI framework. Astro components and vanilla TypeScript. three is
  self-hosted from npm `three@0.128.0` and dynamically imported. Fonts are
  self-hosted, subset WOFF2 via `@fontsource`.
- The page budget comes from the research:
  - Lite first view: about 300 KB.
  - Rich tier: three plus scene, about 160 KB gz, deferred.
  - LCP under 2.5 s, CLS under 0.1, INP under 200 ms (p75, mobile).

## 7. SEO, AI search, analytics

- `seo-plan.md` is binding: URLs, hreflang with x-default, canonical,
  titles and descriptions, and JSON-LD (Organization, LocalBusiness,
  SoftwareApplication with no offers or ratings, BreadcrumbList).
  `robots.txt` and the sitemap with alternates follow the plan, and the
  `*.pages.dev` preview is set to noindex.
- Add `llms.txt` and plain, factual product summaries, so AI assistants
  can cite the products (the `ai-seo` skill).
- Use Cloudflare Web Analytics, which is cookieless. Add no other trackers.

## 8. Demo form

- Fields: name and phone are required. Shop name, shop type and number of
  branches are optional.
- The client validates inline, and the server validates again.
- `POST /api/demo` (Pages Function) does five things in this order:
  1. Verifies a Cloudflare Turnstile token.
  2. Rejects the request if the honeypot field is filled.
  3. Rate-limits per IP.
  4. **Stores the lead in Cloudflare D1 first**, so no lead is lost if
     email fails.
  5. Sends a notification to `info@actionaura.me`.
- The email route is decided in the plan after a check. Cloudflare Email
  Service is preferred. The fallback is SMTP through the existing Namecheap
  Private Email mailbox (worker-mailer).
- Success and error states are bilingual. The success text promises only a
  call back.
- The privacy page names exactly the fields kept, where they are kept, and
  for how long.

## 9. Quality gates (before anything is called done)

1. **Run the real site.** Playwright screenshots of every page in AR and
   EN at 320, 390, 768, 1024 and 1440 px, rich and reduced-motion. The main
   session looks at them; agents' "done" is not evidence.
2. The contrast harness (already built for the prototype) runs on every
   world. The minimum is 4.5:1 for body text.
3. Lighthouse and `web-quality-audit` / `core-web-vitals` checks, with
   simulated mid-range Android CPU throttling, on the lite tier.
4. `accessibility` audit: keyboard path, focus, labels, landmarks. Run
   `break-ui` on the form and the status pills with worst-case data.
5. `seo-audit` plus `schema` validation. Hreflang reciprocity is checked by
   a script, not by eye.
6. Unit tests for the form validation and the Pages Function. Each guard
   is **mutation-proven**: break it, watch the test go red, restore. Do
   this for Turnstile, the honeypot, required fields and the rate limit,
   and prove both directions: a valid lead is accepted, a bad one is
   refused.
7. Claims audit: every product sentence is traced to `content.py` or the
   DentaCare code.
8. A **real-phone check** on a mid-range Android before launch.
9. An independent reviewer pass, separate from the builders.

## 10. Hosting, domain, launch

- A Cloudflare Pages project, built from `feat/website` (and later from the
  branch the owner names).
- The root domain `actionaura.me` points at Pages.
- DNS is at Namecheap today, and it carries live email (MX, SPF, DKIM,
  DMARC) and `owner.actionaura.me`. The recommended move is to Cloudflare
  DNS, copying every record first and verifying with
  `scripts/ops/check_mail_dns.py`. **Owner action, gated:** nothing changes
  DNS without the owner's explicit go-ahead at that step. The Namecheap
  ALIAS record is the no-move alternative.
- Launch checklist: use the `launch` skill. Then set up Google Search
  Console and Bing Webmaster Tools, submit the sitemap, and create a Google
  Business Profile (an owner action).

## 11. Figma

The approved page designs go into the owner's Figma ("Tareq Abu shaban's
team", Starter plan, so calls are rationed). This happens once the pages
render, through `generate_figma_design` capture plus `use_figma`. Figma is
used to show and edit; the code stays the source of truth.

## 12. Inputs (copied into `website/docs/` when building starts)

All of these live in the job folder today:

- `design-direction.md` (superseded where it conflicts with
  `design-system.md`)
- `messaging.md`
- `seo-plan.md`
- `v2/research-and-concepts.md`
- `v2/design-system.md`
- `v2/logo-worlds.html` (the prototype)
- `pages-board.html` (the first full-page layouts)
- the five mood photos (`agy-img/aura_*.jpg`)

## 13. Owner actions (cannot be done by the team)

1. Plug the phone in once so real phone-app screenshots can be captured.
2. Supply real photos when available, starting with the POS machine.
3. Have a native Jordanian reader check the Arabic text once.
4. Arrange a lawyer review of the privacy text.
5. Approve the DNS move when the site is ready.
6. Create the Google Business Profile.

## 14. Risks

| Risk | Mitigation |
|---|---|
| The 3D tier is slow on weak phones | Three tiers with capability detection; lite is the default on phones until proven otherwise on a real device |
| Arabic sounds machine-written | Native reader check (owner action 3) |
| Clinic claims drift from the DentaCare code | Claims list extracted from code, then checked |
| Starter-plan Figma limits | Figma used only for approved designs |
| A DNS move breaks email | Copy every record, verify with the script before and after, owner present |
