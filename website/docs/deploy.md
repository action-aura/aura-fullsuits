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

Paste the printed `database_id` into `wrangler.toml` and `retention/wrangler.toml`. Then:

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
- Env var: `NODE_VERSION=22.12.0`

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
- `SMTP_USER` (the mailbox name)
- `SMTP_PASS` as a secret
- `NOTIFY_TO=info@actionaura.me`

Option B, mail off: set `NOTIFY_PROVIDER=none`.

Verify: for A, step 9 delivers a test mail. For B, step 9 shows the lead in D1 only.

## 7. [owner] Web Analytics

Create a Web Analytics site in Cloudflare. Set `PUBLIC_CF_BEACON_TOKEN` as a build env var. Rebuild.

Verify: the page source holds the beacon script. The dashboard shows a visit after you open the site.

## 8. [team] Deploy the retention job

```
npx wrangler deploy -c retention/wrangler.toml
```

Verify: `npx wrangler tail` on the next cron run shows it ran. To test sooner, run it locally with `--test-scheduled`.

## 9. [team] Smoke test on the `*.pages.dev` URL

- Run the `stack.spec.ts` checks against the URL.
- `curl -I <pages.dev URL>` shows `X-Robots-Tag: noindex`.
- Post one real form lead.
- If smtp is on, a test mail arrives.

Verify: all four pass. The lead is in D1:
`npx wrangler d1 execute aura-leads --remote --command "SELECT id, created_at FROM leads ORDER BY id DESC LIMIT 1"`.
Then delete the test lead.

## 10. [owner] Point DNS (GATED)

Why: this makes the site live on `actionaura.me`. A wrong DNS record can break the company email (`info@actionaura.me`) or take the old site down.

Choose one route:

- Move DNS to Cloudflare. Copy every record first. Run `scripts/ops/check_mail_dns.py` before and after the move.
- Or use the Namecheap ALIAS route.

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
- [ ] The real-phone check is done on a mid-range Android.
- [ ] The native Arabic read is done.
- [ ] The lawyer has signed off on Privacy and Terms.
- [ ] Office hours are confirmed.
- [ ] The share-card preview is tested in WhatsApp.
- [ ] Search Console shows no errors.
- [ ] The form is checked live: post a test lead, confirm it is in D1, then delete it.
