# Action Aura website v2: research and concepts

Date: 2026-10-17. Role: creative director pass. Brief in the client's words: "luxurious and charismatic functional stuff, transitions and moving stuff, the colour changes into retail's theme when you enter the retail section, the logo at its best quality, not only the 2D lines, check online, watch the colours."

## 0. Method and caveats

- Skills: all seven loaded (high-end-visual-design, design-taste-frontend, gpt-taste, apple-design, emil-design-eng, animation-vocabulary, impeccable); no failures. The impeccable launcher was not run (planning-only work).
- Brand kit read: brand guide plus the master and Retail SVGs. The construction numbers are reusable in 3D: 120-unit canvas, 45 degree apex, ellipse 36 x 8 rotated -12 degrees centred at (60, 69), node at (95.2, 61.5), facets split at x=60. The kit already defines Aura Clinic (blue trio, plus glyph) and Aura Owner (gold). No Gym trio exists; one is proposed below.
- Limits: the Chrome extension was not connected, so no live screenshots. Dribbble renders nothing without JS; it was reached through site-restricted search. JS-heavy sites were read through their Awwwards pages (scores, tags, palettes) and case studies. "Known pattern" rows were not re-verified today.
- Contrast ratios in section 3 were computed (WCAG relative luminance), not estimated.

## 1. Audience and device reality (Jordan)

- Android about 76%, iOS about 24% (StatCounter, Aug 2026): Chrome for Android and Samsung Internet are the primary targets.
- Median mobile download about 47 Mbps at end of 2025 (DataReportal citing Ookla), up from 30.5 a year earlier. Bandwidth is no longer the bottleneck; CPU/GPU on 2-4 GB mid-range phones is. So the rich path may be heavy in deferred bytes, but the lite path must be light in work: colour transitions CSS-driven, and the 3D logo with a pre-rendered twin.

## 2. References (17)

| # | Reference | What exactly is good (technique) | Fit for a Jordanian shop owner on a mid-range Android |
|---|---|---|---|
| 1 | landonorris.com (OFF+BRAND; SOTD 17 Nov 2025, 8.18; animations 8.60) | Cursor-reactive WebGL liquid overlay on the hero; strict two-colour world (#D2FF00 / #111112); GSAP | Partial. Take the two-colour discipline and the desktop-only cursor layer; lime is wrong for us; heavy |
| 2 | igloo.inc (abeto for Bureaux; Awwwards case study) | One procedural ice object per project; transitions done as material effects (frost, chromatic aberration); tested on low-end devices; three.js, GSAP | Partial. Steal "one object per chapter, transition equals material change"; the engine is too heavy for lite |
| 3 | varanigin.com/ilgin (Awwwards element) | Scroll-scrubbed 3D bottle pours into a glass; three.js | Yes as a pattern: one hero object whose state is scroll position. Needs a frame-sequence fallback |
| 4 | odysseeclinic.com.au (NIGHTJAR; SOTD 8 Oct 2026) | Navy #1E305E + ivory #F6F4F2, almost Aura's own pair; GSAP micro-transitions only; a sphere as identity node | Yes. Calm luxury without WebGL; the tone reference for Aura Clinic |
| 5 | moto-card.com (Properly Studio; SOTD 24 Sep 2026) | Dark luxury #0B0C07 / #E1E5E5; three.js + GSAP; one CTA repeated through the page | Yes for register and CTA repetition (ours is WhatsApp); the 3D card can be stills |
| 6 | santionispirits.com (Active Theory; SOTD 5 Oct 2026; Animations 9.40) | WebGL illustrated journey with sound | No (comic register, heavy). The 9.40 shows jurors reward transition quality, not quantity |
| 7 | overworldaudio.com (Rogier de Boeve; SOTD 15 Oct 2026; WPO 6.40) | three.js explorable map; #13151f / #e9ecee | No. The WPO score is the warning: 3D-first pages pay in performance |
| 8 | apple.com/airpods-pro (structure verified) | Per-feature start/end-frame image sequences, used selectively; short stacked headlines | Yes. Image sequences are the lite-path version of live 3D |
| 9 | stripe.com | Animated gradient hero shipped with a static WebP fallback (wave-fallback-desktop-1x.webp observed) | Yes. "Fallback asset first, canvas second" is our loading rule |
| 10 | linear.app | Product UI as hero visual; four product sections, one restrained rhythm | Yes for restraint; no for its "Fig 0.1" labels (template tell) |
| 11 | lusion.co | 3D storytelling studio; scroll-to-explore reel | Aspiration only; take the lighting quality bar, not the weight |
| 12 | awwwards.com/inspiration/republic-note-logo-republic-note | Tagged logo / glass / 3D: a dimensional glass brand mark as hero | Yes as the logo brief, pre-rendered on lite |
| 13 | awwwards.com/inspiration/logo-webgl-transition-with-side-scrolling-visuals-nexus (Parallax) | The logo is the transition carrier into the next content | Partial. Keep "logo carries the transition"; no horizontal hijack on phones |
| 14 | dffrntera.com (Awwwards element) | Faceted three.js crystal driven by scroll | Yes as material reference: our A is faceted |
| 15 | cuberto.com (known pattern) | Page background colour change on scroll; magnetic buttons | Partial. Colour-on-scroll is the asked-for behaviour; magnetic buttons desktop-only |
| 16 | Dribbble: dribbble.com/search/glass-logo-animation, dribbble.com/tags/spline_3d, dribbble.com/shots/26011595-Portals-hero-3D-landing-page-animation | C4D frosted-glass logo studies; Spline 3D heroes | Partial. Style reference for the glass mark; Spline runtime too heavy, render to frames |
| 17 | Baseline: shopify.com/pos, dribbble.com/tags/pos-website | "Start for free" hero, hardware photos + UI screenshots, 14-section skeleton (hardware, demo, FAQ) | Yes functionally: what a shop owner expects to find. Keep the skeleton, replace the skin; the Dribbble POS tag is the template look to avoid |

Patterns extracted, with the tier each needs:

- Colour world by scroll: CSS scroll-driven animation on `--world-*` custom properties (caniuse 87.8%; Chrome 115+, Samsung 23+, Firefox 161+, Safari 26+), IntersectionObserver step + 600 ms colour transition as fallback, 300 ms cross-fade under reduced motion. No scroll listeners. GSAP (free since 2025, ScrollTrigger included) only if a WebGL scene must be scrubbed.
- Dimensional logo in three tiers: live three.js r128 UMD (about 146 KB gz); AVIF/WebP frame sequence; one static render + CSS perspective tilt on lite and reduced-motion.
- Logo as page-to-page carrier: Astro `<ClientRouter />` with `transition:name="aura-ring"` so the ring morphs from homepage chapter into product hero (View Transitions 92.9% global; Astro falls back to `animate` or `swap`).

## 3. Colour worlds (shared by all three concepts)

Measured contrast; "AA" is 4.5:1 or better for body text.

| World | Dark field | Light surface | Accent trio | Text on dark | Text on light | Button pairs | Glyph |
|---|---|---|---|---|---|---|---|
| Action Aura (master) | #0E1729 (cards #101A30) | ivory #F6F4EF | #2F7B7B / #7FCAC5 / #8FD0D0 | ivory 16.3:1; aqua #8FD0D0 10.3:1; teal #2F7B7B only at 24 px+ (3.6:1) | navy #16233D; teal 4.5:1 is borderline, keep it for links | navy on aqua 9.0:1 | none |
| Aura Retail | warm ink #1A1512 (ivory 16.5:1) | ivory | #A06030 / #C97B3D / #F0B87A | #F0B87A 10.2:1 | #A06030 only (4.5:1). #C97B3D FAILS at 3.0:1: display 24 px+ or decoration only | navy on #F0B87A 8.8:1. Never ivory on #C97B3D (3.0:1) | square (kit) |
| Aura Clinic (kit trio, kept) | ocean #0C1B2B (ivory 15.8:1) | porcelain #EEF4F8 (the one light room) | #33699C / #3D7FB5 / #8CC7EE | #8CC7EE 9.5:1 | navy 14.1:1; #33699C 5.2:1; #3D7FB5 3.9:1 is large-text only | navy on #8CC7EE 8.6:1; ivory on #33699C 5.3:1 | plus (kit) |
| Aura Gym (proposed) | garnet ink #1A0E14 (ivory 17.1:1) | ivory | garnet #8B1E3F / crimson #C8324B / rose #F5A3A8 | #F5A3A8 9.6:1 | #8B1E3F 8.1:1; #C8324B 4.8:1 | ivory on #8B1E3F 8.1:1; navy on #F5A3A8 8.0:1 | horizontal rounded bar (one plate, one rep) |

Why garnet for Gym: red is the gym archetype (Peloton, Barry's), cooled toward wine so it neither collides with Retail's copper-orange nor drifts to pink; it sits opposite teal on the wheel, and the kit's Owner gold stays distinct. WhatsApp green #25D366 stays true (9.0:1 on every dark field, navy text on it 7.9:1) and is never world-tinted; it must be recognised instantly.

## 4. Three directions

### A. ORBIT. The ring leaves the logo and runs the site

- Idea: the aura ring is the one object always on screen. It is the logo's orbit in the hero, the scroll-progress instrument on the way down, and the carrier of the current world's colour. This fits because the brand's only kinetic element is the ring ("pierced by a tilted orbit, the aura"), and the energy node literally says "you are here".
- Hero, first 5 seconds: navy field with a fixed 3% grain. The two metal facets of the A reveal by clip-path (700 ms), the ring draws itself in (SVG stroke, 900 ms), the node ignites. Then the live mark idles (rotation plus or minus 6 degrees, 8 s yoyo). The pointer moves the light, never the object; on phones only scroll moves anything. Headline left, WhatsApp pill plus a "Call" text link, nothing else.
- Logo, dimensional: three.js extruded facets built from the SVG numbers (brushed metal, roughness 0.3, metalness 0.9, environment-lit), ring as a torus with the kit's two ring gradients as emissive, node as a small emissive sphere with a sprite glow (no bloom pass). Below 64 px the flat kit mark is used, per the brand rule.
- Colour-world transition: as you scroll, the A recedes (scale 1 to 0.6, fades) while the ring grows to the viewport edge and docks as a 2 px arc at the top right (top left in RTL). Each chapter threshold scrubs `--field`, `--accent`, `--glow` over 40 vh of travel, interpolated in OKLCH so copper never greys out; text colour snaps at the midpoint. The node rides the arc as progress. Entering a product page, `transition:name="aura-ring"` morphs the arc into that page's hero ring while the field colour morphs with it (300 ms).
- Motion signature: Ring Draw-in 900 ms cubic-bezier(.23,1,.32,1); World Scrub, scroll-linked, 40 vh, no easing; Node Settle, spring damping 1.0 response 0.4 s when a chapter completes; Facet Catch, a specular sweep across the A on hover or every 9 s, 1.2 s; Press, scale 0.97 in 120 ms on every tappable.
- Luxury details: Space Grotesk 700 display at -0.02em, Work Sans body (brand fonts); light from top-left with long hue-tinted shadows; hairlines at 8% ivory; double-bezel cards; no pure black or white.
- Functional: WhatsApp pill fixed bottom-right (bottom-left in RTL), "Book a demo" and "Call" secondary, nav under 72 px on one line. Arabic pages use IBM Plex Sans Arabic (a Latin-family sibling, so it sits with a grotesk) with Western digits; the logo is never mirrored, only the layout. Reduced motion: ring static, worlds step-fade 300 ms, no idle.
- Feasibility and weight: Astro static. Lite path (default on phones and on Save-Data, 3g or worse, deviceMemory 4 GB or less, no WebGL2): HTML 15 KB, CSS 25 KB gz, JS 8 KB, fonts about 60 KB Latin or 90 KB Arabic, static mark AVIF 40 KB, three chapter images 150 KB; roughly 300 KB first view, under 600 KB full page. Rich path adds three.js r128 (146 KB gz), 10 KB scene code and a 30 KB environment texture, loaded after `load` and cross-faded over the static render through a 2 px blur. GSAP ScrollTrigger (about 40 KB gz) only if the WebGL recede needs it; otherwise rAF reading scrollY.

### B. THREE COUNTERS. Each product is a real surface you stand at

- Idea: Retail is a copper-anodised shop counter, Clinic a porcelain reception desk, Gym a matte rubber floor; the pierced A is etched, glazed or debossed into each as a hallmark. Fits because the products are places, not feature lists, and a shop owner reads a counter instantly.
- Hero: a three-slab turntable stage; the master mark in brushed metal stands on a dark slab beside the headline. Scroll (or swipe on phone) turns the stage. Logo dimensional as an emboss (normal map), not floating glass.
- Transition: sticky stage; every 100 vh rotates it 120 degrees while material and world colour cross-fade through a 2 px blur (hides the seam between two states); the field tints to match. On product pages the slab becomes the header.
- Motion: Stage Turn (scrubbed); Material Cross-fade 500 ms + blur(2px); Hallmark Glint 1.5 s; Slab Settle, spring damping 0.8 response 0.3 s because the swipe carried momentum; Press 0.97.
- Luxury: real material renders, one directional light, contact shadows. Functional: same CTA system; lite path is three stills.
- Feasibility: pre-rendered Blender sequences, 3 x 36 frames x about 8 KB, roughly 860 KB lazy per chapter; or live three.js with about 300 KB of PBR textures. Heaviest of the three. Risk: materials rendered less than exceptionally read as stock.

### C. LAMP ROOM. The aura is literal light; each product is a lamp switching on

- Idea: the site is a dark room and the glow around the mark is the aura. Retail switches on a copper lamp, Clinic lifts the blinds (the one light room), Gym switches on a garnet lamp. Colour world as light, not paint: the field stays near navy; glow, accents and surface tints change. Honest about the product: quiet infrastructure that makes a business glow.
- Hero: the mark on navy with a slow breathing teal glow behind it (CSS radial-gradient on `--glow`, 8 s). The cursor is a second soft light revealing texture on cards (spotlight borders, desktop only). Logo via light: the static render with CSS perspective tilt and a specular sweep; the live three.js mark optional.
- Transition: scroll-driven; an entering chapter fades its lamp up (glow colour and position, 600 ms ease-out) while the previous dims; the Clinic chapter flips the room to porcelain, the single theme switch allowed, brightness eased over 500 ms so nothing jumps.
- Motion: Lamp Up 600 ms ease-out; Breathe 8 s; Spotlight Follow, spring damping 1.0 response 0.3 s; Blind Lift 500 ms; Press 0.97.
- Feasibility: cheapest. Pure CSS plus about 6 KB JS; lite and rich are nearly the same page. Risk: less "wow" on the logo unless the live mark is added.

## 5. Recommendation: ORBIT as the spine, lit the LAMP ROOM way, COUNTERS as the phone hero

Blend: A's ring-as-instrument and live faceted mark; C's light-based colour worlds (field stays dark, only Clinic lifts to porcelain; glow and accents carry the change); B's pre-rendered material close-up of the mark as the lite-path hero.

Why:

1. The ring is the only kinetic element the brand already owns. As progress instrument and colour carrier it is specific to Action Aura; no template has it.
2. Light-based worlds keep contrast safe and cheap: three dark inks plus one porcelain room, so every pair in section 3 holds, and it is CSS custom properties, not WebGL.
3. Three logo tiers give one art direction on a 2019 Android and a MacBook. The 3D mark must be built from the SVG construction numbers so it matches the flat mark exactly at 64 px, where the kit says to switch.
4. Jurors reward transition quality over quantity (Santioni 9.40 for a few superb transitions; Overworld 6.40 WPO for a 3D-first page). Four named moves done perfectly beat twenty.

Drop outright: horizontal scroll hijack, cursor-following blobs, loaders and "scroll to explore" cues, custom cursors, section-number labels, any price or testimonial claim.

Checks before build: real-device test on a mid-range Android (run the product, not the tests); visual check of the Arabic pairing (IBM Plex Sans Arabic vs Readex Pro); label Aura Clinic and Aura Gym "in development" honestly.

Owner decisions: accept the Gym garnet trio and bar glyph (not in the kit); confirm Clinic as the single light room; confirm WhatsApp stays true green, never world-tinted.
