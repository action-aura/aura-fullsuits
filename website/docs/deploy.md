# Deploy runbook

One session, owner present. Do the steps in order. Do not skip a Verify line.

Tags:
- [owner]: only the owner can do it (login, accounts, secrets).
- [team]: we do it.
- [owner+team]: both are needed at the same time.
- GATED: it changes the live domain. Stop and confirm before you run it.

No secret goes in this file, in git, or in chat. Secrets are typed into Cloudflare only.

All commands run from the `website` folder unless said otherwise.

## 1. [owner] Log in to Cloudflare

```
npx wrangler login
```

A browser opens. Approve access.

Verify: `npx wrangler whoami` shows the owner's account.

## 2. [team] Create the leads database

```
npx wrangler d1 create aura-leads
```

Paste the printed `database_id` into `wrangler.toml` and `retention/wrangler.toml`. Then commit both files by explicit path and push `feat/website`. Do this BEFORE step 3.

Why: Pages builds from git. The wrangler file is the source of truth for bindings. A placeholder id gives a deploy with no working DB. The D1 id is not a secret.

```
npx wrangler d1 migrations apply aura-leads --remote
```

Verify: `npx wrangler d1 execute aura-leads --remote --command "SELECT name FROM sqlite_master WHERE type='table'"` lists the leads table.

## 3. [owner+team] Create the Pages project

In the Cloudflare dashboard, create a Pages project from the GitHub repo. Use these settings:

- Branch: `feat/website`
- Root directory: `website` (if not, `functions/` is not found)
- Build command: `npm run build`
- Output directory: `dist`
- Node: `website/.node-version` pins Node 22.12.0. Setting `NODE_VERSION=22.12.0` in the dashboard is a backup only.

Check first: with `wrangler.toml` as the source of truth, the dashboard may accept only secrets, not plain runtime vars. If so, put the non-secret runtime values in a `[vars]` block in `wrangler.toml` and commit them: `NOTIFY_PROVIDER`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `NOTIFY_TO`. Build-time `PUBLIC_*` values go in the Pages build environment variables.

Verify: the first build is green. The `*.pages.dev` URL opens the home page. The form endpoint under `functions/` is listed in the deployment's Functions tab.

## 4. [owner] Turnstile

Create a Turnstile widget. Add `actionaura.me` and the `*.pages.dev` host. Then set:

- `PUBLIC_TURNSTILE_SITEKEY` as a build env var.
- `TURNSTILE_SECRET` as a secret.

Verify: after a rebuild, the contact form on the `pages.dev` URL shows the Turnstile check. No console error.

## 5. [owner] IP salt

Make 32 or more random bytes:

```
node -e "console.log(crypto.randomBytes(32).toString('hex'))"
```

Set the output as the secret `IP_SALT`. Do not save it anywhere else.

Verify: the secret name `IP_SALT` is listed in the Pages settings.

## 6. [owner] Lead notification mail

Option A, mail on:

- `NOTIFY_PROVIDER=smtp`
- `SMTP_HOST=mail.privateemail.com`
- `SMTP_PORT=465`
- `SMTP_USER=info@actionaura.me` (the full address; it is also used as the From address)
- `SMTP_PASS` as a secret
- `NOTIFY_TO=info@actionaura.me`

Port 465 uses TLS. Port 587 is supported, but use 465.

Option B, mail off: set `NOTIFY_PROVIDER=none`.

Verify: for A, step 9 delivers a test mail. For B, step 9 shows the lead in D1 only.

## 7. [owner] Web Analytics

Create a Web Analytics site in Cloudflare. Set `PUBLIC_CF_BEACON_TOKEN` as a build env var. Rebuild.

Verify: the page source holds the beacon script. The dashboard shows a visit after you open the site.

## 8. [team] Deploy the retention job

```
npx wrangler deploy -c retention/wrangler.toml
```

`retention/wrangler.toml` has `workers_dev = false`, so no public URL is created.

Verify: `npx wrangler tail` on the next cron run shows it ran. To test sooner, run it locally with `--test-scheduled`.

Monitoring: turn on a Cloudflare notification for Worker errors, or put a dated monthly check in the owner calendar. The check is to run `wrangler tail` or look at the D1 row count of leads older than 24 months. It should be 0. The privacy promise depends on this job.

## 9. [team] Smoke test on the `*.pages.dev` URL

- Do NOT run the full `stack.spec.ts` against pages.dev. Run only the CSP console check. This is the first run with real Turnstile and the beacon live under the CSP:
  `STACK_URL=https://<project>.pages.dev npx playwright test tests/site/stack.spec.ts -g "(b)"`
- Check noindex by hand: `curl -I <pages.dev URL>` must show `X-Robots-Tag: noindex`.
- Make one real form post by hand, with Turnstile solved.
- If smtp is on, a test mail arrives.

Verify: the CSP check (b) passes, `curl -I` shows noindex, and the hand-made post landed. The lead is in D1:
`npx wrangler d1 execute aura-leads --remote --command "SELECT id, created_at FROM leads ORDER BY id DESC LIMIT 1"`.
Then delete the test lead.

## 10. [owner] Point DNS (GATED)

Why: this makes the site live on `actionaura.me`. A wrong DNS record can break the company email (`info@actionaura.me`) or take the old site down.

Cloudflare Pages needs the apex domain as a zone on the Cloudflare account, using Cloudflare nameservers. Do the steps in order:

1. Add the `actionaura.me` zone to Cloudflare. Copy every record: MX, SPF, DKIM (`privateemail`), DMARC, and `owner.actionaura.me`.
2. Run `scripts/ops/check_mail_dns.py` against Cloudflare's view.
3. Switch the nameservers at Namecheap.
4. Re-run the mail check.
5. In the Pages project, go to Custom domains, then "Set up a domain", and add `actionaura.me`. Do not create the DNS record by hand first, because that can cause a 522.
6. Add `www.actionaura.me` with a redirect to the apex.
7. Before go-live, audit subdomains for HTTPS. `_headers` sends HSTS with includeSubDomains for a year. If any subdomain is HTTP-only, remove includeSubDomains first.

Verify: `scripts/ops/check_mail_dns.py` gives the same result as before. `curl -I https://actionaura.me/` shows no `X-Robots-Tag`. Mail to and from `info@actionaura.me` still works.

## 11. [team] Search engines

Submit `sitemap-index.xml` in Google Search Console and in Bing Webmaster Tools.

Verify: both show the sitemap as received. No fetch error.

## 12. [owner] Google Business Profile

Create or claim the profile. Use the confirmed office hours and phone.

Verify: Google starts the verification. The profile shows the same name, phone and address as the site.

## 13. [owner] Mailbox lead copies

Lead mails in the mailbox are deleted after 24 months. Do it by hand or with a mailbox rule. This keeps the privacy promise for mailbox copies.

Verify: the rule exists, or a dated reminder is set for the first cleanup.

## Open owner inputs

- Native Arabic reader for the full Arabic text.
- Lawyer review of Privacy and Terms.
- Office hours.
- Phone screenshots (plug the phone in).
- A real POS photo.

## Before announcing

Do not announce until every box is ticked.

- [ ] The full screenshot run is done, and contrast is measured at 0 failing.
- [ ] The real-phone check is done on a mid-range Android. Measure the home page's LCP with Chrome remote devtools (Performance panel). Lighthouse on the laptop gave 3.3-3.7 s (target 2.5 s), but the laptop has no GPU for testing, so the phone decides. If the phone is also over 2.5 s, open a speed task before launch. Also decide whether phones may get the live 3D logo (today they get the still picture).
- [ ] The native Arabic read is done.
- [ ] The lawyer has signed off on Privacy and Terms.
- [ ] Office hours are confirmed.
- [ ] The share-card preview is tested in WhatsApp.
- [ ] Search Console shows no errors.
- [ ] The form is checked live: post a test lead, confirm it is in D1, then delete it.
