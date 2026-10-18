# Action Aura website: visual design system

For the real site: Astro (static), vanilla JS, three.js r128 for the jewel, fonts Outfit + Readex Pro. Written in the language of the `logo-worlds.html` prototype (v6 after round 4) so builders can lift values directly. Builders cannot see images: every value here is exact.

## 1. Principles

1. **The mark is a directed jewel.** One complete 3D pierced-A per page, fully visible, in the fixed background layer; it glides to one station per section and carries the colour world. Never cropped, never a texture, never duplicated.
2. **Worlds are light, not paint.** Three dark rooms (master navy, Retail warm ink, Gym garnet ink) and one light room (Clinic porcelain). Field, glow and accent change; type, spacing and components do not.
3. **Honest claims only.** Available now: Aura Retail, Aura Clinic. Everything else: "Coming". No prices, no testimonials, no counters unless real.
4. **Bilingual by design.** EN and AR are peers. Arabic is never letter-spaced or uppercased, never faux-bold; multi-line Arabic aligns to its start edge; the logo is never mirrored.
5. **Restraint.** No eyebrows, no section numbers, no scroll cues, no decorative dots, no em-dashes, no fake UI, one marquee or rail per page at most.

## 2. Tokens per world

Field is a vertical 3-stop gradient; glow is a radial `34% 42%` ellipse at the jewel's station; scrim is a per-world vignette ink. "Text" is `--ink`, "muted" is `--ink-muted`, hairline is `--hair`.

| Token | Master (home, about, contact, privacy, 404) | Aura Retail | Aura Clinic (light room) | Aura Gym and every "Coming" world |
|---|---|---|---|---|
| Field gradient | `#0B1322 0%, #0E1729 50%, #15213A 100%` | `#15100D, #1C1511 48%, #2C1C12` | `#F8FBFD, #EEF4F8 50%, #D8E5EF` | `#110709, #1A0C12 50%, #2C1120` |
| Glow (rgba at jewel) | `47,123,123,.5` | `201,123,61,.52` | `140,199,238,.55` | `200,50,75,.5` |
| Scrim ink (rgb) | `14,23,41` | `26,21,18` | `238,244,248` | `26,14,20` |
| Accent trio dark / mid / light | `#2F7B7B / #7FCAC5 / #8FD0D0` | `#A06030 / #C97B3D / #F0B87A` | `#33699C / #3D7FB5 / #8CC7EE` | `#8B1E3F / #C8324B / #F5A3A8` |
| `--accent` (headline highlight, pill dot, focus ring) | `#8FD0D0` | `#F0B87A` | `#3D7FB5` (3.9:1, display sizes only) | `#F5A3A8` |
| `--ink` | `#F6F4EF` | `#F6F4EF` | `#16233D` | `#F6F4EF` |
| `--ink-muted` | `rgba(246,244,239,.72)` (phone `.84`) | same | `#39475F` (phone `#2F3C52`) | same as master |
| `--hair` | `rgba(246,244,239,.14)` | same | `rgba(22,35,61,.16)` | same as master |
| Primary pill | bg `--ink`, text `--field` | same | bg `#16233D`, text `#EEF4F8` | same as master |
| Link / text accent on body | `#8FD0D0` (10.3:1) | `#F0B87A` (10.2:1) | `#33699C` (5.2:1) | `#F5A3A8` (9.6:1) |
| 3D facet L / facet R | `#4FA09B / #22304A` | `#C97B3D / #22304A` | `#3D7FB5 / #16233D` | `#8B1E3F / #22304A` |
| 3D ring / ring emissive / node | `#6FC0BC / #2F7B7B / #EDFDFC` | `#CF8244 / #6B3410 / #FBE3C4` | `#2F6AA0 / #1A446C / #F4FAFF` | `#9E2342 / #4A0C1E / #FFF1F2` |

Rules: WhatsApp green `#25D366` is never world-tinted. Never ivory on `#C97B3D` (3.0:1). Body text on any field must measure >= 4.5:1 against the rendered backdrop, not the flat field. Coming products use the Gym tokens unless the brand kit defines their trio (Owner gold `#96761F / #C9A227 / #E8CD7A` exists; use it only for the Owner console).

## 3. Type

Fonts: Outfit 400/500/600/700 (Latin), Readex Pro 400/500/600 (Arabic). Self-host with `font-display: swap`; preload only Outfit 700 and Readex Pro 600.

| Role | Latin | Arabic | Line-height | Tracking |
|---|---|---|---|---|
| Hero h1 | Outfit 700 `clamp(2.25rem, 1.3rem + 3.4vw, 4.1rem)` | Readex Pro 600 `clamp(1.35rem, 1rem + 1.4vw, 2.1rem)` | 1.0 / 1.4 | -.035em / 0 |
| Section h2 | Outfit 700 `clamp(1.9rem, 1.2rem + 2.4vw, 3.4rem)` | Readex Pro 600 `clamp(1.2rem, .95rem + 1vw, 1.8rem)` | 1.0 / 1.4 | -.035em / 0 |
| Block heading (details chapter on product pages; h2 on Read pages) | Outfit 700 `clamp(1.4rem, 1.1rem + 1vw, 2rem)` | Readex Pro 600 `1.3rem` | 1.15 / 1.5 | -.035em / 0 |
| h3 (card, feature) | Outfit 600 `1.25rem` | Readex Pro 600 `1.2rem` | 1.2 / 1.5 | -.01em / 0 |
| Lead | Outfit 400 `clamp(1.0625rem, 1rem + .3vw, 1.2rem)` | Readex Pro 400 `lead x 1.04` | 1.55 / 1.85 | 0 |
| Body | Outfit 400 `1rem` | Readex Pro 400 `1.0625rem` (16px minimum on phones) | 1.6 / 1.9 | 0 |
| UI label / pill | Outfit 500 `11px` uppercase `.14em` | Readex Pro 500 `12px`, sentence case, no tracking | 1 | see left |
| Nav | Outfit 500 `13px` | Readex Pro 500 `14px` | 1 | .01em / 0 |
| Numbers | `font-variant-numeric: tabular-nums`; Western digits 0-9 in both languages for prices and phone numbers | | | |

Rules: `text-wrap: balance` on h1/h2/h3, `text-wrap: pretty` on leads and captions. Headlines max 2 lines on desktop (h1 3 lines allowed in the hero). Emphasis within a headline = the world accent colour on one word (`.hl`), never a second family. On bilingual pages the Arabic title sits directly under the Latin title at the Latin size ratio above, same ink; the Arabic lead sits under the Latin lead, `text-align: start`. On Arabic-only pages everything is Readex Pro and `dir="rtl"`. A chapter made of several short blocks (the product details chapter: hardware, Jordan or privacy, what you need to run it) keeps its `h2` elements but sets them at the block-heading scale; the body under a block heading is `--ink-muted` at line-height 1.7 (Arabic 1.9), 12px below the heading, and blocks sit `clamp(40px, 6vh, 64px)` apart, so each reads as heading + body rather than a run of section titles.

## 4. Spacing, rhythm, surfaces

- Gutter `--gutter: clamp(16px, 4.5vw, 64px)`. Content max 1400px. Copy column max `36rem`, lead max `31rem`, prose (Read pages) max `65ch`.
- Section padding `clamp(88px, 14vh, 150px)` top and bottom; hero `min-height: 100svh`; chapter `min-height: 80vh`; the close `70vh`. Phones: text pinned to the top (`align-items: start; padding-top: min(16vh, 140px)`).
- Grid: desktop sections are `grid-template-columns: minmax(0,46%) 1fr`; the copy takes the column opposite the jewel. Phones: single column, full width.
- Radii: pills `999px`; frames outer `20px`, inner `14px` with `6px` padding (concentric: outer = inner + padding); inputs `12px`; nothing else is rounded.
- Hairlines: `1px solid var(--hair)`. Never a solid grey border.
- Shadows (always tinted, never pure black on light): dark rooms `inset 0 1px 0 rgba(255,255,255,.55), 0 14px 34px -14px rgba(0,0,0,.55)`; light room `inset 0 1px 0 rgba(255,255,255,.12), 0 14px 34px -14px rgba(22,35,61,.45)`. Frames add `0 30px 60px -30px <scrim ink at .6>`.
- Grain: fixed, pointer-events none, `opacity .05` overlay on dark rooms, `.04 multiply` on the light room.
- Scrims: desktop a whisper vignette `radial-gradient(ellipse 60% 120% at <copy side 12% or 88%> 50%, ink .55, .45 40%, .18 70%, 0)`; phones a vertical profile `.86 0%, .82 62%, .5 74%, .16 84%` (light room `.9/.86/.54/.18`). Images inside frames get `outline: 1px solid rgba(0,0,0,.1); outline-offset: -1px` (light) or `rgba(255,255,255,.1)` (dark).

## 5. The jewel

Geometry from the kit's 120-unit construction (apex 60,12.6; ring 36x8 at 60,69 rotated -12 degrees; node 95.2,61.5), extruded .7 with bevel .12/.09; ring torus r 3.6, tube .26; node sphere .38 with a tight additive halo (1.9, .6, not depth-tested), a wide halo (5.2, .22), and in the light room a white corona (1.5, .55) plus a sky halo (4.4, .4). Camera FOV 22, elevation 6 degrees, ACES tone mapping, exposure 1.0 (.92 in the light room). Materials: facets metalness 1, roughness .2/.24, clearcoat .35 (ceramic in the light room: metalness .1, clearcoat 1); ring metalness 1, roughness .2, emissive .45. Vertex brightness ramp .55 at the feet to 1.05 at the apex. Neutral studio dome (`#9A9EA6 / #2E3238 / #07080A`) with a wide softbox above, a rim card behind right, a small hot glint card.

**Placement rules.** Scale 1 = 58% of the viewport height on desktop; the mark keeps the kit's 25% clear space from the header, the viewport edge and the bottom, and never stands behind text at a station; where a section's content spans the full width (a rail, a grid) it dissolves instead (below).

| Page type | Desktop stations (centre across / down, scale) | Phone |
|---|---|---|
| Home | hero R 73%/54% 1.0; company R 66%/52% 1.14; Retail L 27% .9 (copy flipped right); Clinic R .9; Gym L .9 (flipped); close R 73%/58% .76 | centred, apex at mid-height, scale 1; close .9 |
| Product page (Retail, Clinic) | one station per section, five in all: hero R 73%/54% 1.0 in the product's world; day rail R 75%/50% .7 dissolved (alpha 0); features grid R 75%/50% .7 dissolved (alpha 0); details R 75%/50% .7; demo CTA R 73%/56% .9 | apex at mid-height on the hero only; hidden (opacity 0) past the hero, lite still in the CTA block |
| About + Partners | hero: centred, 72%/50%, scale 1.2, copy BELOW it (the only centred-copy hero); then R .8 | centred, apex 40% |
| Book a demo / Contact | R 73%/54% .9 beside the form | lite still above the form, 40% width |
| Privacy (Read) | lite still, top right, 28% height | none |
| 404 | centred, scale 1.3, copy below | centred, scale 1 |

RTL pages mirror every station horizontally (R becomes 27%, L becomes 73%); the mark's geometry is never mirrored.

**Dissolve.** A station may carry `alpha` (0 to 1; absent means 1). Where a section's content spans the full width (the product day rail, the features grid) no station inside the clear-space band clears it, and cards must not go opaque to hide it (the ring would show in the 20px gutters), so the mark dissolves: alpha 0 with the scale held, while the world glow stays lit at the station. The dissolve runs inside the middle 30% of the scroll gap between two section centres (hold to 40%, fade to 70%, gone after), so the mark is never half-faded behind a card at rest; it condenses back the same way for the next copy section. At rest at a dissolved station the mark has no rendered pixels (`.jewel` opacity 0); that is the check, since a bounding box cannot be measured for an absent mark. Phones already hide the mark after the product hero; nothing changes there.

**Lite path** (any of: `prefers-reduced-motion: reduce`, no WebGL2, `navigator.deviceMemory <= 4`, `Save-Data`, any viewport < 900px until the real-phone check proves the live scene smooth on a mid-range Android (spec section 14; Lighthouse mobile measured 18-25 s blocking time on the rich tier, 2026-10-18)): a pre-rendered still per world (WebP, 1404x1296, transparent, 4 files, 120 KB or less each: measured 51 / 51 / 104 / 53 KB for master / retail / clinic / gym), rendered from the live scene by `tools/render-stills.mjs` and cross-faded between worlds over 1.1s (the `.jewel-still` opacity transition). Loading order everywhere: still first, three.js after `load`, the canvas fades in over 1.1s while the still fades out (it stays in the DOM at opacity 0). No pointer tilt and no blur on this path.

**Still frame.** Each still is the hero rest pose: station 73%/54%, scale 1, scroll 0, mix 1.0 for its world, idle bob/sway and node pulse at zero, key light at rest, captured from the canvas at 1440x900 inside a 13:12 frame centred on the station point: 702x648 CSS px, 72/58 of the mark's nominal height, at device scale 2. The frame holds the nominal 7.66x9.14-unit box plus the ground shadow (to 5.53 units below the centre) and the bead's halo (to 5.77 units on the node side in the Clinic room); the geometry is identical in every world, so one frame serves all four. The bead's additive halo tail is dimmer in a still than live by physics (a straight-alpha image cannot carry an additive glow); the bead, ring and facets are exact.

**Placement.** Desktop: `.jewel-still` is `72vh * --js` tall, `width: auto`, centred at `--jx/--jy` (the `translate(-50%, -50%)` centring is why the frame is symmetric about the station point). Phones: `152.4vw * --js` wide (702 / 437.5 mark widths times the 95vw mark), top edge raised 9.72% of its height so the nominal apex sits at mid-height as scene.js places it. Acceptance: at 1440x900 on `/en/`, the pixel box of the still-tier mark and of the live mark agree within 16px on every edge (left, top, bottom at diff threshold 14/255; right, the bead and ring, at 96/255), `tests/site/behaviour.spec.ts`. Known drifts outside the test: the live mark rises `0.5 * scroll progress` units and yaws toward the copy column at each station, while a still carries the hero yaw everywhere, including RTL; visible only during the 1.1s load cross-fade.

## 6. Motion signature

| Move | What | Duration / easing |
|---|---|---|
| Arrival | canvas fades in, mark rises .8 units into its station | 1.1s fade, 1.8s rise, cubic-out |
| Reveal | copy children fade up 18px from a 8px blur, 70ms stagger (no blur on phones) | .9s `cubic-bezier(.23,1,.32,1)` |
| Glide | jewel moves and rescales between stations | scroll-linked smoothstep across the section gap, then loop inertia (k .085/frame) |
| Dissolve | mark opacity to 0 at a full-width section, back for the next copy section; the glow stays | scroll-linked inside the middle 30% of the gap between section centres (hold to 40%, fade to 70%), smoothstep, plus the .4s `--ease-out` opacity ease |
| World Blend | field, glow, scrim crossfade; text colour eases | scroll-linked; colour .6s cubic-out |
| Facet Catch | key light swings -5 to 0 and back | every 9s, 1.4s smoothstep |
| Idle | bob .04, sway +-3.4 degrees | 7s / 14s sine |
| Node Pulse | tight halo .54 +- .08 | 2.2 rad/s |
| Press | any tappable scales .97 | 160ms cubic-out |
| Hover | pill lifts 1px; link underline to ink; arrow +3px | 160-250ms cubic-out, `(hover:hover) and (pointer:fine)` only |
| Menu Sheet (phone) | full-screen material, links mask-reveal 12px with 50ms stagger | 320ms `cubic-bezier(.32,.72,0,1)` |
| Rail Snap | day-rail scroll-snap, mandatory, proximity on desktop | native |

Tokens: `--ease-out: cubic-bezier(.23,1,.32,1)`, `--ease-io: cubic-bezier(.77,0,.175,1)`, `--ease-drawer: cubic-bezier(.32,.72,0,1)`. Reduced motion: Reveal becomes a .3s opacity fade, Glide and Idle stop (stills at stations), Facet Catch and Node Pulse off, World Blend becomes a 300ms step-fade, Dissolve becomes a step at the midpoint between centres (the station snaps and the .4s ease is removed). Never animate layout properties.

## 7. Components

**Nav.** Fixed, 64px (72px desktop), transparent over the field, no blur. Left: 22px flat mark in `currentColor` + "Action Aura" Outfit 600 13px. Right: Aura Retail, Aura Clinic, Aura Gym (Coming), About, language switch "AR" / "EN", and "Book a demo" as a small outline pill (hairline, 10px 16px). Links are `--ink-muted`; the current page or chapter is `--ink` (driven by `data-chapter` on `<html>`, set from the section nearest the viewport centre). On product pages the whole nav takes that product's world tokens. Phones: brand + language + a hamburger that morphs to an X; the sheet lists the same links at h2 size.

**WhatsApp pill (primary CTA).** `display:inline-flex; gap 12px; padding 8px 22px 8px 8px; radius 999px; bg --ink; text --field; 15px/500`; a 36px disc in `#25D366` with the white glyph at 20px, always on the inline-start side. Label "Chat on WhatsApp" / "راسلنا على واتساب". Link `https://wa.me/962780069485` with a page-specific `?text=` prefill. Shadow per section 4; press .97.

**Demo link / pill (secondary CTA).** Desktop: text link `--ink` 15px/500 with a hairline underline and a 16px arrow (mirrors in RTL); hover underline to ink, arrow +3px. Phones: translucent pill `padding 10px 16px; hairline border; bg color-mix(in srgb, var(--field) 60%, transparent)`. One label per intent across the site: "Book a free demo" / "احجز عرضًا مجانيًا".

**Status pills.** `inline-flex; gap 8px; padding 6px 11px 6px 9px; radius 999px; hairline; 11px/500 uppercase .14em` for Latin, 12px Readex Pro 500 sentence case for Arabic, both labels inside one pill. Available: `--ink` text, filled 6px `--accent` dot. Coming: `--ink-muted` text, hollow dot (`box-shadow: inset 0 0 0 1.5px currentColor`). Exactly two labels exist: "Available now / متوفّر الآن" and "Coming / قريبًا".

**Chapter block.** Status pill (products only), h2 with the product word in `--accent`, Arabic title, lead, Arabic lead, optional single link ("See Aura Retail" / "اعرف أورا ريتيل"). Lives in the copy column opposite the jewel. No cards, no eyebrows.

**Day-in-the-shop rail.** One horizontal `scroll-snap-type: x mandatory` rail per product page, 5 or 6 moments in order of a real day (open the till, scan and sell, the internet drops, the phone mirrors the counter, end-of-day, the books). Each moment: a screenshot frame 320px wide (phone) / 420px (desktop), then an h3 and one 20-word line below the frame, never over it. Moments describe shipped behaviour only. Keyboard: arrow keys move one card; focus ring `2px --accent`, offset 4px.

**Screenshot frame.** Double bezel: outer `padding 6px; radius 20px; bg rgba(255,255,255,.05) (light room rgba(22,35,61,.05)); hairline`; inner `radius 14px; overflow hidden; image outline`. Real screenshots only, 16:10 desktop or 9:19.5 phone; Arabic-language screenshots on Arabic pages, English on English pages, same features. Alt text names the screen ("Aura Retail till, sale in progress"). No fake device chrome, no browser bars.

**Photo frame (the five mood photos).** For the five Nano Banana photographs of POS hardware on real counters with the real Aura screen composited onto the blank displays: same double bezel as the screenshot frame, 3:2 or 4:5, `object-fit: cover`. Caption below the frame in `--ink-muted` 13px, functional and honest: "Illustration: Aura Retail on a shop counter" / "صورة توضيحية: أورا ريتيل على كاونتر المحل". No pills or text over the photo. Max two photos per screen; never a grid of five.

**Demo form.** Fields: Name (required), Phone (required, `type=tel`, `inputmode=tel`, Western digits, placeholder `07X XXX XXXX`), Business type (optional select: Shop or showroom / Clinic / Other), City (optional), Message (optional, 3 rows). Labels above inputs (`13px/500 --ink`), helper text below in `--ink-muted`, errors below in `--accent` of the master world plus an icon, validated on blur and on submit. Inputs `padding 14px 16px; radius 12px; bg color-mix(in srgb, var(--ink) 6%, transparent); hairline; focus ring 2px --accent`. Submit = the WhatsApp pill labelled "Send and open WhatsApp" that posts the form and opens `wa.me` with the prefilled text; a plain "Send" button exists for people without WhatsApp. Honeypot field, no CAPTCHA, rate limit server-side. Success replaces the form inline: "Thanks, we'll call you within one working day" (only if true; otherwise "we'll call you back").

**Footer.** Hairline above. Desktop three columns: brand (flat mark, "Action Aura", tagline "Go Bigger · Go Smarter" with the dot in `--accent`), Products (Aura Retail, Aura Clinic, Aura Gym with a Coming pill), Company (About, Partners, Book a demo, Privacy). Bottom row 12px `--ink-muted` with `.06em`: "Action Aura · Amman, Jordan", "+962 78 006 9485", language switch. No version strings, no social icons unless the accounts exist.

## 8. Pages (EN and AR; AR is the RTL mirror with Readex Pro throughout; the logo is never mirrored)

**Home.** Master world. Nav. Hero: copy left (h1 "Your shop keeps selling, even when the internet doesn't." + Arabic title + lead + Arabic lead + WhatsApp pill + demo link), jewel R 58%. Company chapter ("Action Aura", jewel R 66%). Retail chapter, flipped, Available now, jewel L, world copper. Clinic chapter, Available now, jewel R, light room. Gym chapter, flipped, Coming, jewel L, garnet. Close "Twenty minutes, at your counter." with both CTAs and the footer line. Six sections, no cards.

**Aura Retail.** Retail world from the first pixel. Hero: pill Available now, h1 "The till that keeps selling offline.", lead, CTAs, jewel R. Day-in-the-shop rail (6 moments). Features: a bento of exactly six cells with two cells carrying screenshots (offline till; barcode scanning; stock and purchasing; receivables and payables; receipts in Arabic and English; phone mirrors the counter). Two photo frames side by side with captions. "What works with it", "Ready for Jordan" and "What you need to run it" as one details chapter of three block headings (section 3), plain text, only hardware actually supported (barcode scanners, receipt printers; no cash-drawer claim). Demo CTA block (copy + both CTAs, jewel .9). Footer.

**Aura Clinic.** Clinic world (light room) from the first pixel; `--ink` navy. Hero: Available now, h1 "Appointments, records and billing, in one calm system.", CTAs, cobalt jewel R. Rail: a day in the clinic (5 moments). Features bento of five (appointments; patient records; treatment plans; billing; Arabic and English). Screenshots from the clinic product. Details chapter of three blocks (hardware, privacy, what you need to run it). Demo CTA. Footer.

**About + Partners.** Master world. Hero: jewel centred at 72%/50% scale 1.2, copy below: "One mark, one way of working." + the mark explained in two sentences (the pierced A, the orbit, the node). Story section (plain prose, 65ch). Products: three chapter blocks in a row on desktop, each with its status pill (Retail and Clinic Available now, Gym Coming), plus one line "More Aura products are coming" with a Coming pill, no names that are not decided. Partners: logo-only wall of real partner marks; if there are none yet, the section is omitted, never faked. Footer.

**Book a demo / Contact.** Master world. Two columns: form left, right column with the WhatsApp pill, phone number, office line "Al-Rayyan Complex, Amman", hours, jewel R .9 behind the right column's lower half. Phones: form first, contact block second.

**Privacy.** Read mode: prose at 65ch, h2/h3 rhythm, no rail, no bento; lite still top right 28% height on desktop, none on phones.

**404.** Master world. Jewel centred scale 1.3, h1 "This page isn't on the shelf." / "هذه الصفحة ليست على الرف.", one link "Back to the start". No search box.

## 9. Do / Don't

Do: keep one jewel per page at a station; keep the copy opposite it; let the mark dissolve for a full-width section (rail, grid) and bring it back for the next copy section; measure body contrast on the rendered backdrop; use the two status labels only; ship real screenshots in the page's language; caption composited photos as illustrations; honour reduced motion with stills; use logical CSS properties everywhere; keep WhatsApp green true; use Western digits for numbers in Arabic; one label per CTA intent.

Don't: crop the mark, mirror the mark, tint the mark's ring to anything but its world; put text over the jewel at a station; make cards opaque to hide the mark (the ring shows in the gutters); add eyebrows, section numbers, scroll cues, decorative dots, em-dashes, version strings, locale/time strips; use Inter, Fraunces, Instrument Serif or any serif; letter-space or uppercase Arabic; build fake UI from divs; show a cash-drawer, e-invoicing, or notification feature as available; add a third CTA intent; use pure black or pure white; animate width, height, top or left; load any third-party script other than three.js r128 from cdnjs and the self-hosted fonts.
