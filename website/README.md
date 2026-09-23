# Aura Retail — marketing site

A static, bilingual (Arabic-first / English) marketing site for Aura Retail.
Plain HTML/CSS/JS. No build step, no framework, no `npm install` — this
matches the rest of this repo's own rule (see the root `CLAUDE.md`): a
marketing site that needs a toolchain to edit is a marketing site that rots.

## What's here

```
website/
  index.html       Home — hero, proof bar, feature pillars, three deep
                    dives (offline / accounting / sync), e-invoicing
                    honesty callout, full feature grid, requirements,
                    steps, pricing teaser
  features.html     The same deep dives + the full feature list, grouped
                    by "selling & hardware / growing beyond one till /
                    money owed and owed to you" — the page a shopkeeper
                    lands on directly, without seeing the homepage first
  pricing.html      The licence card, the "more branches" card, and the
                    add-on price table
  contact.html      A form that builds a mailto: link — see "The contact
                    form" below
  css/
    tokens.css       Design tokens (colour, type scale, spacing, shape)
    style.css        Everything else — layout, components, responsive
  js/
    content.js       ALL bilingual copy, plus the one PRICING object and
                      the one CONTACT object (email/phone placeholders —
                      see "The contact placeholders" below)
    i18n.js          The language engine (applies content.js, toggles
                      dir/lang, persists the choice, fires
                      `aura:langchange` when it does)
    main.js          Mobile nav open/close, populating every
                      data-contact-link element from CONTACT, and the
                      contact form's mailto builder
  assets/
    icons.svg        Hand-built icon sprite (see "Icons" below — this file
                      MUST stay valid XML, not just valid HTML)
    brand/            Copied from products/retail/frontend/brand/ — do not
                      edit these here; re-copy from that directory if the
                      brand kit changes
```

## Editing copy

Every visible string lives in `js/content.js`, split into `en` and `ar`
objects with the same key paths. An element that shows that string carries
`data-i18n="the.key.path"` (array items are addressed by index, e.g.
`proof.0.title`). To change what a page says:

1. Find the string in `js/content.js` (both `en` and `ar` — always edit
   both, or you'll ship an English sentence to an Arabic reader or vice
   versa).
2. Edit the value. Nothing else needs to change — the HTML already points
   at the key.
3. If you're ADDING a new piece of copy (not editing existing copy), add
   the key to BOTH the `en` and `ar` objects in `content.js`, then add
   `data-i18n="that.key"` to the HTML element. Open the page with DevTools
   open first — a missing key logs `console.warn('[aura-i18n] missing
   "..." for lang "..."')` rather than failing silently, so it's easy to
   catch after an edit.

**The Arabic is not a translation of the English** — it was written as its
own copy, using the SAME terminology the product itself uses (فرع/branch,
عرض سعر/quotation, ميزان المراجعة/trial balance, etc., taken from
`products/retail/frontend/locales/ar.json` so this site reads like the
product a shopkeeper will actually use). If you add a new English line,
write a real Arabic sentence for it — don't run it through a translator
and paste the output.

## Where the pricing numbers live

**Exactly one place**: the `PRICING` object near the top of
`js/content.js`:

```js
var PRICING = {
  currency_en: "JD",
  currency_ar: "د.أ",
  base_price: "250",
  base_devices: "2",
  extra_device_price: "50",
  extra_branch_price: "250"
};
```

`pricing.html` reads these into every element carrying `data-price="<key>"`
(there's a small inline script at the bottom of that file that does this,
re-running on `aura:langchange` so the currency symbol follows the
language toggle). Change a number here and it updates everywhere it
appears — there is no second copy of a price baked into the HTML.

These figures came from the project's own commercial-model notes (250 JD
for a licence covering 2 devices, +50 JD per extra device, +250 JD per
extra branch) as of 2026-09-05.

**Billing period is decided: ONE-TIME.** The owner confirmed on
2026-09-23 that a shop pays once per licence — not a subscription, not
monthly, not an annual renewal. That is now stated plainly (in both
languages) in several places, not just on the pricing page itself, since
it's a real selling point against subscription-priced competitors:
`meta.home_desc`, `meta.pricing_desc`, `pricing_teaser.eyebrow`/`desc`,
`pricing_page.lede`/`plan_per`/`plan_features.0`/`note`, and
`footer.note1` in `content.js`. If this ever changes again, all of those
keys need updating together, in both `en` and `ar` — grep the file for
"once" / "مرة واحدة" to find them all.

Do not extend this into promising anything about free updates or support
duration — "paid once" is a fact about the purchase, not a commitment
about what happens after it, and nothing in this repo backs a stronger
claim than that.

## Adding the customer portal later

This site is deliberately structured so a login portal can be added
without touching what's already here:

- It's a plain multi-page static site, not a single-page app with client
  routing — adding `website/account/` (or `account.html` +
  `account/*.html`) as a new, separate set of pages is a filesystem
  operation, not a rewrite.
- The header/footer markup is repeated per page (no shared templating,
  because there's no build step) — a portal section can reuse the same
  `.site-header`/`.site-footer` HTML block and `css/tokens.css` +
  `css/style.css` and it will look like the same product immediately.
- `js/content.js`'s `nav` object already has the shape a portal link would
  slot into (`nav.home`, `nav.features`, ... — add `nav.account` the same
  way).
- Nothing here assumes there's no backend elsewhere; `js/i18n.js` and
  `js/main.js` have zero dependency on this being a fully static site
  forever — a portal page can load its own additional script for
  real auth/API calls without touching these two files.

Do **not** build the portal itself yet — this repo's instructions were to
lay the ground for it, not to build it now.

## The contact form

`contact.html` has no server behind it (this whole site is static files).
"Submitting" the form does NOT claim to send anything — it builds a
`mailto:` link from what was typed (name, shop, city, phone, email,
message) and hands the browser to it, so the visitor's own email client
opens with the message pre-filled, addressed to the placeholder address in
`js/content.js`'s `AURA_CONTACT` object. The form's own note says exactly
this in both languages. **Do not** change this to a fake "message sent!"
success state — that would be lying to a shopkeeper about whether their
message actually went anywhere, which is exactly the kind of dishonest UI
CLAUDE.md's engineering standard exists to rule out.

## The contact placeholders — exactly where to edit them

`js/content.js` **line 33** starts the `CONTACT` block's comment (the
object itself is declared at **line 54** — search the file for
`CONTACT DETAILS -- edit ONLY here` if either line number has drifted).
That is the one and only place to change the site's email or phone —
nothing else needs touching:

```js
var CONTACT = {
  email: "hello@actionaura.jo",   // placeholder -- real project email TBD
  phone: ""                       // deliberately empty -- no real number yet
};
```

- **`email`** is a placeholder (`hello@actionaura.jo`) — Action Aura does
  not have an official project email live yet, one is being set up.
  Change the string and it updates everywhere automatically: the footer
  on all four pages, the "Prefer to reach us directly?" panel on
  `contact.html`, and the contact form's own `mailto:` target. This works
  because those elements carry `data-contact-link="email"` and
  `js/main.js`'s `initContactLinks()` populates both their visible text
  and `href` from `AURA_CONTACT` on every page load — there is no second,
  hardcoded copy of the email anywhere in the HTML to forget about.
- **`phone`** is empty on purpose. There is no real phone/WhatsApp number
  yet, and a fake-looking one on a sales site is worse than showing none
  at all — so the "Call or WhatsApp" block that used to sit on
  `contact.html` next to the email has been removed rather than left
  showing a placeholder digit string. When a real number exists: put it
  in `phone` (e.g. `"+962791234567"`), then add an element carrying
  `data-contact-link="phone"` wherever it should appear (the empty slot
  next to the email item in `contact.html`'s `.contact-alt` block is the
  natural place) — `initContactLinks()` already knows how to wire a
  `"phone"` key the same way it wires `"email"`, including turning it
  into a `tel:` link.

## Icons — a real trap, read this before touching `assets/icons.svg`

Every icon on the site is `<svg><use href="assets/icons.svg#icon-name">`,
referencing an external sprite file. That file is fetched and parsed as a
**standalone XML document**, not as lenient HTML — so it must be
well-formed XML, and Chromium fails that parse **completely silently**:
every icon on every page quietly renders as an empty box, with no console
error and no network error (the file still 200s). This actually happened
while building this site: the sprite's own top-of-file comment used `--`
(a double hyphen) for an em-dash-style aside, which is illegal inside an
XML comment (`<!-- ... -- ... -->` is not well-formed XML), and it broke
every single icon on every page at once. If you ever add or edit a comment
in `assets/icons.svg`, use a single hyphen or a colon, never `--`, and
validate with:

```bash
python -c "import xml.dom.minidom as m; m.parse('website/assets/icons.svg')"
```

## RTL notes (read before changing `.feature-row` or any two-column layout)

- **Never flip layout with the CSS `direction` property.** The
  `.feature-row.reverse` sections (used to alternate which side the
  illustration sits on) originally used a `direction: rtl` /
  `direction: ltr` trick to swap column order — and because `direction` is
  an inherited property, it also flipped `text-align: start` on every
  nested Arabic paragraph to LEFT-aligned, which is wrong. The fix was to
  use CSS Grid's `order` property instead (`.feature-row.reverse
  .feature-visual { order: -1; }`), which reorders grid tracks without
  touching text direction at all. If you add another "reverse" layout,
  copy this pattern, not the direction trick.
- **`min-width: 0` on grid/flex children is load-bearing here, not
  decoration.** Grid and flex items default to `min-width: auto`, which
  floors their size at their *unwrapped* content width — on a 320px phone
  that floor can exceed the viewport even though every layout rule uses
  `fr`/`%`, never a fixed px width. `style.css` sets `min-width: 0` on the
  direct children of `.hero .wrap`, `.feature-row`, `.contact-grid`,
  `.footer-grid`, `.cta-banner` and on `.bento-card` for exactly this
  reason. If you add a new two-column or flex layout, add the same rule to
  its children, or a long word/heading WILL silently overflow the page at
  narrow widths only (it can look perfectly fine at 1440px and still
  break at 320px).
- **Never hardcode a grid's column count with an inline `style` attribute
  on an element that also needs to respond to a media query.** An inline
  `style="grid-template-columns:repeat(4,1fr)"` beats every stylesheet
  rule, INCLUDING the `@media (max-width: 720px)` rule meant to collapse
  it to one column — this shipped once and overflowed the page at phone
  width. Use a real class (see `.req-grid--2` / `.req-grid--4` in
  `style.css`) so the responsive rules can still win.

## Verifying a change

There's no test suite (it's a marketing site), but before calling a change
done:

1. Serve the folder locally on an unusual port so you don't collide with
   anything else running in this repo, e.g.:
   ```bash
   python -m http.server 48273 --bind 127.0.0.1
   ```
   (stop it again when you're done — don't leave it bound).
2. Open every page in both languages and check the browser console for
   errors or `[aura-i18n] missing "..."` warnings.
3. Check at 320px width (a real low-end phone), not just 375/390 — several
   real overflow bugs during this site's own build only showed up at
   320px and were invisible at 390px.
4. Toggle the language back and forth a few times, not just once — a race
   condition between the language toggle and `pricing.html`'s currency
   re-render (two independent click listeners on the same button, racing
   on `document.documentElement.lang`) only showed up as "the price symbol
   is one click behind," which a single before/after screenshot would
   have missed entirely. Fixed by having `js/i18n.js` fire an
   `aura:langchange` event that `pricing.html` listens for, instead of
   guessing at listener order.

## Deploying

It's a folder of static files — any static host works: drag the `website/`
folder into Netlify Drop, or use `netlify deploy`/`vercel deploy`/GitHub
Pages/Cloudflare Pages pointed at this directory. No environment
variables, no build command, no server. If a host asks for a build
command, leave it blank and set the publish directory to `website/`.
