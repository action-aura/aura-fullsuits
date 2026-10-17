# SEO PLAN - actionaura.me (Astro static, Cloudflare Pages)
(Written by the seo-specialist agent 2026-10-17; saved verbatim by the main session because the agent had no write tool.)

Evidence legend: [V] = seen in live search results/competitor pages (proves people target the term, NOT its volume); [G] = educated guess. No keyword-volume tool; confirm in Search Console after 4-6 weeks.

## 1. URLs, hreflang, root language
- /ar/ , /en/ ; pages: /ar/ , /ar/aura-retail/ , /ar/about/ , /ar/contact/ , /ar/privacy/ and same under /en/. Trailing slash everywhere (Astro trailingSlash:"always").
- Slugs Latin and identical in both languages. Arabic goes in titles/H1, not URLs (percent-encoding breaks WhatsApp sharing).
- Canonical: self-canonical, absolute https, no www (www -> apex redirect at Cloudflare). Never canonicalise ar to en.
- hreflang on every page (head AND sitemap): ar, en, x-default; reciprocal. x-default for pages = /en/ URL; for root = root.
- Root URL: NO server 301/302 on language. Root "/" = real indexable lightweight language-chooser (logo, two big links, self canonical, x-default) with inline script that for humans redirects once via navigator.language (ar* -> /ar/, else /en/) and remembers choice in localStorage. Visible AR/EN switch on every page linking to the twin URL.
- html lang="ar" dir="rtl" on Arabic pages; lang="en" on English.

## 2. Keywords
Arabic:
- /ar/aura-retail/: برنامج كاشير [V]; برنامج نقاط بيع [V]; نظام نقاط البيع للمحلات [V]; برنامج محاسبة للمحلات [V]; برنامج كاشير يعمل بدون انترنت [G]; برنامج إدارة مخزون [V]; برنامج ديون الزبائن والموردين [G]; برنامج كاشير متعدد الفروع [G]; برنامج كاشير باركود [G]; برنامج مبيعات ومشتريات [V]; برنامج كاشير عمان / الأردن [V]; كاشير للموبايل [G].
- Home (/ar/): أكشن أورا [G]; برنامج إدارة محلات [G]; نظام كاشير للمحلات الصغيرة [G].
- Contact: تجربة برنامج كاشير / احجز عرض تجريبي [G].
English:
- /en/aura-retail/: POS software Jordan [V]; point of sale system Amman [V]; cash register software Jordan [V]; retail management software Jordan [G]; offline POS software [G]; multi-branch POS [G]; POS with barcode scanner [G]; inventory and customer debt tracking software [G]; POS with phone app for owners [G].
- Home/About: Action Aura Jordan [G].
Do not target e-invoicing/JoFotara terms.

## 3. Titles (<=60) and descriptions (<=155)
- Home AR: أكشن أورا | برامج إدارة المحلات في الأردن / أكشن أورا شركة برمجيات في عمّان. اكتشف Aura Retail، برنامج كاشير وإدارة محلات يعمل بدون انترنت ويربط أجهزتك. احجز عرضاً تجريبياً.
- Home EN: Action Aura | Retail Software from Amman, Jordan / Action Aura builds Aura Retail, POS and shop-management software that works offline and syncs your devices. Book a demo.
- Aura Retail AR: Aura Retail | برنامج كاشير وإدارة محلات / كاشير على الكمبيوتر وتطبيق للمدير على الموبايل، يعمل بدون انترنت، مخزون، ديون الزبائن والموردين، تقارير، فروع متعددة وقارئ باركود.
- Aura Retail EN: Aura Retail | POS and Shop Management Software / Desktop till plus manager phone app. Works offline, syncs devices, tracks stock, customer and supplier debts, reports, multiple branches, barcode scanning.
- About AR: من نحن والشركاء | أكشن أورا / تعرّف على أكشن أورا في عمّان، وكيف نعمل مع أصحاب المحلات والشركاء والمستثمرين.
- About EN: About and Partners | Action Aura / Meet Action Aura in Amman and learn how we work with shop owners, partners and investors.
- Contact AR: احجز عرضاً تجريبياً | أكشن أورا / احجز عرضاً تجريبياً لـ Aura Retail أو تواصل معنا في عمّان على +962 78 006 9485.
- Contact EN: Book a Demo | Action Aura / Book a demo of Aura Retail or contact our Amman team on +962 78 006 9485.
- Privacy: "سياسة الخصوصية | أكشن أورا" / "Privacy Policy | Action Aura". 404: noindex, real 404 status, bilingual links.
No "best", "#1", price or e-invoicing claims.

## 4. Structured data (JSON-LD per page, generated from Astro frontmatter)
- Organization (Home): name "Action Aura", alternateName "أكشن أورا", url, logo PNG >=112px, telephone +962780069485, sameAs only real profiles, contactPoint.
- LocalBusiness/ProfessionalService (Contact + About): Al-Rayyan Complex, Amman, JO; telephone; openingHours only if real; must match Google Business Profile NAP exactly.
- SoftwareApplication (Aura Retail): applicationCategory BusinessApplication, operatingSystem "Windows, Android" (confirm), inLanguage ["ar","en"], publisher. OMIT offers/aggregateRating/review (no rich result, but valid). Never price 0.
- BreadcrumbList on non-home pages, localized.
- Validate: Rich Results Test + schema.org validator.

## 5. Local SEO and webmaster
- Google Business Profile: "Software company", Amman address, phone, bilingual description; verify; no fake reviews.
- Search Console Domain property via Cloudflare DNS TXT; submit sitemap. Bing Webmaster Tools import from GSC.
- @astrojs/sitemap with i18n alternates; exclude 404; include root chooser.
- robots.txt: Allow all + Sitemap line; don't block /_astro/. *.pages.dev preview = noindex/redirect.

## 6. Core Web Vitals and Arabic fonts
- p75 mobile: LCP <2.0s (cap 2.5), INP <200ms, CLS <0.1; page <500KB; near-zero JS (island only for demo form).
- Self-host WOFF2 fonts, subset Arabic+Latin, max 2 families, variable or 2 weights, font-display swap, preload primary Arabic regular only, size-adjust fallback (Tahoma/Segoe UI). Logical CSS properties for RTL. Hero AVIF/WebP with width/height + fetchpriority high; rest lazy. Brotli + immutable cache on hashed assets.

## 7. Competitors
1. Mahaal (Jordan, Arabic-first): vertical landing pages (restaurants, supermarkets, cafes, pharmacies). Lesson: vertical pages later (grocery, clothing, mobile shops).
2. NARD (Amman, nardpos): trust signals, "15+ years", LinkedIn presence. Lesson: About+Partners page.
3. POSRocket (Amman): plain feature pages for inventory, staff, reports.
Inyad and Qoyod (KSA) own generic "برنامج كاشير" with long guides. Our gap/differentiator: Jordan-specific + works offline + customer/supplier debts -> feature in H1/H2s. mozon-tech.com ranks English "Point of Sale in Jordan" guide -> future blog.

Sources: inyad.com top-5 Jordan list; mozon-tech.com/en/blog/point-of-sale-in-jordan/; qoyod.com برنامج-الكاشير; jo.opensooq.com POS listings; linkedin.com/company/nardpos.
