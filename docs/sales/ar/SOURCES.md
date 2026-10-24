# Sources for the Arabic seller guide

Where every number and rule in `seller-guide.md` and `cheat-sheet.md` came from.

Priority used: (1) Claude Doc "Aura Growth Plan" (https://claude.ai/artifact/FGQcWdVcDV2EPGgEbyqhtx), read-only, rev 77, read 2026-10-24; its "Your answers" section wins over older sections. (2) Step brief given by the owner's orchestrator (same decisions). (3) `website/js/content.js` (Arabic strings; a retired copy of the site, not online) for product and feature names only. (4) Owner answers of 2026-10-24 on renewal, commission scope and refunds (also recorded in the doc, rev 78).

## Prices and packages

| Item in guide | Source |
|---|---|
| Prices are per branch; branch = one shop; device hardware priced separately | Doc "Your answers" (Prices per branch); doc "Packages and prices" (device always priced separately) |
| P1: 250 JOD first year (incl. updates/new features), then 100 JOD/year, 1 desktop + 1 phone, no contract | Doc "Your answers"; doc "Package 1" section ("250 JOD buys Aura with everything for the first year") |
| P2: 35 JOD/month, 12-month contract; P3: 20 JOD/month, 24-month contract | Doc "Packages and prices" table; brief |
| Extra device = 1 desktop + 1 phone pair, 50 JOD once, same on all packages | Doc "Decided on the last four" |
| Each branch pays its own price | Doc "Decided on the last four" (Branches on monthly packages) |
| Unpaid: keeps selling; loses sync, online extras, updates | Doc "Your answers" and "Package 1: what happens after an unpaid year" |
| Monthly package unpaid months stay owed | Doc "Your answers" |
| Reminders 30 and 7 days, then 14-day grace | Doc "Your answers" |
| Contract end: new contract or normal package | Doc "Your answers" |
| Package change via website account, shown as "coming" | Doc "Your answers"; website accounts are Step 2, not built |
| Safety fixes reach every shop | Doc "Your answers" |
| Always free: WhatsApp automation, simple daily analysis, owner/manager phone app while licence active | Doc "Always free" |
| NOT built: AI agent, deep analysis, online card payment, self sign-up, iPhone | Doc "What exists today and what must be built" (Important note), "Your answers" (self sign-up, iPhone on request); doc "What Owner already has" |
| E-invoicing built but not switched on | Doc ("E-invoicing ... already built ... switched off"); website `einvoicing` block |

## Commission

| Item | Source |
|---|---|
| Ladder 20/50/100/200/350, cumulative totals, resets monthly, +30 per sale after the 5th | Doc "Your answers" (Commission) and the Commission table (6 sales = 380, 10 sales = 500) |
| 10 JOD per renewal | Doc "Your answers" |
| 30-day hold before payment | Doc "Your answers" ("paid after 30 days") |
| Monthly package counts after ~100 JOD (P2 3rd payment = 105; P3 5th payment = 100), counted in month that payment is confirmed; never if customer stops; switch to P1 counts at 250 | Doc "Decided on the last four" |
| New contract at end earns the 10 JOD renewal | Doc "Decided on the last four" |
| Commission only; sellers are registered employees | Doc "Your answers" (Sellers) |
| Yearly 100 JOD renewal does not grow with extra devices (100/yr per branch) | Owner answer 2026-10-24 |
| Commission only on a new sale or a branch addition; none on extra devices added later; a new branch counts as a sale on the ladder | Owner answer 2026-10-24 |
| Refund within the 30-day hold: that sale does not count for commission | Owner answer 2026-10-24 |

## Rules

| Item | Source |
|---|---|
| Sellers never take cash; customer pays company by CliQ/transfer, invoice number in the note | Doc "Your answers" (Money) and "Rules to teach hard" |
| First contact call or visit; WhatsApp only after agreement | Doc "Your answers" (Leads) |
| Manager confirms; seller cannot confirm own sale; first manager to approve confirms, anywhere | Doc "Your answers" (Confirming payments) |
| No licence before payment confirmed | Doc "Rules to teach hard" |
| Confirmation target of one hour in working hours | Doc "Decisions you need to make" #4 and "Rules to teach hard" (older section, not contradicted by "Your answers") |
| "No thanks" = do-not-contact; first message says how to say no | Doc "Leads Finder, The safe flow" |
| About 30 new contacts per seller per day | Doc "Leads Finder, Limits" ("About 30"). A proposed limit, labelled "المقترح" in the guide |
| Fake customers / friends and family / discounts need approval; commission on money actually paid | Doc "Anti-cheating checks" (a proposal in the doc; guide states the behaviour as a don't, not as final policy detail) |

## Product features (names as on the website)

From `website/js/content.js`, `ar` block: 14 days offline grace and warn-only; Arabic-first RTL; double-entry accounting (دفتر أستاذ، ميزان مراجعة، أرباح وخسائر، ميزانية عمومية); قارئ الباركود; طباعة الإيصالات; درج النقد; فروع متعددة; تحويلات المخزون; حسابات العملاء; ذمم الموردين; نقاط الولاء; عروض الأسعار; مزامنة بين الأجهزة; Windows + Android. Contact email info@actionaura.me from `CONTACT` in the same file.

## Open items shown as "قيد المراجعة القانونية"

Minimum wage / base salary; employment contract wording; privacy and opt-out ("no thanks") wording and deletion of lead data. Doc "Things only you can start now" and "Decided: sellers are employees ... lawyer or accountant must confirm".

## Conflicts between sources

1. **Website prices (resolved, corrected 2026-10-24).** An earlier version of this guide told sellers the website showed old prices. That was wrong: it came from a retired copy of the site (`website/js/content.js`, `website/pricing.html` on the repo branch) that is not online. The live site https://actionaura.me shows no prices and says "Pricing depends on your shop. Ask us." The guide now says sellers give prices themselves from the guide and that the website deliberately shows no prices. The retired copy's one-time/no-renewal wording is not a source for anything in the guide.
2. **Extra device.** Doc "Your answers" says "An extra desktop or phone costs 50 JOD"; the later "Decided on the last four" says 50 JOD once per extra pair of desktop + phone. The later one is used.
3. **Original ask vs ladder.** The WhatsApp ask "350 JOD for every 5 licences" is superseded by the monthly ladder.
4. **Commission timing.** The doc's older proposal says a licence counts when switched on at the customer and pays "30 days after switch-on"; "Your answers" says only "paid after 30 days". The guide says 30-day hold without naming its start. Not confirmed.
5. **Who activates.** Doc says the employee switches the licence on at the customer's device; the brief says the customer activates in the app. The guide has the customer activate, with the seller helping.
6. **Sync wording.** Website says sync over the shop's own wifi with no cloud; the doc says unpaid shops lose sync and "online" features. The guide only says "sync between devices".
7. **Expired-licence behaviour in the app today.** The doc notes the apps currently go read-only when a licence runs out and that the "keep selling" rule still has to be built (Step 2). The guide states the company rule as decided. No customer reaches a renewal before that.

## Could not confirm

- (Closed 2026-10-24.) The renewal-with-extra-devices, commission-scope and refund questions were answered by the owner; see the Commission table. Example 1 year 2 = 100 is now decided, not inferred.
- The real Owner screen names and flow (they change this month). Every step describing Owner carries the update marker.
- Company phone number: not in the website data (`phone` is empty), so only the email is given.
- Printer and Android printing support: not verified, so the guide says to ask the manager.

## Worked-example arithmetic (re-derived)

| Example | Calculation | Result |
|---|---|---|
| 1: P1 year 1 with one extra pair | 250 + 50 | 300 |
| 1: year 2 | 100 | 100 |
| 2: P3 over 24 months | 20 x 24 | 480 |
| 2: with one extra pair | 480 + 50 | 530 |
| 3: P1 over two years | 250 + 100 | 350 |
| 3: P2 over 24 months | 35 x 24 | 840 |
| 3: P3 over 24 months | 20 x 24 | 480 |
| 4: two branches P1, year 1 | 250 + 250 | 500 |
| 4: year 2 | 100 + 100 | 200 |
| P2 3rd payment | 3 x 35 | 105 |
| P3 5th payment | 5 x 20 | 100 |
| Month with 3 sales | 20 + 30 + 50 (table: total at 3) | 100 |
| Month with 5 sales | table: total at 5 | 350 |
| Month with 7 sales | 350 + 30 + 30 | 410 |
| Oct: 2 P1 sales + 1 P2 sale | 2 sales counted: table total at 2 | 50 |
| Dec: P2 counts + 2 P1 sales | 3 sales counted: table total at 3 | 100 |
| Payment timeline for P2 sold in October | payments Oct, Nov, Dec; 3rd is December | December |
