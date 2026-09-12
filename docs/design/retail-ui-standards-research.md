# What a top-tier retail POS / back-office UI must get right

A source-backed reference for Aura Retail. Researched and written 2026-09-12.

This document exists so that a design argument in this repo can be settled by a
citation instead of by taste. It is written for the product as it actually is —
a POS and back-office for small shops in Jordan, running on a Windows counter
laptop and on Android all-in-one terminals, bilingual English/Arabic with full
RTL, five themes, roughly half of installs with no mouse. `DESIGN.md` is the
brief for *this* product; this document is the outside evidence that brief
should be held against.

---

## 0. How to read this

### 0.1 Three tiers, marked on every claim

Every substantive statement below carries one of three tags. A reviewer must be
able to tell "this violates a published standard" from "I would have done it
differently", and that distinction is the whole point of the tagging.

| Tag | Meaning | What a violation is |
|---|---|---|
| **[HARD]** | A published normative requirement: a WCAG success criterion, a platform rule, an ISO / Unicode / ISO 4217 standard, a currency definition. Someone else wrote it down and it does not care what we think. | A defect. Fix it. |
| **[CONVENTION]** | Not normative, but supported by named evidence — usability research, multiple independent vendor design systems agreeing, or a measured study. Deviating is allowed but needs a reason written down. | A decision that must be justified. |
| **[TASTE]** | A defensible preference with no standard and no study behind it. Included only where the alternative is silence. | Not a defect. Argue it on the merits. |

Where the evidence for something is thin, it says so. Where sources disagree,
both are given with both URLs, and the disagreement is not resolved by guessing.

### 0.2 What "source type" means here

- **standards body** — W3C/WAI, Unicode Consortium, ISO, the ISO 4217
  maintenance agency. Strongest.
- **platform documentation** — Apple HIG, Android/Material, Microsoft Learn.
  Binding on that platform, advisory elsewhere.
- **vendor design system** — a company's published design rules.
- **research** — peer-reviewed or a named institutional study.
- **practitioner writing** — NN/g, Baymard, a type foundry, a known engineer's
  article. Credible, not normative.
- **SEO content-marketing** — a listicle written to rank. Treated as *weak* and
  labelled as such wherever it is the only thing available.

### 0.3 Method, and its limits — stated plainly

Roughly 120 URLs were fetched for this document across five parallel research
tracks. Everything quoted below was retrieved and read; nothing is quoted from
memory.

Three honest limitations:

1. **Apple's and Material's live documentation is JavaScript-rendered** and
   returns an empty shell to a plain fetch. Those pages were retrieved through
   a text-extraction proxy (`r.jina.ai`). The content is the real page, but it
   passed through a renderer this document does not control. Apple and Material
   quotes are marked accordingly.
2. **The web-search budget was exhausted partway through.** Later topics relied
   on direct fetches of URLs already known. Some gaps below are gaps in the
   *search*, not proof of absence, and are labelled that way.
3. **Several things could not be sourced at all.** They are listed in §8.2
   rather than filled in with plausible-sounding numbers. A wrong citation is
   worse than an admitted gap.

---

## 1. POS-specific interaction standards

### 1.1 The first finding is a negative one, and it matters

**No major POS vendor publishes a numeric UI specification.** This was checked
directly, not assumed:

- **Square** — `https://design.squareup.com/us/en/articles/designing-at-scale-part-one`
  (vendor design system). Square's system is called **Market**. The public
  material is organisational and process-focused; there are no component specs,
  no touch-target sizes, no layout rules. (A Square design system named
  "Quantum" could not be verified and appears to belong to unrelated
  companies.)
- **Shopify POS** — `https://shopify.dev/docs/apps/build/pos` (vendor dev
  docs). Quote: *"Components are the building blocks you use to render your
  custom UI in the POS interface… Shopify's UI toolkit provides a wide range of
  web components like buttons, tiles, and modals that match the Shopify POS
  design system."* Shopify enforces consistency by **restricting extension
  developers to prebuilt components** rather than by publishing pixel rules. No
  numbers are disclosed.
- **Toast** — `https://doc.toasttab.com/doc/devguide/index.html` (vendor dev
  docs). Confirmed: pure API reference (orders, payments, menus, stock,
  webhooks). **No design system exists in Toast's public developer docs.**
- **Elo, Verifone, PAX, Ingenico** (terminal hardware vendors) — no publicly
  fetchable UI design guideline document was found for any of the four. PAX's
  developer guide
  (`https://faqs.pax.us/wp-content/uploads/2020/09/PAXSTORE-Developer-Guide-V2.00-09-11-2020-1.pdf`)
  covers app submission only. Verifone's developer portal returned a TLS
  certificate error.

**Clover is the exception, and its guidance is worth reading.**
`https://docs.clover.com/dev/docs/design-resources` (vendor dev docs):

> *"Design clear, efficient, and robust workflows that minimize the number of
> steps required, as Clover apps are often used in fast-paced work
> environments."*

> *"Prioritize clarity and accessibility in your UI. Use high-contrast visuals,
> readable fonts, and generously sized text and input controls."*

`https://docs.clover.com/dev/docs/app-design-requirements` sets a hard floor of
**4.5:1 contrast** for submitted apps. Notably, Clover **defers to Google
Material Design** for the actual numbers rather than inventing its own.

> **Consequence for us [CONVENTION]:** there is no "POS industry standard" to
> conform to. The real standards for a POS UI are the *platform* accessibility
> rules (§1.2, §2), and the only POS vendor that says anything normative says
> "use Material's numbers and 4.5:1". Any claim in a review that "POS systems
> do it this way" is a claim about observed products, not about a published
> standard, and should be labelled that way.

### 1.2 Touch target size — the numbers, and which ones bind

This is the single most-cited and most-garbled number in UI design. Here is what
each source *actually* says, fetched directly.

| Source | Number | Tier | Exact wording |
|---|---|---|---|
| **WCAG 2.2 SC 2.5.8 Target Size (Minimum)**, Level **AA** | **24 × 24 CSS px** | **[HARD]** | *"The size of the target for pointer inputs is at least 24 by 24 CSS pixels, except when…"* |
| **WCAG 2.2 SC 2.5.5 Target Size (Enhanced)**, Level AAA | 44 × 44 CSS px | [CONVENTION] | *"The size of the target for pointer inputs is at least 44 by 44 CSS pixels"* |
| **Android accessibility** | **48 × 48 dp** | [HARD] on Android | *"Ensure all touch targets are at least 48 dp, even if this extends past the UI element visual."* |
| **Apple HIG — default** | 44 × 44 pt (iOS/iPadOS) | [HARD] on iOS | *"a button needs a hit region of at least 44x44 pt … to ensure that people can select it easily"* |
| **Apple HIG — stated *minimum*** | **28 × 28 pt** (iOS/iPadOS) | [HARD] on iOS | 44 pt is the *default*; 28 pt is the published *minimum* |
| **Microsoft / Windows** | **7.5 mm square ≈ 40 × 40 px** at 135 PPI | [CONVENTION] | *"In general, set your touch target size to 7.5mm square range (40x40 pixels on a 135 PPI display at a 1.0x scaling plateau)"* |
| **NN/g** | **10 × 10 mm** (1 cm × 1 cm) | [CONVENTION] | *"Interactive elements must be at least 1cm × 1cm (0.4in × 0.4in) to support adequate selection time and prevent fat-finger errors."* |
| **Parhi, Karlson & Bederson 2006** | **9.2 mm** discrete, **9.6 mm** serial | research | see below |

Sources, all fetched:
`https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html` ·
`https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html` ·
`https://developer.android.com/design/ui/mobile/guides/foundations/accessibility` ·
`https://developer.apple.com/design/human-interface-guidelines/accessibility`
and `/buttons` (via text-extraction proxy) ·
`https://learn.microsoft.com/en-us/windows/apps/develop/input/guidelines-for-targeting` ·
`https://www.nngroup.com/articles/touch-target-size/` (Aurora Harley,
5 May 2019).

**Where sources disagree.** WCAG's binding AA floor (24 px) is *less than half*
NN/g's recommended physical minimum (10 mm ≈ 38 px at a typical 96 dpi laptop).
They are not measuring the same thing: WCAG sets a legal-conformance floor that
must be achievable in dense text UIs; NN/g reports what avoids fat-finger
errors. **Both are correct and the practical answer for a till is NN/g's.** Do
not cite "WCAG says 24 px" as evidence that a 24 px till button is adequate — it
is evidence only that it is not *non-conforming*.

**Apple's 28 pt is a real number and is routinely misquoted.** Apple's own
accessibility page publishes a default/minimum pair per platform (iOS 44/28,
macOS 28/20, tvOS 66/56, visionOS 60/28, watchOS 44/28) under the sentence
*"Strive to meet the recommended minimum control size for each platform to
ensure controls and menus are comfortable for all when tapping and clicking."*
"Apple requires 44 × 44" is a simplification; 44 pt is what Apple wants used.
Apple also gives spacing guidance: roughly **12 pt of padding around bezelled
elements and 24 pt around non-bezelled ones**.

**The Parhi study, with its real numbers.** Parhi, P., Karlson, A. K. &
Bederson, B. B., *"Target size study for one-handed thumb use on small
touchscreen devices"*, MobileHCI '06, pp. 203–210
(`https://www.microsoft.com/en-us/research/publication/target-size-study-for-one-handed-thumb-use-on-small-touchscreen-devices/`,
peer-reviewed research; publisher record `10.1145/1152215.1152260`). Microsoft
Research's own abstract page states **9.2 mm for discrete tasks** and **9.6 mm
for serial tasks**, with no significant error difference above those sizes.
Note a discrepancy: a second summary of the same paper reports the serial figure
as **7.6 mm**. The full text is paywalled and was not retrieved, so both numbers
are reported and neither is adopted as definitive.

**The exceptions to SC 2.5.8 matter and are frequently forgotten** [HARD]. A
target under 24 px still conforms if any of these hold:

1. **Spacing** — *"Undersized targets … are positioned so that if a 24 CSS pixel
   diameter circle is centered on the bounding box of each, the circles do not
   intersect another target or the circle for another undersized target"*;
2. **Equivalent** — *"The function can be achieved through a different control
   on the same page that meets this criterion"*;
3. **Inline** — *"The target is in a sentence or its size is otherwise
   constrained by the line-height of non-target text"*;
4. **User Agent Control** — the author did not modify the size;
5. **Essential** — *"A particular presentation of the target is essential or is
   legally required"*.

### 1.3 The cost of a mis-tap: this is a *slip* problem, not a *mistake* problem

The relevant research distinction, from NN/g
(`https://www.nngroup.com/articles/user-mistakes/` and
`https://www.nngroup.com/articles/slips/`, practitioner writing):

> *"Slips occur when a user is on autopilot, and takes the wrong actions in
> service of a reasonable goal."*
>
> *"Mistakes occur when a user has developed a mental model of the interface
> that isn't correct."*

NN/g states explicitly that slips hit **experienced users performing fast,
repetitive tasks** hardest. That is the definition of a cashier. A till screen's
error budget is therefore spent almost entirely on slips, and slip mitigation is
*constraints, defaults and forgiving formatting* — not documentation, not
training, not tooltips.

**Real touch offset is measurable and systematic.** Henze, N., Rukzio, E. &
Boll, S., *"100,000,000 Taps: Analysis and Improvement of Touch Performance in
the Large"*, MobileHCI '11 — author-hosted full text at
`https://nhenze.net/uploads/100000000-Taps-Analysis-and-Improvement-of-Touch-Performance-in-the-Large.pdf`
(peer-reviewed research). **120,626,225 touch events from 91,731 Android
installations.** Touches were systematically offset from the intended target,
device-dependently (e.g. HTC Wildfire ≈ 1.22 cm / 2.44 cm from a lower-right
target); a compensation function reduced touch error by **7.79 %**. The authors
caveat that handedness could not be determined, so the thumb-posture explanation
is their hypothesis, not a proven cause.

> **Consequence [CONVENTION]:** touch error is not random jitter you can design
> away with a slightly bigger button; it has a systematic directional component
> that varies by device. This is an argument for *spacing between destructive
> and non-destructive controls*, not merely for larger controls.

**Microsoft says the quiet part out loud**, and it is the best single sentence
in the literature for a POS
(`https://learn.microsoft.com/en-us/windows/apps/develop/input/guidelines-for-targeting`,
platform documentation):

> *"Consider making targets that are repeatedly or frequently pressed larger
> than the minimum size, and targets that have severe consequences if touched in
> error should have greater padding."*

That is, verbatim, the rule for **Pay** (frequently pressed → larger) and for
**Void / Refund / Delete line** (severe consequence → greater padding).

### 1.4 Confirm, or undo? The money path decides

NN/g, `https://www.nngroup.com/articles/confirmation-dialog/` (Jakob Nielsen,
18 Feb 2018; last reviewed 7 Aug 2026), practitioner writing:

> *"Use a confirmation dialog before committing to actions with serious
> consequences — such as destroying users' work or costing large amounts of
> money"*
>
> *"if you cry wolf too many times, people will stop paying attention to the
> question, and the confirmation dialog will lose its power to prevent errors."*
>
> *"Do go to great lengths to provide undo, because some user errors will remain
> despite even the best of confirmation dialogs."*

WCAG has an actual **normative** criterion for exactly this situation, and a POS
is squarely inside its scope. **SC 3.3.4 Error Prevention (Legal, Financial,
Data), Level AA** [HARD]
(`https://www.w3.org/WAI/WCAG22/Understanding/error-prevention-legal-financial-data.html`):

> *"For web pages that cause legal commitments or financial transactions for the
> user to occur, that modify or delete user-controllable data in data storage
> systems, or that submit user test responses, at least one of the following is
> true:"* — **Reversible** (*"Submissions are reversible"*), **Checked**
> (*"Data entered by the user is checked for input errors and the user is
> provided an opportunity to correct them"*), or **Confirmed** (*"A mechanism is
> available for reviewing, confirming, and correcting information before
> finalizing the submission"*).

The Intent section gives the rationale in money terms: *"Both of these types of
mistakes involve transactions that take place immediately and cannot be altered
afterwards, and can be very costly."*

> **Consequence [HARD + CONVENTION]:** every irreversible money action must
> satisfy at least one of reversible / checked / confirmed — that part is
> normative. *Which* one is the design decision, and NN/g's evidence says prefer
> **reversible** (undo) over **confirmed** (dialog) wherever reversal is
> genuinely possible, because a dialog on a routine action trains the cashier to
> dismiss dialogs. Adding a line to the cart should never confirm. Voiding a
> completed sale should.

### 1.5 Response time: three limits, and what they mean at a till

NN/g, `https://www.nngroup.com/articles/response-times-3-important-limits/`
(Jakob Nielsen), practitioner writing — the canonical numbers, quoted:

- **0.1 s** — *"the limit for having the user feel that the system is reacting
  instantaneously"*; no special feedback needed.
- **1.0 s** — *"the limit for the user's flow of thought to stay uninterrupted,
  even though the user will notice the delay."*
- **10 s** — *"the limit for keeping the user's attention focused on the
  dialogue."* Beyond it, show a percent-done indicator.

> **Consequence [CONVENTION]:** a scanned line must appear in the cart within
> 0.1 s or the cashier will scan again. Anything on the money path that can
> exceed 1 s (card authorisation, e-invoice submission, sync) must show a
> determinate, non-dismissable state — and the Pay control must be disabled for
> the duration, because the alternative is a double charge (§1.7).

### 1.6 Scan-first workflow and keyboard-only operation

Barcode scanners in retail are **keyboard-wedge HID devices**: they emulate
typing. Zebra's DataWedge documentation
(`https://techdocs.zebra.com/datawedge/7-4/guide/output/keystroke/`, vendor
documentation, retrieved via text-extraction proxy):

> *"Keystroke Output collects the processed data and sends it to the associated
> application as a series of keystrokes, emulating the actions of a user
> pressing keys on the device."*
>
> *"DataWedge supports TAB, ENTER and other special characters that might be
> required by an application to submit acquired data for further processing, to
> advance the cursor to another input field or for other reasons."*

The ENTER suffix is configurable and can be sent either as a key event or as a
string. Honeywell's equivalent suffix configuration could not be fetched
directly (their support site returned a client-side error shell) — treat the
Honeywell specifics as **unverified**, though the mechanism is identical in kind.

> **Consequences [CONVENTION], derived from the documented hardware behaviour
> rather than quoted from a POS design document — there is no such document
> (§1.1):**
>
> 1. The scan-target field must hold focus by default and **regain** it after
>    every modal, every toast, every list update. A scan aimed at a field that
>    lost focus is silently lost or, worse, typed into the wrong field.
> 2. Enter arriving at the scan field means "commit this line", and that must
>    not be the same Enter that submits the *payment* form. A scan fired while a
>    payment dialog is open must not complete the sale.
> 3. Nothing on the sale path may require a pointer. A till with a scanner and a
>    keyboard must be able to complete a sale end to end.
> 4. `inputmode` matters more than `type`. Baymard
>    (`https://baymard.com/labs/touch-keyboard-types`) warns that
>    `type="number"` is inconsistent across Android and mishandles leading
>    zeros; use `inputmode="decimal"` with `pattern="[0-9]*"` for amount and
>    quantity fields.

**Number-pad layout is a real, cited conflict** [CONVENTION]. Calculators and
cash registers put **7-8-9 on the top row**; telephones put **1-2-3 on top**, and
every touchscreen OS number pad follows the telephone. Wikipedia's
`Numeric_keypad` and `Telephone_keypad` articles record both the convention and
its origin, citing **Deininger, R. L. (1960), "Human Factors Engineering Studies
of the Design and Use of Pushbutton Telephone Sets", *Bell System Technical
Journal* 39(4), pp. 995–1012, doi:10.1002/j.1538-7305.1960.tb04447.x** — Bell
Labs found the telephone layout *slightly faster* than the calculator layout. A
POS sits between two populations: a cashier trained on a cash register expects
7-8-9 on top; a person holding a phone expects 1-2-3. **No standard resolves
this.** Pick one, apply it everywhere in the product, and never ship both
layouts in the same app.

### 1.7 Payment refusal, decline states, and not charging twice

No usability source was found covering "what a checkout screen should visually
do when the network drops mid-sale" — a genuine gap (§8.2). What exists is
payment-processor API documentation, which is nonetheless the best available
model for the *content* of a refusal.

Stripe, `https://docs.stripe.com/declines` (vendor documentation). Three
distinct failure classes a UI must not conflate:

1. **Issuer declines** — the bank said no.
2. **Blocked payments** — the processor's fraud system blocked it: *"When Stripe
   blocks a payment, it doesn't obtain authorization from the card issuer"*.
3. **Invalid API calls** — an integration bug.

Stripe ships a pre-written customer-safe `seller_message` string and an
`advice_code` (`do_not_try_again` vs `confirm_card_data`) precisely so that
merchants do not invent their own decline copy.

Stripe, `https://docs.stripe.com/idempotency` (vendor documentation):

> *"use an idempotency key. Then, if a connection error occurs, you can safely
> repeat the request without risk of creating a second object or performing the
> update twice."*

Keys expire after **24 hours**. Critically, *"we don't save the idempotent
result because no API endpoint initiates execution"* when the initial request
fails validation — idempotency protects against ambiguous network retries,
**not** against a UI that lets the cashier press Pay twice.

> **Consequence [CONVENTION]:** the Pay control must enter a disabled, visibly
> pending state on first press and stay there until the server answers or the
> request is definitively abandoned. Server-side idempotency is the safety net;
> the disabled button is the actual control. A refusal must say **which of the
> three classes it is** in words, because "declined" covers three different next
> actions (retry, try another card, call someone).

### 1.8 Cart / total / charge layout

No published source specifies this spatial relationship — see §1.1. What can be
said with citation is narrower and still useful:

- The total is the one number the *customer* also reads, often from a metre
  away. ISO 9241-303's visual-angle requirement (§2.4) is the only citable basis
  for sizing it, and it gives a formula, not a pixel value.
- **[CONVENTION]** Clover's *"minimize the number of steps required"* and NN/g's
  slip research both argue for the charge action occupying a fixed, invariant
  screen position — a control that moves is a control that gets mis-tapped by a
  user on autopilot.
- **[TASTE]** Everything else — two-column vs stacked layouts, tile grids vs
  search-first product entry, where a customer-facing total sits — is taste.
  Observed-product arguments ("Square does X") are observations, not standards,
  and should be labelled as such in review.

### 1.9 Error and validation behaviour at the moment of entry

Baymard Institute, `https://baymard.com/blog/inline-form-validation`
(practitioner writing, backed by their usability-testing corpus):

- **31 % of sites have no inline validation at all**; a further **4 %** implement
  it but get it wrong.
- Timing rule, quoted: *"the validity of each field input should be checked when
  the user leaves the field — for example using an `onblur` event"*, while the
  **error must clear live**: *"the error message must live update on a keystroke
  level, disappearing the moment users enter a valid input."*
- Premature validation was observed to make participants stop typing to read an
  error they had not yet earned.

**Transfer caveat, stated because it is real:** Baymard studies *e-commerce web
checkout* — an unattended buyer filling a self-directed form — not a trained
cashier under time pressure with a customer waiting. The validation-timing and
keyboard-type findings transfer. Anything Baymard says about cart abandonment,
trust signals or checkout step count **does not transfer to a POS at all** and
must not be cited as if it did.

GOV.UK Design System
(`https://design-system.service.gov.uk/components/error-message/`, government
design system) — the most prescriptive published rules on error wording found
anywhere:

> put the message in red after the question text and hint text · use a red
> border to visually connect the message and the question it belongs to · if the
> error relates to a specific field within the question, give it a red border
> and refer to that field in the error message
>
> *"Do not clear any form fields when showing the Error message component. Keep
> both passing and failing answers."*

Banned wording, quoted: technical jargon like *"form post error"*, *"unspecified
error"*; the words *"forbidden"*, *"illegal"*, *"you forgot"*, *"please"*,
*"sorry"*, *"valid"* and *"invalid"*; informal *"oops"*; and general errors such
as *"An error occurred"* or *"This field is required"*.

NN/g, `https://www.nngroup.com/articles/error-message-guidelines/` (Neusesser &
Sunwall, 14 May 2023): messages must be *"displayed close to the error's
source"*, use *"human-readable language"*, *"offer constructive advice"*, adopt a
*"positive and nonjudgmental tone"*, and *"never use exclusively color or
animation to indicate errors"*.

That last point is not merely advice. **WCAG SC 3.3.1 Error Identification,
Level A** [HARD]
(`https://www.w3.org/WAI/WCAG22/Understanding/error-identification.html`):

> *"If an input error is automatically detected, the item that is in error is
> identified and the error is described to the user in text."*

The words **"in text"** are normative. A red border alone fails.

### 1.10 Labels, not placeholders

NN/g, `https://www.nngroup.com/articles/form-design-placeholders/` (Katie
Sherwin, 11 May 2014; reviewed 10 Sep 2018), practitioner writing. Four
documented failures of placeholder-as-label: it *"strains users' short-term
memory"*; users *"may mistake a placeholder for data that was automatically
filled in"* and skip the field; *"the default light-grey color of placeholder
text has poor color contrast against most backgrounds"*; and it vanishes on
focus, penalising keyboard users. Recommendation: *"clear, visible labels that
are placed outside empty form fields."*

**[CONVENTION], strongly evidenced.** A price or quantity field whose only label
is a placeholder is a defect at a till, where the operator is not reading
carefully.

---

## 2. Accessibility and legibility that actually matters here

### 2.1 Contrast — the exact thresholds [HARD]

All fetched from `https://www.w3.org/WAI/WCAG22/Understanding/…` (standards
body). These are the numbers, with nothing rounded or paraphrased.

| Criterion | Level | Threshold | Applies to |
|---|---|---|---|
| **1.4.3 Contrast (Minimum)** | **AA** | **4.5:1** normal text, **3:1** large text | text and images of text |
| **1.4.6 Contrast (Enhanced)** | AAA | **7:1** normal text, **4.5:1** large text | text and images of text |
| **1.4.11 Non-text Contrast** | **AA** | **3:1** against adjacent colours | UI component boundaries and states; parts of graphics needed to understand content |

**"Large text" has an exact definition** and it is smaller than people assume:
*"at least 18 point or 14 point bold"*, and the Understanding document gives the
CSS conversion explicitly: *"1pt = 1.333px, therefore 14pt and 18pt are
equivalent to approximately 18.5px and 24px."* So **24 px regular** or
**18.5 px bold** is where the 3:1 allowance begins. Below that, 4.5:1.

**The rationale numbers, useful when arguing about a specific pair.** 4.5:1
exists because *"visual acuity of 20/40 is associated with a contrast
sensitivity loss of roughly 1.5"*, and 3 × 1.5 = 4.5. The AAA 7:1 figure
*"compensated for the loss in contrast sensitivity usually experienced by users
with vision loss equivalent to approximately 20/80 vision"*.

**Exceptions** [HARD]: incidental text (inactive components, pure decoration,
not visible, or part of a picture with significant other visual content), and
**logotypes** — *"Text that is part of a logo or brand name has no contrast
requirement"*. That exception is narrow: it covers the mark, not the product
name rendered as UI text.

**SC 1.4.11 is the one most often missed.** It requires 3:1 for *"visual
information required to identify user interface components and states"* — an
input's border against the surface behind it, a checkbox outline, a
selected-tab indicator, a focus ring. The Understanding document's own passing
example is *"A standard text input with a grey border (#767676) and white
adjacent color outside the component."* A 1 px hairline at 1.4:1 that "looks
nicer" is a conformance failure, not a style choice.

### 2.2 The WCAG 2.x contrast formula is known to be wrong for dark themes

This matters enormously for a product with three dark themes, and it is almost
never said out loud.

Andrew Somers / Myndex,
`https://git.apcacontrast.com/documentation/WhyAPCA.html` (practitioner /
standards-adjacent writing by the algorithm's author, working under the W3C
Accessibility Guidelines Working Group):

> *"WCAG 2.x contrast cannot be used for guidance designing 'dark mode.'"*
>
> *"WCAG 2.x … far overstates contrast for dark colors to the point that 4.5:1
> can be functionally unreadable when a color is near black."*
>
> *"WCAG 2 contrast can pass colors that should fail as not readable, and
> sometimes the WCAG 2 math fails a color pair that should pass as very
> readable."*

**APCA's status, from a W3C primary source** [HARD, as a fact about status]:
APCA is **not normative** and **not part of WCAG 2.2**. W3C's own WCAG 3
introduction (`https://www.w3.org/WAI/standards-guidelines/wcag/wcag3-intro/`)
states that *"WCAG 3 will not supersede WCAG 2 and WCAG 2 will not be deprecated
for at least several years after WCAG 3 is finalized"* and that the drafts *"are
in an exploratory or developing phase and will change substantially"*. Notably,
that W3C page does **not name APCA at all** — the widely-repeated "APCA is
WCAG 3's contrast algorithm" framing is not confirmed by W3C's public-facing
overview.

> **Consequence for a five-theme product [HARD + CONVENTION]:** WCAG 2.2's
> ratios remain the compliance floor and must be met — that is not optional.
> But **passing 4.5:1 in Calm, Night and Dusk does not prove those themes are
> readable**, and the algorithm's own author says so. A computed ratio answers
> "is this conformant", never "is this legible on a cheap panel at a counter".

### 2.3 The other criteria a till screen actually trips

Beyond §1.2's target-size numbers, these are the criteria a POS touches most
often. All [HARD] at the level stated.

- **SC 1.4.1 Use of Color, Level A**: *"Color is not used as the only visual
  means of conveying information, indicating an action, prompting a response, or
  distinguishing a visual element."* See §4.2.
- **SC 2.4.11 Focus Not Obscured (Minimum), Level AA** (new in 2.2): *"When a
  user interface component receives keyboard focus, the component is not
  entirely hidden due to author-created content."* The Intent names **sticky
  headers and sticky footers** as the typical offenders. A till with a fixed
  header and a fixed total bar is exactly the shape that fails this.
- **SC 2.4.13 Focus Appearance, Level AAA**: the focus indicator must be *"at
  least as large as the area of a 2 CSS pixel thick perimeter of the unfocused
  component or sub-component"* **and** have *"a contrast ratio of at least 3:1
  between the same pixels in the focused and unfocused states."* Note that is a
  *change* ratio, not an adjacent-colour ratio.
- **SC 1.4.12 Text Spacing, Level AA**: no loss of content or functionality when
  a user sets line height to **1.5×** font size, paragraph spacing to **2×**,
  letter spacing to **0.12×**, word spacing to **0.16×**. Critically for §3, the
  criterion carves out scripts: *"Human languages and scripts that do not make
  use of one or more of these text style properties in written text can conform
  using only the properties that exist for that combination of language and
  script."*
- **SC 2.5.3 Label in Name, Level A**: *"For user interface components with
  labels that include text or images of text, the name contains the text that is
  presented visually."* It explicitly does **not** apply to icon-only controls —
  *"where a visible text label does not exist for a component, this success
  criterion does not apply to that component"* — those are covered by SC 4.1.2
  instead (§5.2).
- **SC 3.3.1 Error Identification, Level A** and **SC 3.3.4 Error Prevention
  (Legal, Financial, Data), Level AA** — §1.9 and §1.4.

**Which level to target.** W3C itself
(`https://www.w3.org/WAI/WCAG22/Understanding/conformance`): *"It is not
recommended that Level AAA conformance be required as a general policy for
entire sites because it is not possible to satisfy all Level AAA success
criteria for some content."*

> **[CONVENTION]** AA everywhere is the right bar; AAA (7:1) is worth adopting
> *selectively* for the money numbers. That is a defensible targeted use of AAA
> rather than a blanket claim.

### 2.4 Viewing distance, physical size, and glare — the weakest-sourced section

**Stated honestly: the standard that governs this could not be read.**
ISO 9241-303 (*Ergonomics of human-system interaction — Part 303: Requirements
for electronic visual displays*, `https://www.iso.org/standard/57992.html`) is
paywalled; iso.org returned 403, the sample PDFs did not extract as text, and
`web.archive.org` is unreachable from this environment. ANSI/HFES 100 is
likewise real but inaccessible.

The commonly-repeated figures — a **16 arcminute minimum** character height with
**20–22 arcminutes** preferred, at a **400–750 mm** viewing distance — appear in
search summaries and in practitioner summaries of the standard
(e.g. `https://www.userfocus.co.uk/resources/iso9241/part3.html`, which returned
403 on fetch). **They could not be verified against the standard itself and are
therefore WEAK.** Do not cite an arcminute figure in a review as though this
document established it.

What *is* confirmed is only the geometry: visual angle V = 2·arctan(S / 2D)
(`https://en.wikipedia.org/wiki/Visual_angle`, tertiary source). That gives a
method — pick a target angle, measure the real viewing distance, compute the
physical height — but this document cannot supply the authoritative target
angle.

**Ambient light and legibility — what *is* peer-reviewed.** Dobres, J.,
Chahine, N. & Reimer, B. (2017), *"Effects of ambient illumination, contrast
polarity, and letter size on text legibility under glance-like reading"*,
*Applied Ergonomics*, DOI `10.1016/j.apergo.2016.11.001` (peer-reviewed; abstract
verified via Europe PMC). The finding, and it is directly the shop-counter case:
**negative polarity (light text on dark) under dark ambient illumination
produced the worst legibility**, while bright ambient illumination combined with
positive polarity improved legibility thresholds — because brighter light
contracts the pupil and reduces optical aberration. "Glance-like reading" is
precisely what a cashier does to a total.

> **Consequence [CONVENTION], and it cuts against fashion:** for a bright, glary
> counter, a **light** theme is the better default for money-critical surfaces,
> not a dark one. See §4.4 — the evidence here is stronger than the vendor
> claims that point the other way.

### 2.5 Numeric legibility: tabular figures, decimal alignment, digit confusion

**Tabular figures are an explicit OpenType feature and are not on by default.**
From the OpenType specification
(`https://learn.microsoft.com/en-us/typography/opentype/spec/features_pt`,
standards body / platform documentation):

> `tnum` — Tabular Figures. *"Replaces figure glyphs set on proportional widths
> with corresponding glyphs set on uniform (tabular) widths. Tabular widths will
> generally, but not always, be the default. This feature would not be used in
> monospaced designs."*
>
> *"UI suggestion: This feature should be off by default."*

That last line is the operative one: **you cannot assume tabular figures**; they
must be requested. In CSS that is `font-variant-numeric: tabular-nums` (MDN:
*"activating the set of figures where numbers are all of the same size, allowing
them to be easily aligned like in tables. It corresponds to the OpenType values
`tnum`"*,
`https://developer.mozilla.org/en-US/docs/Web/CSS/font-variant-numeric`).

Related values worth knowing, same MDN source: `lining-nums` (`lnum`) puts all
figures on the baseline — old-style figures, where *"some numbers, like 3, 4, 7,
9 have descenders"*, are wrong for money; `slashed-zero` (`zero`) *"forces the
use of a 0 with a slash; this is useful when a clear distinction between O and 0
is needed."*

> **[CONVENTION], strongly supported:** every column of money, every quantity
> column, every running total and the grand total must be set with
> `tabular-nums lining-nums`. A proportional `1` is narrower than a `9`; in a
> column that means the decimal point wanders, and a wandering decimal point in a
> three-decimal currency (§3.5) is a misread waiting to happen.

**Digit-confusion research exists but is thin.** The one credible overview
located (`https://www.sciencedirect.com/science/article/pii/S0042698919301087`,
*Vision Research*, peer-reviewed) frames the problem correctly — misreading
numbers on signs, medicine leaflets and aircraft displays has severe
consequences, yet numeral legibility is under-studied — and reports that
**wider fonts produced better recognition and fewer misreadings** than narrower
ones. A more detailed practitioner survey
(`https://typography.guru/journal/letters-symbols-misrecognition/`) returned
**403** and could not be read.

> **[CONVENTION], moderately supported:** prefer a wider, open numeral design for
> money; do not condense digits to fit a column; and do not pick a typeface where
> `0`/`O`, `1`/`l`/`I` or `5`/`S` are near-identical for fields where a cashier
> types a code. This is weaker evidence than the tabular-figures point and should
> be argued as a preference, not a rule.

---

## 3. Arabic and RTL

This is the section Western design writing gets wrong or skips, so it is the
longest and the most heavily cited. Read §3.4 and §3.5 before touching any
screen that shows money.

### 3.0 A structural finding: there is no standard for RTL *UI*

W3C has mature, normative guidance for bidirectional **text** (UAX #9, the i18n
articles, the bidi test suite). It has **no** guidance for RTL **user interface
components** — which icons flip, which controls mirror, where a currency sign
goes in a layout. This is confirmed by an open W3C issue requesting exactly such
a document: `https://github.com/w3c/i18n-drafts/issues/757` (W3C tracker, filed
2025-08-07).

> **Consequence:** the text-level rules in §3.4/§3.6 are **[HARD]** — they come
> from Unicode and W3C. The component-level mirroring rules in §3.1 are
> **[CONVENTION]** — they come from Material, Apple and Microsoft, which happen
> to agree with each other, and that agreement is the strongest evidence
> available, not a standard.

### 3.1 What mirrors and what must not

**Five independently fetched sources converge on the same non-mirroring list.**
That convergence is the finding; no single one of them is authoritative.

| Source | Type | URL |
|---|---|---|
| Material Design (bidirectionality) | vendor design system | `https://m1.material.io/usability/bidirectionality.html` (legacy; the M3 page is a JS SPA and could not be fetched) |
| Apple (Supporting Right-To-Left Languages) | platform documentation | `https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPInternational/SupportingRight-To-LeftLanguages/SupportingRight-To-LeftLanguages.html` (archived) |
| Apple HIG "Right to left" (current) | platform documentation | `https://developer.apple.com/design/human-interface-guidelines/right-to-left` (via text-extraction proxy) |
| Microsoft Globalization — Mirroring | platform documentation | `https://learn.microsoft.com/en-us/globalization/fonts-layout/mirroring` |
| Android — RTL support | platform documentation | `https://developer.android.com/training/basics/supporting-devices/languages` |

**MIRRORS** [CONVENTION]:

- Overall layout, reading order, text alignment, navigation order.
- Directional icons: arrows, back/forward, "next", indent/outdent. Apple: *"Flip
  an interface icon that shows forward or backward motion."*
- Controls showing progress between two values. Apple: *"Flip controls that show
  progress from one value to another."*
- Asymmetric controls whose asymmetry encodes direction — Material's example is
  *"A volume icon with a slider at its right side should be mirrored."*
- Scroll bars. Microsoft: *"If a scroll bar is required, it appears on the left
  side of the control."*
- Brackets and quotation marks — this one **is** [HARD], because it is a Unicode
  character property, not a design choice. UAX #9 rule L4: *"A character is
  depicted by a mirrored glyph if and only if (a) the resolved directionality of
  that character is R, and (b) the Bidi_Mirrored property value of that
  character is Yes."* The authoritative list is
  `https://www.unicode.org/Public/UNIDATA/BidiMirroring.txt`. This is the
  renderer's job, not yours — but it means you must not hard-code a `(` as
  artwork.

**MUST NOT MIRROR** [CONVENTION, five-source agreement]:

- **Numbers.** Material: *"Numbers, such as the clock and phone numbers"* are not
  mirrored. Apple, verbatim and unambiguous: ***"Don't reverse the order of
  numerals in a specific number."*** Android lists *"Numbers in formatted
  messages"* under what not to mirror. **This is the rule most often broken and
  the most expensive to break in a POS.** See §3.4.
- **Clocks and anything clockwise.** Material: *"Clocks still turn clockwise for
  RTL languages. A clock icon or a circular refresh or progress indicator with
  an arrow pointing clockwise should not be mirrored."*
- **Media playback controls.** Material: *"Do not mirror media playback buttons
  and the media progress indicator as they refer to the direction of the media
  being played"* — the tape's direction, not time's. Microsoft, independently:
  *"common icons such as the fast-forward and rewind icons in media players, use
  the same orientation in both LTR and RTL layouts."* Apple, independently:
  *"Video controls and timeline indicators"* do not flip. **Three vendors agree;
  there is no live disagreement here**, contrary to the usual claim that this is
  contested.
- **Charts and graphs.** Apple: *"Graphs (x– and y–axes always appear in the same
  orientation)."* Material lists *"Charts and graphs"* as non-mirroring.
- **Icons depicting real-world objects.** Material's example is a camera; Apple:
  *"In general, avoid flipping interface icons that depict real-world
  objects."* Microsoft adds physical keyboards.
- **Logos and marks.** Apple: *"Don't flip logos or universal signs and marks."*
- **Photographs and illustrations.** Apple: *"Avoid flipping images like
  photographs, illustrations, and general artwork."*
- **Machine-readable strings.** Android: URIs, SQL, and by extension **barcodes,
  SKUs and licence keys**.

**Implementation, and it is a platform rule, not advice** [HARD on each
platform]. Android's RTL guidance requires `start`/`end` in place of
`left`/`right` across the whole attribute set (`gravity`, `padding`, `margin`,
`layout_align*`, `drawable*`, `layout_to*Of`), and `android:autoMirrored="true"`
for simple drawables that should flip. The UAE government design system says the
same thing for CSS
(`https://designsystem.gov.ae/guidelines/advanced-css`, government design
system): *"Use logical properties to avoid manually flipping styles for
right-to-left interfaces"* — `padding-inline-start`, `margin-inline-end`, and so
on. W3C adds
(`https://www.w3.org/International/questions/qa-html-dir`): set `dir` once at the
document level, and **never set base direction via CSS**.

### 3.2 Arabic typography: what is actually established

**Vertical space.** Two facts, one soft and one hard.

- *Soft, and the only numbers found:* Material Design's legacy typography spec
  (`https://m1.material.io/style/typography.html`, vendor design system) places
  Arabic in a "Tall" script category and states **"line height is 0.1em larger
  than the English-like languages"** and **"font size is 1px larger than that
  specified for English"** for Title through Caption styles. Whether Material 3
  still says this could not be confirmed — the live page is a JS SPA.
- *Hard, and structural:* W3C's Arabic Layout Requirements
  (`https://www.w3.org/TR/alreq/`, standards body): *"Arabic ascenders and
  descenders extend much further than those of the Latin script, and care must
  be taken to correctly align text in the different scripts when they appear
  together."* The OpenType specification is blunter still
  (`https://learn.microsoft.com/en-us/typography/opentype/spec/recom`): for a
  script needing more vertical extent than Latin at matched visual size, *"the
  (sTypoAscender - sTypoDescender) distance for that font would likely need to
  be greater than one em."*

**A real counterexample worth knowing.** The UAE federal government design system
(`https://designsystem.gov.ae/guidelines/typography`) applies **one uniform
rule** — *"line height must indeed be 1.5x… margin after the paragraph must be
2x… letter spacing should be 0.12 of the font size"* — to Arabic and Latin
alike, stating that *"Arabic follows the same accessibility standards as English
content"*. So a real government RTL system does **not** give Arabic extra
leading. **Sources disagree**; the honest position is that Arabic needs *more
vertical room in the box* (ascender/descender extent, a structural fact), which
is not the same claim as *more leading between lines*.

**Letter-spacing must not be applied to Arabic** [HARD in effect, though
formally a convention]. MDN
(`https://developer.mozilla.org/en-US/docs/Web/CSS/letter-spacing`), under
Internationalization concerns:

> *"Some written languages should not have any letter spacing applied. For
> instance, languages that use the Arabic script expect connected letters to
> remain visually connected… Applying letter spacing may lead to the text
> looking broken."*

W3C's alreq explains the mechanism: *"The only spaces inside Arabic words are
created near characters that are not dual-joining… Moving two joined characters
closer to or further from each other creates undesirable results."* Microsoft's
Arabic shaping documentation
(`https://learn.microsoft.com/en-us/typography/script-development/arabic`)
confirms it architecturally: the `curs` feature matches *"exit point of the
current character… with entry point of the following character"* — inserted
tracking breaks that join. Note the formal status: the W3C CSS WG issue raising
this (`https://www.w3.org/International/track/issues/333`) was closed as a
documentation suggestion, **not** as a normative spec requirement.

> **Consequence for this product:** `DESIGN.md` §5 says *"Uppercase labels get a
> touch of letter-spacing"*, and the brand wordmark uses 0.16 em tracking. Both
> are correct for Latin and **must not apply to the Arabic catalog strings**. A
> single unscoped `letter-spacing` rule on a label class is a real, shippable
> defect in Arabic, and no contrast test can see it.

**Emphasis in Arabic does not work the way Latin emphasis works.** W3C's
typography gap analysis
(`https://w3c.github.io/typography/gap-analysis/arab-ar-fa`, W3C working-group
document):

> *"Bold and italic are not always appropriate for expressing emphasis, and some
> scripts have their own unique ways of doing it, that are not in the Western
> tradition at all."*
>
> *"There is not currently a way to achieve effective underlining in a way that
> works with the Arabic script."*

Arabic has no letter case, and slanting is rejected by practising Arabic type
designers — TypeDrawers
(`https://typedrawers.com/discussion/2147/where-is-arabic-italic-originating-from`,
practitioner forum with named, credentialed designers): Bahman Eslami, *"I can't
buy into the idea of slanting Arabic and also naming it italic or iranic or
whatever. It's fundamentally wrong."* Khaled Hosny notes the practical failure:
*"a word with no vertical lines like 'حب' will not stand much (if at all) when
slanted."*

> **Consequence [CONVENTION]:** in Arabic, emphasis must be carried by **weight,
> size, colour or a surface**, never by italics, capitals, underline or
> tracking. Any component whose Latin design leans on `text-transform:
> uppercase` or `italic` has **no Arabic equivalent** and needs a different
> emphasis mechanism, not a fallback.

### 3.3 Which Arabic typeface — what the foundries themselves say

`DESIGN.md` §9.7 lists choosing and bundling an Arabic face as open work. This
is what the publishers say in their own words (all fetched directly from the
publisher's own description files or site):

| Face | Publisher's own words | Fit |
|---|---|---|
| **Noto Kufi Arabic** | *"a simplified, unmodulated ('sans serif') Kufi design mainly for texts in larger font sizes"* | Display / headings. **Explicitly not** a body/UI face. |
| **Noto Naskh Arabic** | *"a modulated ('serif') Naskh design, suitable for texts in the Middle Eastern Arabic script"* | Continuous reading. |
| **Noto Sans Arabic** | *"an unmodulated ('sans serif') design for texts in the Middle Eastern Arabic script"* | The sans body option. |
| **Noto Sans / Naskh Arabic *UI*** | dedicated UI cuts exist because *"user interface elements with limited vertical space"* need them; they are *"more compact vertically and have the same line height as the basic Noto Sans fonts"* (`https://notofonts.github.io/noto-docs/website/use/`) | **The strongest signal in the table.** A foundry shipping a separate UI cut is direct evidence that Arabic vertical metrics are a real UI problem. |
| **Cairo** | *"wide open counters and short ascenders and descenders that minimize length while maintaining easy readability"* | Geometric sans, Kufi-derived. |
| **Tajawal** (Boutros) | *"a distinctive low contrast Arabic and sans serif Latin typeface family… following a modern geometric style while still respecting the calligraphy rules of the Arabic script"* | Ships a matched Latin. |
| **Almarai** (Boutros) | *"specifically developed to enhance legibility across different media and particularly on screens"* | **The only explicit screen-legibility claim found from a publisher.** |
| **Dubai Font** (Monotype, Nadine Chahine) | designed with *"regular check-ins with the designers on the team exchanging files to ensure the different scripts were fitting together"* (`https://www.monotype.com/resources/case-studies/dubai`) | A documented model for Latin/Arabic harmonisation. |

**Style convergence [CONVENTION]:** Noto's own descriptions, 29LT's product copy
and W3C's alreq all point the same way — **Kufi for display, Naskh (or a
Naskh-informed sans) for body and UI**. alreq defines Kufi as *"characterized by
angular forms, with pronounced emphasis of horizontal strokes"* and Naskh as
*"the bookhand par excellence… formed the basis for most types intended for
continuous reading."* **No source on Ruq'ah for UI or small sizes was found at
all** — a genuine gap, not an omission.

**Fetches that failed**, so nothing is claimed about them: Khatt Foundation
(`khtt.net`, 403), Arabetics (TLS error), all Google Fonts specimen pages (JS
SPAs), Monotype's Frutiger Arabic / Neue Helvetica Arabic pages (404).

**Pairing hazard** [CONVENTION]. The OpenType spec
(`https://learn.microsoft.com/en-us/typography/opentype/spec/recom`) warns that
*"glyphs from different scripts in this font may not appear correctly aligned
relative to each other when used with applications that either don't support the
BASE table or that support it but assume that a particular baseline will not
vary across scripts"*, and recommends all scripts record the same baseline
value. Mixing a Latin UI face with an unrelated Arabic face means mismatched
vertical metrics, a shifted baseline in mixed runs, and clipped ascenders —
which is exactly why Dubai Font was designed by exchanging files between script
teams rather than by fallback.

### 3.4 Numbers and money in RTL — the part that breaks tills

**Digits always run left to right, even inside Arabic** [HARD]. W3C's bidi
primer
(`https://www.w3.org/International/articles/inline-bidi-markup/uba-basics.en`,
standards body):

> *"Numbers in RTL scripts run left-to-right within the right-to-left flow, but
> they are handled by the bidi algorithm a little differently than words… the
> number is seen as part of the preceding Arabic text, so the two Arabic words
> that surround the number are treated as part of the same directional run —
> even though the sequence of digits runs LTR on screen."*

UAX #9 (`https://www.unicode.org/reports/tr9/`) gives the machinery: digits are
bidi class **EN** (European Number) or **AN** (Arabic Number); rule W2 converts
EN to AN after a strong Arabic letter; and resolved levels are such that
*"right-to-left text will always end up with an odd level, and left-to-right and
numeric text will always end up with an even level."* Even level means LTR.
W3C's alreq §6.1.2 states the classification directly: *"European digits and
Eastern Arabic-Indic digits are of category EN (European Number). Arabic-Indic
digits are of category AN (Arabic Number)."*

> **Consequence [HARD]:** `12.500` is `12.500` in Arabic. Never reverse it,
> never right-align the digits of a single number, never "mirror" a total. Apple
> states the rule in one line: *"Don't reverse the order of numerals in a
> specific number."*

**The Jordanian dinar has three decimal places, and this is a standards fact,
not a convention** [HARD]. Confirmed from the ISO 4217 maintenance agency's own
published table (`https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml`,
standards body, table dated 2026-01-01):

> `<CcyNm>Jordanian Dinar</CcyNm><Ccy>JOD</Ccy><CcyNbr>400</CcyNbr><CcyMnrUnts>3</CcyMnrUnts>`

Independently confirmed twice in Unicode CLDR, in two different file formats
(`common/supplemental/supplementalData.xml` and
`cldr-json/cldr-core/supplemental/currencyData.json`):

> `<info iso4217="JOD" digits="3" rounding="0"/>`

Kuwait (KWD) and Bahrain (BHD) are the other two three-decimal dinars.

> **Consequence:** a money field that renders two decimals, rounds to two
> decimals, or formats with a generic two-decimal currency helper is **wrong in
> Jordan**, and silently loses fils. Any test that asserts a money string must
> assert three decimals.

**Where the currency sign goes in Arabic.** CLDR's resolved data for **ar-JO**
(`https://raw.githubusercontent.com/unicode-org/cldr-json/main/cldr-json/cldr-numbers-full/main/ar-JO/numbers.json`,
standards body) gives the standard currency pattern as:

> `"standard": "‏#,##0.00 ¤;‏-#,##0.00 ¤"`

Two things are encoded there, both important:

1. **The currency sign (`¤`) comes *after* the number**, separated by a space —
   not before it as in English `JD 12.500`.
2. **The pattern begins with an invisible RLM (U+200F)**. CLDR itself baked a
   right-to-left mark into the pattern, which is the standards body's own
   mitigation for the isolation hazard described in §3.6. If CLDR thought this
   needed protecting, so should we.

Note a nuance, flagged as a synthesis rather than a single quote: the pattern's
literal template shows two decimal placeholders (`0.00`), while the fraction
count for JOD is overridden separately by the `digits="3"` entry above.
Software combining both CLDR facts correctly renders **three** decimals.

**Regional placement is not uniform** — a weaker finding, reported because it
warns against over-generalising: a Qatar (ar-QA) pattern was reported (via a
search summary, **not** independently fetched, so **WEAK**) to place the symbol
*before* the number. Do not assume one Gulf/Levant rule.

### 3.5 Arabic-Indic vs Western digits in Jordan — the standards say one thing and Jordan does another

This is the single most decision-relevant finding in this document, and it is a
genuine, unresolved conflict between a standards body and observed practice.

**What the standard says** [HARD, as a fact about CLDR]. Unicode CLDR's locale
file for Jordan
(`https://raw.githubusercontent.com/unicode-org/cldr/main/common/main/ar_JO.xml`,
verified independently more than once):

> `<defaultNumberingSystem>arab</defaultNumberingSystem>`

`arab` means Arabic-Indic digits — ٠١٢٣٤٥٦٧٨٩. Note that the base `ar` locale
(no region) defaults to `latn` (Western digits); **Jordan is an explicit
override to Arabic-Indic in CLDR.**

**What Jordan actually does.** Two live Jordanian government sites, both entirely
in Arabic prose, both fetched directly:

- **Central Bank of Jordan**, `https://www.cbj.gov.jo` — dates rendered
  `2026/09/01`, `2026/08/19`; figures `13.3%`, `24.9%`, `2.13`, `2.93`; exchange
  rates `708`, `710`, `822.27–826.51`; reserves `28379.5`. **All Western digits.
  No Arabic-Indic digits anywhere.**
- **Income and Sales Tax Department (ISTD)**, `https://www.istd.gov.jo` — the
  authority behind JoFotara, which this product integrates with. Arabic prose
  with Western digits throughout: *"موازنة دائرة ضريبة الدخل والمبيعات 2026"*,
  *"6 مليار دينار"*, *"25 ألف دينار"*.

**What the authoritative typographic source says about Jordan: nothing.** W3C's
alreq (`https://www.w3.org/TR/alreq/`) classifies regions explicitly — *"European
Numerals are used with Western Arabic-speaking countries; e.g. Algeria or
Morocco"*, *"Arabic-Indic Numerals (٠١٢٣٤٥٦٧٨٩) are used in Eastern
Arabic-speaking countries; e.g. Egypt, Saudi Arabia, Iraq"* — and **does not
name Jordan or the Levant in either bucket.** That is a gap in the best
available source, not an oversight in the search.

**Apple acknowledges the ambiguity** rather than resolving it (HIG "Right to
left", via proxy): Arabic *"may use either Western or Eastern Arabic numerals
depending on region."*

> **Conclusion, stated as a judgement with its evidence [CONVENTION]:** Western
> digits on money in a Jordanian POS is **defensible and well-supported** — the
> country's own central bank and tax authority both use them in Arabic-language
> official content, and the tax authority is this product's e-invoicing
> counterparty. `DESIGN.md` §9.7's "Western digits on money is the current rule"
> is therefore the right call, but it should be recorded as *contradicting the
> CLDR default for ar-JO*, because any developer who later reaches for a
> standard locale formatter will get Arabic-Indic digits and will think our code
> is the bug.
>
> **What was NOT established:** what a Jordanian *shopper* prefers on a price tag
> or a printed receipt, as opposed to what a government website publishes. No
> source for that was found. Do not present the government-site evidence as
> evidence about consumers.

### 3.6 Bidi hazards you will actually hit

W3C's worked examples
(`https://www.w3.org/International/articles/inline-bidi-markup/bidi_examples.html`
and `https://www.w3.org/International/questions/qa-bidi-unicode-controls`,
standards body) cover, almost line for line, the cases a POS produces:

- **A Latin code inside an Arabic name.** Their example is an Arabic book title
  containing "C++": `<span dir="rtl">AN INTRODUCTION TO <span dir="ltr">c++</span></span>`
  — the inner wrap exists *"to prevent the bidi algorithm from misplacing the
  plus signs."* Substitute a SKU, a barcode, a model number, an IMEI.
- **Punctuation jumping to the wrong side.** Without an explicit direction on the
  phrase, a trailing `!`, `?`, `:` or `,` is governed by the *paragraph's*
  direction, not the phrase's, and lands on the wrong end. W3C's fix for a
  quoted Arabic string inside English is an RLM after the closing punctuation.
- **A trailing number after RTL text** — their "PURPLE PIZZA" + "5 reviews"
  example. Structurally identical to a product name followed by a price or a
  quantity badge.
- **Phone numbers** — `<span dir="ltr">(012) 345 6789</span>`; the parentheses
  reorder without it.
- **Colon-punctuated identifiers** — their MAC-address example
  `<span dir="ltr">01:02:aa:4a:bb:06</span>`; same class as a time, a reference
  number, or a licence key.
- **`dir="auto"` misdetection** (`https://www.w3.org/International/questions/qa-html-dir`):
  a paragraph starting with an Arabic character is detected RTL, and a trailing
  English word lands on the wrong side.

**The fixes, in order of preference** [HARD, these are the specified mechanisms]:

1. **`<bdi>`** for any runtime-inserted string of unknown direction. MDN: it
   *"tells the browser's bidirectional algorithm to treat text in isolation from
   its surrounding text"*; W3C: *"Without a `dir` attribute, the `bdi` element
   behaves as if `dir=\"auto\"` had been applied."*
2. **`dir="ltr"` / `dir="rtl"` on a tight wrapper** where the direction is known.
3. **`unicode-bidi: isolate`** in CSS. MDN's own stated use case for this value
   is, verbatim, ***"embedding a left-to-right product code inside an Arabic
   (right-to-left) paragraph"*** — which is this product's exact case.
4. **Unicode isolates (FSI U+2068, LRI U+2066, RLI U+2067, PDI U+2069)** where
   markup is unavailable: a `title` attribute, plain text, a printed receipt, a
   WhatsApp message. W3C is explicit that isolates are preferred over the older
   embedding controls: *"use RLI and LRI, and avoid using RLE and LRE"*, because
   *"you really want to avoid what's inside the boundaries interacting with what's
   outside."*
5. On Android, `BidiFormatter.unicodeWrap()`. Android's guidance: *"Apply
   `unicodeWrap()` to every piece of text inserted into a localized message"*
   except machine-readable strings.

> **Consequence for this product:** every place where shop-typed data (a product
> name, a customer name, a payment method, a category) is interpolated into a
> sentence, and every place a Latin SKU or a price sits beside Arabic text,
> needs isolation. `DESIGN.md` already notes that shop-typed names *"are data
> and are shown as typed"* — that is exactly the unknown-direction case `<bdi>`
> exists for.

### 3.7 What could not be established about Arabic/RTL

Listed rather than filled in:

- **No W3C or ISO document on RTL UI component design exists** (§3.0).
- **No Ruq'ah-for-UI guidance** was found anywhere.
- **No date/time-specific bidi guidance** as a distinct category; only the
  general neutral/numeral absorption mechanism applies.
- **No CSS-level guidance** on Arabic/Latin vertical-metric matching
  (`size-adjust`, `ascent-override`); only the OpenType spec addresses it.
- **Saudi Arabia's government design system** (dga.gov.sa) exists but returned
  403 on every attempt; a third-party Vercel-hosted "DGA registry" is **not** a
  government publication and must not be cited as one. **No Qatar government
  design system was found at all.** The **UAE's** (`designsystem.gov.ae`) is
  real and substantive on typography and CSS logical properties, but publishes
  **no icon-mirroring table** — checked directly.
- **No usable RTL design case study** for Careem, Talabat, HyperPay, Tap
  Payments, Arab Bank or Capital Bank. The one Careem "case study" found
  self-disclaims its own data as *"not accurate and… used for demonstrative
  purposes."*
- **What Jordanian consumers expect on receipts and price tags** (§3.5).

---

## 4. Colour and theming

### 4.1 Semantic roles, not literal colours — four design systems agree

| System | The statement | URL (all fetched, JS-rendered ones via proxy) |
|---|---|---|
| **Material 3** | *"Color roles are like the 'numbers' in a paint-by-number canvas. They're the connective tissue between elements of the UI and what color goes where."* Roles beginning `on-` *"indicate a color for text or icons on top of its paired parent color."* Pairs *"provide an accessible minimum 3:1 contrast."* | `https://m3.material.io/styles/color/roles` |
| **IBM Carbon** | *"Tokens are a method of applying color in a consistent, reusable, and scalable way. They are used in place of hard coded values, like hex codes."* | `https://carbondesignsystem.com/elements/color/tokens/` |
| **Atlassian** | *"Each color design token maps to a different value for each theme so their appearance differs depending on which theme is being used."* | `https://atlassian.design/foundations/color-new` |
| **GitHub Primer** | Base tokens *"don't respect color modes and should never be used directly in code or design"*; *"When `bgColor-default` is referenced for a background color, the value of that token will automatically change depending on the color mode."* | `https://primer.style/foundations/color` |

**Adobe Spectrum is the most useful of the four for a five-theme product**
(`https://spectrum.adobe.com/page/color-system/`, vendor design system), because
it is the only one that documents multiple themes as a solved engineering
problem rather than a styling choice:

> *"Spectrum assigns generic meanings to a subset of colors in order to set
> consistent expectations for users"* — informative, accent, negative, notice,
> positive.
>
> *"Dark themes target higher contrast ratios to provide appropriate and
> perceptually consistent contrast when compared to the light theme."*
>
> *"Colors 400 through 600 can be used for nonessential UI decoration… They
> should never be used to communicate essential information to users."*
>
> *"Static colors have consistent color values across all themes and are not
> based on contrast with the background color of the theme."*

And the mechanism, with numbers: *"Spectrum's color themes are generated using
target contrast ratios with a specified background color."* Their worked example
for a single token, Blue-900, is a **target contrast of 5.07 in the light theme,
6.02 in dark, and 6.91 in darkest** — same token name, three different solved
RGB values, each independently tuned to its own theme's target.

> **Consequence [CONVENTION], and this is the strongest single validation of the
> approach in `DESIGN.md` §4.1:** the pattern all five mature systems converge on
> is (1) primitives per theme, (2) a fixed set of semantic role names every
> screen references, (3) a theme is exactly one full assignment of values to
> those names, and (4) **no component logic changes between themes — only
> values.** `DESIGN.md`'s "a theme is a block of token values only; there is no
> rule anywhere that applies to one theme and not another" is the same rule, and
> Spectrum's higher contrast *targets* for darker themes is the one refinement
> worth importing: a flat 4.5:1 floor applied identically to all five is weaker
> than Spectrum's escalating targets.
>
> Spectrum's `--focus-ring-halo`-shaped concept — a **static** colour that
> deliberately does not follow the theme — is also named and legitimised there.
> That is not an exception to the rule; it is a documented token category.

### 4.2 Never colour alone [HARD]

**WCAG 2.2 SC 1.4.1 Use of Color, Level A**
(`https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html`):

> *"Color is not used as the only visual means of conveying information,
> indicating an action, prompting a response, or distinguishing a visual
> element."*

The Understanding document adds a useful nuance most people miss: *"If content
is conveyed through the use of colors that differ not only in their hue, but
that also have a significant difference in lightness, then this counts as an
additional visual distinction"* when contrast reaches 3:1 or more. So a
sufficiently large **lightness** difference can itself be the second cue — but
*"if content relies on the user's ability to accurately perceive or differentiate
a particular color an additional visual indicator will be required regardless of
the contrast ratio."*

**Prevalence.** The US National Eye Institute
(`https://www.nei.nih.gov/learn-about-eye-health/eye-conditions-and-diseases/color-blindness`,
medical/government source): *"About 1 in 12 men have color vision deficiency"*
and *"Men have a much higher risk than women."* NEI gives **no** figure for women
and no ethnicity-specific figure. The commonly-cited "≈8 % of men and ≈0.5 % of
women of Northern European descent" appears in the US Web Design System
(`https://designsystem.digital.gov/design-tokens/color/overview/`): *"Approximately
0.5% of adult women and 8% of adult men have some kind of color insensitivity,
especially between red and green."* The 8 % figure is corroborated by NEI
(1 in 12 ≈ 8.3 %); the 0.5 % women figure rests on USWDS alone.

USWDS also gives the best procedural rule found:

> *"Start with your core message and use type scale and hierarchy to test and
> refine its effectiveness. Then, introduce color to support that message."*
>
> *"Color should only be used as progressive enhancement — if color is the only
> signal, that signal won't get through as intended to everyone."*

### 4.3 Money signals: red/green is the worst possible pair

Datawrapper (`https://blog.datawrapper.de/colorblindness-part2/`, practitioner
data-visualisation writing, well regarded and not SEO filler):

> *"Just be aware that for [colourblind readers], red and green won't look like
> an indicator for something good and bad, but like a darker olive/orange and a
> lighter one."*

Their recommended remedies, all quoted: vary by **lightness** (works even printed
in black and white); use **symbols** in tables rather than colour-only cells;
*"the safest choice is to mix blue with orange or red"*; add **patterns**; and
**label directly** instead of relying on a colour legend.

> **Consequence [HARD + CONVENTION]:** money-in / money-out, positive / negative
> variance, paid / overdue must each carry a **non-colour** cue — a sign, a
> parenthesis, an arrow, a word, or a large lightness step. `DESIGN.md`'s
> `--text-money-positive` / `--text-money-negative` are a green/red pair and are
> therefore **exactly the pair the research warns about**. Their AAA 7:1 contrast
> against the surface satisfies SC 1.4.3, but SC 1.4.1 is a *separate* criterion
> and is not satisfied by contrast. A screen showing a variance only as a
> coloured number is non-conforming at Level A. A sign (`+` / `−`), an
> accounting parenthesis, or an explicit "in"/"out" label fixes it.
>
> **This is the cheapest real defect in the document to introduce and the
> cheapest to catch.** It should be a line in the checklist and, ideally, a test.

### 4.4 Dark themes: the evidence is not what the fashion says

**The vendor guidance, with its numbers** (Material Design,
`https://m2.material.io/design/color/dark-theme.html` — the M2 page is still
live and `material.io/design/color/dark-theme.html` redirects to it; vendor
design system):

- *"Use dark grey – rather than black – to express elevation and space in an
  environment with a wider range of depth."*
- ***"The recommended dark theme surface color is #121212."***
- *"A dark theme should avoid using saturated colors, as they don't pass WCAG's
  accessibility standard of at least 4.5:1 for body text against dark
  surfaces."*
- *"Don't use bright colors for large surfaces because they can emit too much
  brightness."*
- Elevation is expressed as a **lighter overlay, not a shadow**: *"Elevation
  overlay transparencies range from 0% for the lowest level to 16% for the
  highest level"* — 0 dp = 0 %, 1 dp = 5 %, 2 dp = 7 %, 3 dp = 8 %, 4 dp = 9 %,
  6 dp = 11 %, 8 dp = 12 %, 12 dp = 14 %, 16 dp = 15 %, 24 dp = 16 %.

Apple (`https://developer.apple.com/design/human-interface-guidelines/dark-mode`,
via proxy) sets a stricter bar than WCAG AA for dark surfaces: *"At a minimum,
make sure the contrast ratio between colors is no lower than 4.5:1. For custom
foreground and background colors, strive for a contrast ratio of 7:1, especially
in small text."* Apple also frames dark mode purely as **user preference**, not
as a health intervention.

**The research says light-on-dark reads worse, and this is the genuinely
under-cited finding.** Four peer-reviewed sources, citations verified:

1. **Buchner, A. & Baumgartner, N. (2007)**, *"Text – background polarity affects
   performance irrespective of ambient illumination and colour contrast"*,
   *Ergonomics*, DOI `10.1080/00140130701306413`. Abstract, verbatim: *"In a
   series of experiments, proofreading performance was consistently better with
   positive polarity (dark text on light background) than with negative polarity
   displays (light text on dark background)."* The advantage held *"irrespective
   of ambient illumination and colour contrast"*.
2. **Piepenbrock, C., Mayr, S., Mund, I. & Buchner, A. (2013)**, *"Positive
   display polarity is advantageous for both younger and older adults"*,
   *Ergonomics*, DOI `10.1080/00140139.2013.790485`. Verbatim: *"A positive
   polarity advantage was found for both age groups. The presentation in positive
   polarity is recommended for all ages."*
3. **Piepenbrock, Mayr & Buchner (2014)**, *"Positive Display Polarity Is
   Particularly Advantageous for Small Character Sizes"*, *Human Factors*, DOI
   `10.1177/0018720813515509`. Citation verified via CrossRef; abstract not
   independently retrieved, so treat the content as **moderately** sourced. The
   title is the finding.
4. **Taptagaporn, S. & Saito, S. (1990)**, *"How display polarity and lighting
   conditions affect the pupil size of VDT operators"*, *Ergonomics*, DOI
   `10.1080/00140139008927110`. Verbatim: *"CRTs using a positive display
   polarity (dark characters on bright background) are ergonomically more
   appropriate for VDT operators than ones using a negative display polarity."*
   Mechanism: light background → smaller pupil → less optical aberration →
   sharper focus.

Plus Dobres et al. 2017 (§2.4), which ties polarity to **ambient light**
specifically.

NN/g's synthesis (`https://www.nngroup.com/articles/dark-mode/`, practitioner
writing summarising the above): *"light mode won across all dimensions"* for
visual-acuity tasks, and *"the positive-polarity advantage increased linearly as
the font size was decreased."* NN/g also records the important counter-case:
*"some people with visual impairments will do better with dark mode"* —
specifically people with cloudy ocular media, for whom a bright screen scatters.

> **Sources disagree, explicitly.** Material asserts, as design rationale, that
> *"Dark grey surfaces also reduce eye strain"*. That is a **vendor assertion
> with no cited study**. The peer-reviewed polarity literature finds the
> opposite for reading performance in normal and bright light. **Report both.
> Do not silently pick one.**
>
> **Consequence for this product [CONVENTION]:** five themes are a legitimate
> product feature and there is no reason to remove the dark ones — a subset of
> users genuinely read better on them, and a dim stockroom is a real case. But
> the **default** for a bright shop counter should be a light theme, and a dark
> theme must be *tested under real counter light*, not on a dim monitor. This is
> also the strongest available justification for `DESIGN.md`'s rule against
> `#000000` grounds and `#FFFFFF` text: halation on a cheap panel is the
> negative-polarity failure mode these studies measure.

### 4.5 Keeping five themes legible — the engineering pattern

From Spectrum (§4.1), Primer and Atlassian, the pattern is consistent:

1. **Three token tiers.** Primer: base (raw values, *"never used directly"*) →
   functional/semantic → component. A screen references only the semantic tier.
2. **Per-theme contrast *targets*, not a single global floor.** Spectrum's
   5.07 / 6.02 / 6.91 for one token across three themes.
3. **Explicit numeric guarantees between token steps.** Primer: *"Step 9 is
   considered the minimum contrast value for text against steps 0 through 4,
   while 10 meets the minimum against 5 and 6"*, and for their high-contrast
   theme, *"The goal is to hit a minimum of 7:1 for most text and interactive
   elements."*
4. **Decorative colours are named as decorative and forbidden from carrying
   meaning** (Spectrum's 400–600 rule).

Whether any of these systems runs contrast checks in CI could **not** be
confirmed from their published pages — the architecture implies it, but the
claim is not sourced.

> **What no source covers, and it is the important gap:** every one of these
> systems verifies *contrast*. None of them publishes a method for verifying
> that five themes remain *distinguishable from one another*, or that a solved
> colour does not read as "disabled" or as the wrong brand register. Those are
> real failure modes — `DESIGN.md` §4.3 documents three of them shipping past a
> green contrast suite — and the literature offers nothing. **Rendering the
> screen and looking at it remains the only known control**, which is a gap in
> the state of the art, not a gap in our process.

---

## 5. Iconography

### 5.1 Icons are less legible than designers believe

NN/g, `https://www.nngroup.com/articles/icon-usability/` (Aurora Harley,
27 July 2014), practitioner usability research:

> *"There are a few icons that enjoy mostly universal recognition from users. The
> icons for home, print, and the magnifying glass for search are such
> instances."*
>
> *"Outside of these examples, most icons continue to be ambiguous to users due
> to their association with different meanings across various interfaces."*
>
> ***"A text label must be present alongside an icon to clarify its meaning in
> that particular context."***
>
> *"Icon labels should be visible at all times, without any interaction from the
> user."*

They report a clock icon used for "history" that *"confused all test
participants: Not a single test participant clicked this icon."*

The quantified companion finding, NN/g
`https://www.nngroup.com/articles/hamburger-menus/`: hidden navigation was
engaged **only 27 % of the time versus 48–50 %** for visible or combined
options; desktop task completion slowed by **at least 39 %**; content
discoverability fell **over 20 %**; perceived difficulty rose **21 %**. Their
conclusion: *"Do not use hidden navigation (such as hamburger icons) in desktop
user interfaces."*

> **[CONVENTION], strongly evidenced.** Icon-only is acceptable for the three
> genuinely universal icons and for controls a cashier uses hundreds of times a
> day (where recognition is learned rather than inferred). Everything else —
> and in particular anything on the money path or in the back office, which
> owners use *slowly and rarely* — needs a visible label. "The icon is obvious"
> is the claim this research exists to refuse.

**Material 3 says the same thing with a size threshold**
(`https://m3.material.io/styles/icons/applying-icons`, via proxy): *"Other
symbols should have an accompanying text label below 20dp to ensure their
meaning is clear"*, and navigation items *"must have labels for clarity and
accessibility."*

**No academic icon-recognition study with usable recognition-rate percentages was
found.** ISO 9186 (graphical symbol comprehension testing) exists but iso.org
returned 403. Treat any specific "X % of users recognise icon Y" claim as
unsourced.

### 5.2 An icon-only control still needs a name [HARD]

**WCAG SC 4.1.2 Name, Role, Value, Level A**
(`https://www.w3.org/WAI/WCAG21/Understanding/name-role-value.html`): *"For all
user interface components… the name and role can be programmatically
determined."* Its failure examples explicitly include an icon-only link with no
accessible name.

**SC 2.5.3 Label in Name, Level A** applies only when a visible *text* label
exists — *"where a visible text label does not exist for a component, this
success criterion does not apply to that component."* The two criteria divide
cleanly: 4.1.2 requires icon-only controls to have a name at all; 2.5.3 requires
icon+label controls to have the *visible words* inside that name, so that a
voice-control user saying what they can see actually activates the control.

USWDS's implementation pattern
(`https://designsystem.digital.gov/components/icon/#accessibility`, government
design system): decorative icon SVGs get `aria-hidden="true" focusable="false"`;
icon-only buttons get an `aria-label`; and where an icon accompanies visible
text, the icon is `aria-hidden` so the name is not announced twice.

> **Bilingual consequence [HARD]:** SC 2.5.3 must hold **in each language**. An
> Arabic button labelled "دفع" whose `aria-label` is the English "Pay" fails it.
> Accessible names must come from the same catalog as the visible string, not be
> hard-coded.

### 5.3 Geometry and coherence — the numbers

| System | Grid / live area | Stroke | Source |
|---|---|---|---|
| **Material (legacy M1)** | 24 dp total, **20 × 20 dp live area with 4 dp padding**; dense: 16 × 16 live in 20 dp. Keylines: square 18 dp, circle 20 dp diameter, rectangles 20 × 16 dp | *"Maintain a 2dp width for all stroke instances… optical corrections allow 1.5dp strokes"*; 2 dp exterior corner radius, square interior corners | `https://m1.material.io/style/icons.html` |
| **Material Symbols (current)** | Optical sizes **20, 24, 40, 48 dp** | Variable **weight axis 100–700**; *"the minimum weight for this size [24dp] should be 200"*; also `fill` (0/1) and `grade` axes | `https://developers.google.com/fonts/docs/material_symbols` and `https://m3.material.io/styles/icons/applying-icons` |
| **Apple SF Symbols** | Three scales (small / medium / large) | **Nine weights**, ultralight to black, each corresponding *"to a weight of the San Francisco system font, helping you achieve precise weight matching between symbols and adjacent text"* | `https://developer.apple.com/design/human-interface-guidelines/sf-symbols` (via proxy) |
| **Feather** (which Lucide, and therefore AuraIcons, derives from) | *"Each icon is designed on a 24x24 grid"* | `stroke-width: 2`, `stroke-linecap: round`, `stroke-linejoin: round`, `fill: none`, `stroke="currentColor"` | `https://raw.githubusercontent.com/feathericons/feather/master/README.md` |

**Optical size is a real, vendor-recognised problem, not folklore.** The
strongest evidence is structural: Material Symbols ships a dedicated **optical
size axis spanning 20–48 dp** whose stated purpose is to adjust stroke weight as
the symbol size changes, and Apple ships nine weights explicitly matched to text
weights. Two major vendors built variable axes for this; that is stronger
evidence than any single article.

> **[CONVENTION]:** a 16 px icon needs a proportionally heavier stroke than a
> 48 px one to read at the same optical weight. A fixed 2 px stroke across every
> size — which is what a straight Lucide port gives you — is *coherent* but
> **thin at small sizes and heavy at large ones**. Material's own floor for a
> 24 dp symbol is weight 200, above its default 400 only in the sense that it
> forbids the lightest cuts.

**Icon size vs touch target** [CONVENTION], Material 3, verbatim: *"Symbols of
24dp should have a target size of 48dp by default"*, and *"A 20dp size symbol
can use a target size of 40dp"* for denser desktop layouts. The icon is not the
target; the padding around it is.

**No verified grid/stroke spec could be obtained for IBM Carbon, Phosphor or
Lucide** — 404s, JS shells, or READMEs that omit the numbers. Lucide is commonly
described as 24 × 24 / 2 px (which matches its Feather ancestry) but this was not
confirmed from Lucide's own documentation.

### 5.4 Cultural and RTL icon legibility

RTL mirroring of icons is covered in §3.1 and the vendor sources agree.

**Cultural icon legibility is the weakest-sourced topic in this document.** No
primary localization source was found for the familiar claims — that a piggy
bank, a US-style mailbox, a thumbs-up, an owl for wisdom, hand gestures, or
pig/alcohol/dog imagery translate badly in a Muslim-majority market. Searches
returned only listicles. **These are treated here as [TASTE] backed by general
cultural knowledge, not as cited findings**, and a review must not cite this
document as evidence for them.

Likewise, **no peer-reviewed or primary source could be found for the common
claim that green carries positive religious weight and red negative weight in
Arabic-speaking markets.** Three real cross-cultural colour-marketing papers were
located and checked — Aslam (2006), *Journal of Marketing Communications*, DOI
`10.1080/13527260500247827`; Madden, Hewett & Roth (2000), *Journal of
International Marketing*, DOI `10.1509/jimk.8.4.90.19795`; Jacobs et al. (1991),
*International Marketing Review*, DOI `10.1108/02651339110137279` — and **none
of their accessible abstracts covers a Middle Eastern country.** Treat the
green/red cultural claim as folk knowledge pending a real source.

---

## 6. Brand identity for a small commercial software product

### 6.1 The mark must survive 16 px, and that is a design constraint, not an export setting

Required sizes, from primary sources:

- **Favicon and web** — MDN
  (`https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/link`):
  `apple-touch-icon` sizes in use are 120 × 120 (2× iPhone), 152 × 152
  (non-Retina iPad), 167 × 167 (iPad Pro), **180 × 180** (3× iPhone). MDN's own
  advice: *"Usually, it is sufficient to provide a large image, such as 192x192,
  and let the browser scale it down as needed."*
- **Web app manifest** — web.dev (`https://web.dev/articles/add-manifest`):
  *"For Chromium, you must provide at least a 192x192 pixel icon and a 512x512
  pixel icon."* Corroborated by MDN's manifest page.
- **Maskable safe zone** — this one is a **[HARD]** spec number. W3C Web App
  Manifest (`https://www.w3.org/TR/appmanifest/`): *"The safe zone is the area
  within a maskable icon which is guaranteed to always be visible… defined as a
  circle with center point in the center of the icon and with a radius of 2/5
  (40%) of the icon size."*
- **Practitioner consolidation** — Evil Martians
  (`https://evilmartians.com/chronicles/how-to-favicon-in-2021-six-files-that-fit-most-needs`,
  practitioner writing, well regarded): a 32 × 32 `favicon.ico`, a scalable
  `icon.svg`, a 180 × 180 `apple-touch-icon.png`, 192 and 512 manifest icons,
  and a 512 × 512 maskable with *"a central circle of 409×409"* — which is
  409/512 ≈ 80 %, i.e. the same 40 %-radius circle W3C specifies.

**Android adaptive icons — and a correction worth carrying**
(`https://developer.android.com/develop/ui/views/launch/icon_design_adaptive`,
platform documentation, **[HARD]** on Android): the canvas is **108 × 108 dp**,
with *"the outer 18dp on each of the four sides… reserved for masking"*, so
**the inner 66 × 66 dp appears within the masked viewport**. Logo content should
be **48 × 48 dp minimum, 66 × 66 dp maximum**. Layers: foreground, background,
and an optional **monochrome** layer for Android 13+ themed icons.

> Note: **66 dp, not 72 dp.** The 72 dp figure circulates widely in secondary
> sources; the live Android documentation says 66. This was checked directly.

**Windows** (`https://learn.microsoft.com/en-us/windows/apps/design/style/iconography/app-icon-design`,
platform documentation): *"Microsoft aligns its icons to a 48x48 grid."*
*"When rounded corners are applied to an exterior curve, use a 2px radius at
48x48. When rounded corners are applied to an interior curve, use a 1px radius
instead."* Contrast rule: *"Make sure at least half of your icon passes a 3.0:1
contrast ratio on light and dark theme."* Windows 11 *"no longer requires high
contrast assets for app icons"*, though high-contrast variants must be black and
white with gradients avoided. Microsoft also caps metaphor complexity: no more
than two metaphors per icon.

**Apple** (`https://developer.apple.com/design/human-interface-guidelines/app-icons`,
via proxy): layout size **1024 × 1024 px** for iOS/iPadOS/macOS/visionOS.
*"Embrace simplicity in your icon design. Simple icons tend to be easiest for
people to understand and recognize."* On text: *"Include text only when it's
essential to your experience or brand"* — it creates localization problems and
becomes illegible small. Apple now expects **six appearance variants** (default,
dark, clear light, clear dark, tinted light, tinted dark) with consistent visual
features across all of them.

> **[CONVENTION], and it follows from the numbers rather than from a quoted
> rule:** at 16 × 16 and 32 × 32 there is no room for letterforms, so a wordmark
> cannot be the favicon — a monogram or a single distinctive glyph is the only
> thing that survives. No source was found that states this outright; it is
> inferred from the sizes, and is labelled as an inference.

### 6.2 Monochrome reduction is a hard printing constraint, not an aesthetic option

**Thermal receipt printing is one bit per dot.** Confirmed from Epson's own
ESC/POS command reference for `GS v 0` (print raster bit image),
`https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/gs_lv_0.html`
(vendor documentation, retrieved via text-extraction proxy):

> *"Data (d) specifies a bit printed to 1 and not printed to 0."*

There is **no greyscale**. Every pixel of the mark is on or off.

**Paper and print widths.** From Epson's own specification sheet for the
OmniLink TM-T88VII (manufacturer datasheet, retrieved via proxy): printable width
**79.5 ± 0.5 mm** in 80 mm mode and **57.5 ± 0.5 mm** in 58 mm mode; **180 dpi**
resolution; **42/56 character columns** at 80 mm and **30/40** at 58 mm for
Font A/B.

**Resolution is not a single number — do not hardcode it.** Epson's own
higher-end engines run **180 dpi**; the very common cheaper 58 mm mechanisms run
**203 dpi (8 dots/mm)**, a figure that appears widely but was only obtainable
from reseller listings and content-marketing pages (**WEAK**). The commonly-cited
"384 dots per line at 58 mm / 576 at 80 mm" figures could **not** be confirmed
from any directly fetched manufacturer datasheet.

> **Consequence [HARD]:** a receipt mark must be authored as a **one-bit,
> flat-ink** asset with no gradients, no hairlines thinner than one dot at the
> lowest supported density, and no reliance on tone. `DESIGN.md` already ships
> `aura-mark-1bit.svg` for exactly this, which is correct. What is *not*
> established is the dots-per-line figure to target — query the driver or the
> model rather than assuming.

### 6.3 A mark that coexists with five themes

The published rules are thinner than one would hope. What was verified:

- **GitHub** (`https://brand.github.com/foundations/logo`, vendor brand
  guidelines): *"The Invertocat and our wordmark should only appear in white,
  black, or in few cases grey or green."* Logos *"should be legible and pass
  accessibility requirements in all settings"*; when uncertain, *"use the highest
  contrast option."* No numeric minimum size surfaced.
- **Stripe** (`https://stripe.com/newsroom/brand-assets`): *"The Stripe wordmark
  is available in three colors. Use slate and blurple on light backgrounds, and
  white on dark backgrounds."*
- **Microsoft** (§6.1) is the most useful, because it treats the problem as
  systematic rather than as a swap: *"It is difficult to make an icon 100%
  accessible on every background… you have the option to provide separate light
  and dark theme assets"*, with the explicit 3.0:1 target.
- **Android's `<monochrome>` adaptive-icon layer** is the platform's own
  mechanism for a single-tint mark under a user-chosen theme — direct evidence
  that "the mark needs a flat monochrome variant" is a platform expectation, not
  a preference.

**Fetches that failed**, so nothing is claimed: Mozilla/Firefox, IBM, Atlassian
and Shopify brand guideline pages all returned JS shells, 404s or login walls. No
verified minimum-size or clear-space *number* from a major brand system was
obtained.

> **[TASTE], flagged as such:** clear-space and minimum-size rules of the kind in
> `DESIGN.md` §3 (one ring-stroke of clear space; mark ≥ 16 px, lockup ≥ 120 px
> wide) are entirely conventional and defensible, but this research found **no
> published external number to validate them against.** They are our rules, not
> the industry's.
>
> **[CONVENTION], supported:** the mark should not track the theme accent. WCAG's
> logotype exception (§2.1) means it has no contrast obligation, but GitHub's
> *"use the highest contrast option"*, Stripe's two-asset swap and Microsoft's
> separate light/dark assets all point at the same pattern: **a small fixed set
> of mark variants (full colour, on-light, on-dark, one-bit), chosen by surface
> lightness — not a mark that is re-tinted per theme.** A logo that changes hue
> across five themes stops being a logo.

---

## 7. The checklist

Walk a screen (or a screenshot) against this. **Every item is phrased so the
answer is yes or no.** "It looks fine" is not an answer to any of them.

Each item carries its tier and the section that justifies it. A **[HARD]** "no"
is a defect. A **[CONVENTION]** "no" needs a written reason. A **[TASTE]** "no"
is a conversation.

### A. Money correctness — check these first, they are the expensive ones

1. **[HARD §3.4]** Does every displayed amount show **exactly three decimal
   places**? (JOD `CcyMnrUnts` = 3.)
2. **[HARD §3.4]** Does every amount round and store to three decimals rather
   than two?
3. **[CONVENTION §2.5]** Are all money figures rendered with **tabular, lining
   figures** (`font-variant-numeric: tabular-nums lining-nums`)?
4. **[CONVENTION §2.5]** In any column of amounts, do the decimal points line up
   vertically when the integer parts differ in width (e.g. `9.500` above
   `1234.500`)?
5. **[HARD §4.2/§4.3]** Does every positive/negative, in/out, paid/overdue
   signal carry a **non-colour** cue — a sign, a parenthesis, an arrow, or a
   word — in addition to colour?
6. **[HARD §3.4]** In Arabic, does every number render with its digits in the
   same order as in English (`12.500`, never reversed)?
7. **[HARD §3.5]** Do all amounts use the **same digit system** throughout the
   screen (no mix of `12.500` and `١٢.٥٠٠`)?
8. **[CONVENTION §3.4]** In Arabic, is the currency marker placed consistently
   with a single documented decision, and is that decision written down
   somewhere? (CLDR ar-JO puts the sign **after** the number.)
9. **[HARD §3.6]** Is every amount or code adjacent to Arabic text wrapped in
   `<bdi>`, `dir`, or a `unicode-bidi: isolate` container?
10. **[HARD §2.1]** Does the grand total meet **7:1** contrast against its
    surface in every theme (not just 4.5:1)?
11. **[CONVENTION §1.8]** Is the total the largest text on the screen, and is it
    free of decoration, gradient, or animation?

### B. Touch and pointing

12. **[HARD §1.2]** Is every interactive target at least **24 × 24 CSS px**, or
    does it satisfy one of SC 2.5.8's five named exceptions?
13. **[HARD §1.2]** On Android, is every touch target at least **48 × 48 dp**,
    including where the visual is smaller?
14. **[CONVENTION §1.2]** Is every target a cashier uses on the sale path at
    least **~10 mm** on its physical shortest side (NN/g's floor), measured on
    the real device?
15. **[CONVENTION §1.3]** Is the **Pay / Charge** control larger than the
    surrounding controls?
16. **[CONVENTION §1.3]** Is every destructive control (void, refund, delete
    line, close shift) separated from its nearest non-destructive neighbour by
    more padding than the neighbours have between themselves?
17. **[HARD §1.2]** If any target is under 24 px, do the 24 px circles centred on
    each such target fail to intersect any other target's circle?
18. **[CONVENTION §1.8]** Does the charge control occupy the same screen position
    in every state of the sale screen (does it never move)?
19. **[CONVENTION §1.6]** Can the entire sale — scan, quantity, discount, pay,
    print — be completed **without a pointer**, using only a scanner and a
    keyboard?
20. **[CONVENTION §1.6]** Does the scan-target field hold focus on load, and
    regain it after every modal, toast, and list refresh?
21. **[CONVENTION §1.6]** Does an Enter keystroke arriving while a payment dialog
    is open fail to complete the sale?
22. **[CONVENTION §1.6]** Do numeric fields use `inputmode="decimal"` rather than
    `type="number"`?
23. **[CONVENTION §1.6]** Does every number pad in the product use the **same**
    digit layout (all 7-8-9-top or all 1-2-3-top)?

### C. Errors, refusals, and irreversible actions

24. **[HARD §1.4]** Does every irreversible money action satisfy at least one of
    SC 3.3.4's three: reversible, checked, or confirmed?
25. **[CONVENTION §1.4]** Are routine actions (add line, change quantity)
    **free** of confirmation dialogs?
26. **[CONVENTION §1.4]** Where an action is reversible, is undo offered
    *instead of* a confirmation dialog rather than in addition to it?
27. **[HARD §1.9]** Is every error described **in text**, not only by a colour, a
    border, or an icon?
28. **[CONVENTION §1.9]** Does each error message appear adjacent to the field or
    control it concerns?
29. **[CONVENTION §1.9]** Does the message say what to do next, not only what
    went wrong?
30. **[CONVENTION §1.9]** Is the wording free of "invalid", "illegal",
    "forbidden", "you forgot", "please", "sorry", "oops", error codes, and
    generic strings like "An error occurred"?
31. **[CONVENTION §1.9]** Does a validation error appear on **blur**, not on
    every keystroke, and does it clear **immediately** on valid input?
32. **[CONVENTION §1.9]** When an error is shown, are the user's already-entered
    values preserved rather than cleared?
33. **[CONVENTION §1.7]** Does a payment refusal distinguish *bank declined* from
    *blocked* from *our system failed*, in words?
34. **[CONVENTION §1.7]** Does the Pay control become disabled and visibly
    pending on the first press, and stay so until the server answers?
35. **[CONVENTION §1.5]** Does any action that can exceed **1 s** show a
    persistent progress state, and anything past **10 s** show determinate
    progress?
36. **[CONVENTION §1.5]** Does a scanned line appear in the cart in under
    **0.1 s**?
37. **[CONVENTION]** Is every control the server would refuse for this user's
    role either absent or visibly disabled with a reason — never present,
    enabled, and doomed?

### D. Contrast and legibility, per theme

Run **B/C/D** once per theme. A single pass proves nothing about the other four.

38. **[HARD §2.1]** Does all body text meet **4.5:1** against its actual
    background in this theme?
39. **[HARD §2.1]** Does text that relies on the 3:1 large-text allowance
    actually measure **≥ 24 px regular or ≥ 18.5 px bold**?
40. **[HARD §2.1]** Do input borders, checkbox outlines, selected-state
    indicators and focus rings meet **3:1** against adjacent colours?
41. **[HARD §2.3]** Is the keyboard focus ring visible on **every** focusable
    control in this theme, and is it never the same colour as the surface behind
    it?
42. **[HARD §2.3]** When an element receives focus, is it never **entirely**
    hidden behind a sticky header, footer, or total bar?
43. **[CONVENTION §2.2]** For the three dark themes, has the screen been looked
    at on a real panel — not only measured — because the WCAG 2.x formula is
    documented as unreliable below mid-luminance?
44. **[CONVENTION §4.4]** Is there no `#000000` ground and no `#FFFFFF` text in
    any dark theme?
45. **[CONVENTION §4.4]** In dark themes, is elevation expressed by a *lighter
    surface* rather than by a shadow alone?
46. **[CONVENTION §4.4]** Are there no large areas of saturated colour in dark
    themes?
47. **[HARD §4.2]** Is there no information anywhere on the screen conveyed by
    hue alone?
48. **[CONVENTION §4.1]** Does every colour on the screen come from a semantic
    token, with no literal hex outside the token layer?
49. **[CONVENTION §4.1]** Is every token used for the role it is named for
    (no `--state-danger-*` used decoratively, no accent used as body text)?
50. **[CONVENTION §4.5]** Are the five themes still visibly distinguishable from
    one another on the theme picker and on the sale screen?

### E. Arabic and RTL — run every one of these in Arabic, on a real render

51. **[HARD §3.1]** Does the whole layout mirror — reading order, alignment,
    navigation order, tab order?
52. **[HARD §3.1]** Do **numbers** keep their digit order unmirrored?
53. **[CONVENTION §3.1]** Do directional icons (arrows, back/forward, indent)
    mirror?
54. **[CONVENTION §3.1]** Do clocks, circular-refresh icons, and
    media-playback controls **not** mirror?
55. **[CONVENTION §3.1]** Do charts and graphs keep their axis orientation?
56. **[CONVENTION §3.1]** Does the brand mark **not** mirror?
57. **[CONVENTION §3.1]** Do icons depicting real-world objects (camera,
    printer, keyboard, drawer) **not** mirror?
58. **[HARD §3.1]** Is the layout built from logical properties
    (`padding-inline-start`, `margin-inline-end`, `start`/`end` on Android)
    rather than from flipped `left`/`right` rules?
59. **[HARD §3.1]** Is base direction set on the document, never in CSS?
60. **[HARD §3.6]** Is every shop-typed string (product name, customer name,
    category, payment method) isolated with `<bdi>` or equivalent where it is
    interpolated into a sentence?
61. **[HARD §3.6]** Does a product whose name mixes a Latin SKU with Arabic words
    render with the SKU intact and in the right place?
62. **[HARD §3.6]** Does trailing punctuation (`!`, `?`, `:`, `,`) sit on the
    correct side of every mixed-direction phrase?
63. **[HARD §3.6]** Do phone numbers, reference numbers and times render
    unscrambled beside Arabic text?
64. **[HARD §3.2]** Is `letter-spacing` **not** applied to any Arabic string?
    (Check uppercase-label classes and the brand tracking specifically.)
65. **[CONVENTION §3.2]** Is emphasis in Arabic carried by weight, size, colour
    or surface — never by italics, `text-transform: uppercase`, or underline?
66. **[CONVENTION §3.2]** Are Arabic ascenders and descenders uncropped in every
    single-line container (buttons, chips, table cells, tab labels)?
67. **[CONVENTION §3.3]** Do the Latin and Arabic faces sit on a shared baseline
    in a mixed run, with no visible vertical offset?
68. **[HARD §5.2]** Does every icon-only control's accessible name come from the
    **Arabic** catalog when the UI is in Arabic?
69. **[HARD §5.2]** For every labelled control, does the accessible name contain
    the visible Arabic text (SC 2.5.3, checked in Arabic, not only in English)?
70. **[CONVENTION §3.1]** Does no text overflow, truncate, or wrap differently in
    Arabic than in English at the same viewport?

### F. Icons

71. **[HARD §5.2]** Does every icon-only control have a programmatically
    determinable accessible name?
72. **[HARD §5.2]** Is every decorative icon `aria-hidden`, and is no name
    announced twice?
73. **[CONVENTION §5.1]** Does every icon outside {home, print, search} carry a
    **visible** text label, or is its label-free use justified by it being on the
    cashier's high-frequency path?
74. **[CONVENTION §5.1]** Is there no icon-only navigation on the desktop shell?
75. **[CONVENTION §5.3]** Do all icons on the screen share one grid, one stroke
    weight convention, and one corner-radius convention?
76. **[CONVENTION §5.3]** Is the touch target around a 24 px icon at least 48 px
    (or 40 px around a 20 px icon in dense back-office layouts)?
77. **[CONVENTION §5.3]** Do small icons (≤ 20 px) still read at their rendered
    size on the real device, rather than only in the design file?
78. **[CONVENTION §5.3]** Does every icon come from the sanctioned set, with no
    raw emoji rendered as an icon?
79. **[HARD §2.1]** Does every icon that conveys meaning meet **3:1** against its
    background in every theme?

### G. Brand and assets

80. **[CONVENTION §6.1]** Is the mark legible and identifiable at **16 × 16 px**
    in a real browser tab, not just scaled down in a design tool?
81. **[HARD §6.1]** Does a 512 × 512 maskable icon keep all essential content
    inside the **40 %-radius centre circle**?
82. **[HARD §6.1]** Does the Android adaptive icon keep its content inside the
    inner **66 × 66 dp** of the 108 × 108 dp canvas?
83. **[CONVENTION §6.1]** Does the Windows icon pass **3:1** contrast over at
    least half its area on both light and dark backgrounds?
84. **[HARD §6.2]** Does the receipt mark render correctly as **one-bit** ink,
    with no gradient, no tone, and no hairline that disappears at the printer's
    dot pitch?
85. **[CONVENTION §6.3]** Is the mark variant chosen by surface lightness from a
    small fixed set, rather than re-tinted to each theme's accent?
86. **[CONVENTION §6.1]** Does the mark avoid relying on text, given that Apple's
    own guidance is to include text *"only when it's essential"*?

### H. Process — the items the other 86 cannot prove

87. **[CONVENTION §4.5]** Has this screen been **rendered and looked at** in all
    five themes and in both languages, rather than only reasoned about?
88. **[CONVENTION §4.5]** Has it been looked at on the **real hardware** (a cheap
    counter laptop panel, the Android terminal), not only on a developer
    monitor?
89. **[CONVENTION §2.4]** Has the till screen been checked under **bright
    ambient light**, given that negative polarity under poor conditions is the
    documented worst case?
90. **[CONVENTION]** For any guard or test claimed to protect an item above, has
    it been **mutation-proven** — broken deliberately, watched to fail, restored?

---

## 8. What this document does not establish

### 8.1 Where sources genuinely disagree

These are live disagreements. A reviewer must not cite this document as having
settled them.

| Question | Side A | Side B |
|---|---|---|
| **Minimum touch target** | WCAG 2.2 AA: 24 × 24 CSS px (normative) | NN/g: 10 × 10 mm (≈ 38 px); Android 48 dp; Parhi 9.2 mm. All larger. |
| **Apple's minimum** | 44 × 44 pt (the widely-quoted default) | 28 × 28 pt (Apple's own published *minimum*) |
| **Does dark mode reduce eye strain?** | Material asserts it, with no cited study | Four peer-reviewed polarity studies find light-on-dark reads *worse*; NN/g records a real exception for cloudy-ocular-media users |
| **Is WCAG 2.x contrast valid for dark themes?** | WCAG 2.2 is normative and must be met | APCA's author: *"WCAG 2.x contrast cannot be used for guidance designing 'dark mode'"*; APCA is not normative |
| **Digits in Jordan** | CLDR `ar_JO`: `arab` (Arabic-Indic) | Central Bank of Jordan and ISTD both publish Arabic prose with **Western** digits, exclusively |
| **Does Arabic need extra line height?** | Material (legacy): +0.1 em line height, +1 px size | UAE government design system: one uniform 1.5× for both scripts |
| **Number-pad layout** | Cash registers: 7-8-9 top | Telephones and every touchscreen OS: 1-2-3 top; Deininger 1960 found it slightly faster |
| **Thermal printer resolution** | Epson TM-T88VII datasheet: 180 dpi | Generic 58 mm mechanisms widely quoted at 203 dpi (weak sourcing) |

### 8.2 Where no source could be found

Stated so that nobody cites this document for them:

- **ISO 9241-303 / ANSI-HFES 100 numeric requirements** — paywalled (403) and
  `web.archive.org` unreachable. The 16 / 20–22 arcminute figures are **not
  verified here**.
- **Any published POS vendor UI specification with numbers** — Square, Shopify,
  Toast, Elo, Verifone, PAX, Ingenico all checked. Clover alone gives qualitative
  guidance and a 4.5:1 floor.
- **NN/g kiosk or self-service touchscreen article** — confirmed 404 and absent
  from their own topic index.
- **POS offline / degraded-mode visual UX** — nothing found anywhere.
- **Cart / total / pay spatial layout** — no published rule exists.
- **Icon-recognition rates** — no accessible study with usable percentages;
  ISO 9186 returned 403.
- **Cultural icon legibility in Arabic-speaking markets** — listicles only.
- **Green/red cultural meaning in the Middle East** — three real cross-cultural
  colour papers located; none of their accessible abstracts covers a Middle
  Eastern country.
- **Ruq'ah for UI or small sizes** — nothing.
- **Date/time-specific bidi guidance** as a distinct category — nothing; the
  general isolation mechanism is the answer.
- **CSS-level Arabic/Latin vertical-metric matching** (`size-adjust`,
  `ascent-override`) — only the OpenType spec addresses it.
- **Saudi (dga.gov.sa) government design system** — 403 on every attempt. **No
  Qatar system found at all.** The UAE's is real but publishes **no
  icon-mirroring table**.
- **Any real RTL design case study** for Careem, Talabat, HyperPay, Tap, Arab
  Bank or Capital Bank.
- **Minimum-size / clear-space numbers from a major brand system** — Mozilla,
  IBM, Atlassian and Shopify brand pages all unreachable.
- **Thermal printable width in dots** for 58 mm / 80 mm from a manufacturer
  datasheet.
- **What Jordanian shoppers expect** on a price tag or receipt, as distinct from
  what the government publishes.
- **Whether any design system runs contrast checks in CI** — implied by the
  architectures, stated by none of them.

### 8.3 Sources treated as weak

Flagged rather than used: SEO content-marketing pages on Arabic line-height
ratios (1.6–1.8 vs 1.4; 18–20 px vs 15–16 px) — directionally consistent with
Material's legacy +0.1 em / +1 px but unverified; reseller listings for thermal
printer DPI; Shopify Polaris and Salesforce Lightning token structures
(search-snippet only, direct fetch failed); the "25 % tap error rate below
44 pt" figure that circulates attached to Apple — **no primary source for it was
found and it is not used here**.

---

## 9. Source ledger

Grouped by tier. Every URL below was fetched and read during this research.
Items marked (proxy) were retrieved through `r.jina.ai` because the live page is
JavaScript-rendered.

**Standards bodies**
W3C WCAG 2.2 Understanding pages: `target-size-minimum`, `target-size-enhanced`,
`contrast-minimum`, `contrast-enhanced`, `non-text-contrast`, `use-of-color`,
`text-spacing`, `label-in-name`, `focus-appearance`, `focus-not-obscured-minimum`,
`error-identification`, `error-prevention-legal-financial-data`, `conformance`,
`name-role-value` · `https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/`
· `https://www.w3.org/WAI/standards-guidelines/wcag/wcag3-intro/` ·
`https://www.unicode.org/reports/tr9/` ·
`https://www.unicode.org/Public/UNIDATA/BidiMirroring.txt` ·
`https://www.w3.org/TR/alreq/` · `https://www.w3.org/TR/appmanifest/` ·
W3C i18n: `inline-bidi-markup/`, `inline-bidi-markup/bidi_examples.html`,
`inline-bidi-markup/uba-basics.en`, `qa-bidi-unicode-controls`, `qa-html-dir`,
`i18n-tests/`, `github.com/w3c/i18n-drafts/issues/757`,
`w3c.github.io/typography/gap-analysis/arab-ar-fa` · CLDR raw data:
`common/main/ar.xml`, `common/main/ar_JO.xml`,
`common/supplemental/supplementalData.xml`,
`cldr-json/cldr-numbers-full/main/ar-JO/numbers.json`,
`cldr-json/cldr-core/supplemental/currencyData.json` · ISO 4217 official list:
`https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml`
· OpenType spec: `learn.microsoft.com/typography/opentype/spec/features_pt`,
`/spec/recom`, `/script-development/arabic`

**Platform documentation**
`developer.android.com`: `/design/ui/mobile/guides/foundations/accessibility`,
`/training/basics/supporting-devices/languages`,
`/develop/ui/views/launch/icon_design_adaptive` ·
`learn.microsoft.com`: `/windows/apps/develop/input/guidelines-for-targeting`,
`/globalization/fonts-layout/mirroring`, `/globalization/fonts-layout/text-layout`,
`/windows/apps/design/style/iconography/app-icon-design` ·
`developer.apple.com/design/human-interface-guidelines/`: `accessibility`,
`buttons`, `right-to-left`, `dark-mode`, `sf-symbols`, `app-icons` (all proxy);
`developer.apple.com/library/archive/…/SupportingRight-To-LeftLanguages` ·
`developer.mozilla.org`: `font-variant-numeric`, `unicode-bidi`,
`letter-spacing`, `Elements/link`, `Element/bdi`, `Progressive_web_apps/Manifest`
· `web.dev/articles/add-manifest`

**Vendor design systems**
`m3.material.io/styles/color/roles` (proxy) ·
`m3.material.io/styles/icons/applying-icons` (proxy) ·
`m2.material.io/design/color/dark-theme.html` (proxy) ·
`m2.material.io/design/usability/bidirectionality.html` (proxy) ·
`m1.material.io/style/icons.html`, `/style/typography.html`,
`/usability/bidirectionality.html` ·
`developers.google.com/fonts/docs/material_symbols` ·
`spectrum.adobe.com/page/color-system/`, `/page/color-fundamentals/` (proxy) ·
`carbondesignsystem.com/elements/color/tokens/` (proxy) ·
`atlassian.design/foundations/color-new` (proxy) ·
`primer.style/foundations/color` (proxy) ·
`designsystem.digital.gov/design-tokens/color/overview/`,
`/components/icon/#accessibility` ·
`design-system.service.gov.uk/components/error-message/`, `/components/error-summary/`,
`/styles/colour/` · `designsystem.gov.ae/guidelines/typography`,
`/guidelines/advanced-css` · `docs.clover.com/dev/docs/design-resources`,
`/app-design-requirements` · `shopify.dev/docs/apps/build/pos` ·
`design.squareup.com/us/en/articles/designing-at-scale-part-one` ·
`doc.toasttab.com/doc/devguide/` · `docs.stripe.com/declines`, `/idempotency` ·
`techdocs.zebra.com/datawedge/7-4/guide/output/keystroke/` ·
`download4.epson.biz/sec_pubs/pos/reference_en/escpos/gs_lv_0.html` (proxy) ·
`brand.github.com/foundations/logo` · `stripe.com/newsroom/brand-assets`

**Research**
Parhi, Karlson & Bederson 2006 (MobileHCI, `10.1145/1152215.1152260`) ·
Henze, Rukzio & Boll 2011 (MobileHCI, author PDF) ·
Deininger 1960 (*Bell System Technical Journal*,
`10.1002/j.1538-7305.1960.tb04447.x`) ·
Buchner & Baumgartner 2007 (`10.1080/00140130701306413`) ·
Piepenbrock et al. 2013 (`10.1080/00140139.2013.790485`) ·
Piepenbrock et al. 2014 (`10.1177/0018720813515509`) ·
Taptagaporn & Saito 1990 (`10.1080/00140139008927110`) ·
Dobres, Chahine & Reimer 2017 (`10.1016/j.apergo.2016.11.001`) ·
`sciencedirect.com/science/article/pii/S0042698919301087` (*Vision Research*,
typeface legibility) · `nei.nih.gov/…/color-blindness`

**Practitioner writing**
NN/g: `touch-target-size`, `response-times-3-important-limits`,
`ten-usability-heuristics`, `confirmation-dialog`, `error-message-guidelines`,
`errors-forms-design-guidelines`, `user-mistakes`, `slips`,
`form-design-placeholders`, `icon-usability`, `hamburger-menus`, `dark-mode` ·
Baymard: `blog/inline-form-validation`, `labs/touch-keyboard-types` ·
`git.apcacontrast.com/documentation/WhyAPCA.html` ·
`blog.datawrapper.de/colorblindness-part2/` ·
`evilmartians.com/chronicles/how-to-favicon-in-2021-six-files-that-fit-most-needs` ·
`typedrawers.com/discussion/2147/…` ·
`monotype.com/resources/case-studies/dubai` ·
Noto/Google Fonts description files; `notofonts.github.io/noto-docs/website/use/` ·
`raw.githubusercontent.com/feathericons/feather/master/README.md`

**Real-world artefacts (Jordan)**
`https://www.cbj.gov.jo` (Central Bank of Jordan) ·
`https://www.istd.gov.jo` (Income and Sales Tax Department)
