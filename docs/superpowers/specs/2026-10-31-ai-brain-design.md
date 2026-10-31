# AI brain for Aura — design (2026-10-31)

## The ask

The owner's request (2026-10-31, Arabic, paraphrased): "do the AI thing in the
system, and make the system use AI and automate stuff."

- **Retail:** performance analysis, improvement suggestions, deep analysis and
  trend analysis. The overall goal is to help the shop sell more and to
  understand what its buyers do.
- **Owner Control Center:** a Leads Finder (the owner called this the MOST
  important item), and a personal assistant for when a staff member needs help
  with something in the system.

The owner approved the hybrid proposal below in chat and delegated everything
else: "do it from A to Z, I authorise everything needed". Anything marked
*(my call)* is the implementer's decision, not something agreed with the owner.

## Intent

Real AI, worth the word "AI" on the website. The website already claims it:
owner answer 24A (2026-10-30) was to keep the AI claims and build AI ASAP. The
running cost should be near zero.

## Approved decisions

1. **Hybrid brains.**
   - **Leads Finder and the staff helper use Google Gemini's free tier.** The
     data is public Google Maps data, and the helper answers how-to questions
     that are not secret.
   - **Shop sales analysis uses a paid service that does not train on the
     data:** the cheapest Claude, `claude-haiku-5-5`, at about 0.06 JOD per
     shop per month for one daily report.
   - **Why the split:** Gemini's free tier may use prompts for training, and
     human reviewers may read them. The free-tier terms exempt the EEA,
     Switzerland and the UK, not Jordan.
2. **Shop data leaves the till only after the shop owner opts in.** The opt-in
   defaults to OFF.
3. **The provider is swappable by configuration**, with no code change. Free
   tiers get cut: Gemini's was cut 50-80% in December 2025.

## Hard rules (not configurable)

- **R1. A private purpose never reaches a provider that trains on its data.**
  The router refuses it in code, whatever the configuration says. Gemini counts
  as training unless `OWNER_GEMINI_PAID_TIER=true`.
- **R2. AI API keys exist only on the Owner server.** Tills never hold one.
  They send a device-signed request to Owner, Owner calls the AI, and Owner
  meters the call per licence.
- **R3. The AI never produces or alters a phone number, a price or a money
  figure.**
  - Lead phone numbers come only from Google Places. This is the established
    rule in `owner/app/leads/discovery.py:1-15`.
  - The AI response schemas have no phone field.
  - AI output is shown as advice. It is never written into a money or stock
    record.
- **R4. Spending caps**, on top of the existing Places cap:
  - a per-licence monthly cap on shop requests;
  - a per-purpose daily cap for the whole server;
  - a per-staff-member daily cap on the helper.

  A request over a cap is refused before any AI call is made.
- **R5. Invisible unless configured.** With no key set, every AI surface is
  absent or reports "not configured". Nothing crashes, and an install that
  never opts in pays nothing.
- **R6. Customer names never leave the till.** Customers appear in the
  snapshot only as aggregates or as anonymous labels (`C1`, `C2`, ...). Product
  names do leave, because the advice needs them.

## Architecture

```
till (products/retail)                 Owner server (owner/)                     AI
  stats snapshot (local SQLite) --signed--> POST /api/licensing/v1/ai/shop-analysis
                                           authenticate_device_request
                                           caps -> brain("shop_analysis") -----> Claude Haiku 5.5
Owner staff browser ----------- session --> POST /leads/discover/rank
                                           brain("leads") ---------------------> Gemini free (fallback chain)
                                           POST /assistant/ask
                                           brain("staff_help") ----------------> Gemini free (fallback chain)
```

## Owner side

Branch `feat/ai-owner`, based on `release/seller-launch-owner`.

### New package `owner/app/ai/`

**`providers.py`**

- **Interface:**
  `complete(*, system: str, messages: list[{role, content}], max_tokens: int, json_schema: dict | None) -> AIResult`.
  `AIResult` carries `text`, `data` (a parsed dict when a schema was given),
  `input_tokens`, `output_tokens`, `provider` and `model`.
- **`AnthropicProvider`**
  - Uses the official `anthropic` SDK. Add `anthropic==2.3.0` to
    `requirements/owner-server.txt`; the server runs Python 3.11.
  - The client is injectable for tests.
  - Calls `client.messages.create`. When a schema is given, it asks for
    structured output with
    `output_config={"format": {"type": "json_schema", "schema": ...}}`.
  - Haiku 5.5 runs adaptive thinking by default. Send
    `output_config.effort="low"` *(my call: reports summarise, they do not
    need hard reasoning)*.
  - `trains_on_data = False`.
- **`GeminiProvider`**
  - Uses stdlib `urllib`, following the precedent in `leads/discovery.py`, so
    there is no new HTTP dependency.
  - POSTs to
    `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`.
  - Sends the key in the `x-goog-api-key` header, never in the URL.
  - When a schema is given, sends `generationConfig.responseMimeType="application/json"`
    and `responseSchema`.
  - The HTTP call is injectable.
  - `trains_on_data = not paid_tier`.
- **Keys** must never appear in an exception or a log line. Copy the
  `_safe_detail` idea from `discovery.py`.
- **Errors:** `AIError(code)`, where `code` is one of `AI_NOT_CONFIGURED`,
  `AI_RATE_LIMITED` (upstream 429), `AI_UPSTREAM_ERROR`, `AI_BAD_OUTPUT` (JSON
  or schema mismatch), `AI_PRIVACY_REFUSED` or `AI_BUDGET_EXHAUSTED`.

**`brain.py`**

- **Entry point:**
  `run(purpose, *, system, messages, max_tokens, json_schema, config) -> AIResult`.
- **Purposes:** `SHOP_ANALYSIS` (private), `LEADS` (public), `STAFF_HELP`
  (public).
- **Provider chain per purpose** (comma-separated config values):

  | Purpose | Config variable | Default |
  |---|---|---|
  | `SHOP_ANALYSIS` | `OWNER_AI_SHOP_PROVIDERS` | `anthropic` |
  | `LEADS` | `OWNER_AI_LEADS_PROVIDERS` | `gemini,anthropic` |
  | `STAFF_HELP` | `OWNER_AI_HELP_PROVIDERS` | `gemini,anthropic` |

  The paid Claude fallback on the public purposes *(my call)* keeps the
  feature alive when Gemini's free quota runs out, and costs fils.
- **Moving down the chain:** only `AI_RATE_LIMITED` and `AI_UPSTREAM_ERROR`
  send a request to the next provider.
- **R1 before any call:** providers with `trains_on_data` are dropped from a
  private chain. An empty chain raises `AI_PRIVACY_REFUSED`.
- **Unconfigured providers:** a provider with no key is skipped. A chain with
  nothing configured raises `AI_NOT_CONFIGURED`.
- **Logging:** one line per call, with purpose, provider, model, tokens and
  outcome. Never the prompt text.

**`budget.py`**

- Implements the caps with the `owner_rate_limit_counters` pattern from
  `leads/discovery_budget.py`: an atomic `INSERT ... ON CONFLICT ... WHERE
  count < cap`.
- `window_start` holds the **period END**, because of the housekeeping trap
  noted in that file.
- **No Alembic migration.**
- The caps:

  | Cap | Config variable | Default |
  |---|---|---|
  | Per licence, per Jordan month | `OWNER_AI_SHOP_MONTHLY_CAP` | 120 |
  | Per purpose, per Jordan day | `OWNER_AI_DAILY_CAP` | 2000 |
  | Per staff member, per Jordan day | `OWNER_AI_HELP_DAILY_CAP_PER_STAFF` | 100 |

- A cap of 0, or an invalid value, disables the feature. This matches the
  discovery rule.

**`prompts.py`**

- System prompts and JSON schemas for the three purposes.
- Output is in Arabic when `lang == "ar"`.

**`knowledge/`**

- Byte-identical copies of `docs/sales/ar/seller-guide.md`, `cheat-sheet.md`
  and `guide-2026-10-27.md`. The copies exist because the Docker image copies
  only `owner/`.
- A test fails if any copy differs from its source.
- Plus `owner_screens.md`: a short guide to the Owner screens, written by
  reading `templates/layout/_nav_model.html` and the real templates. It must
  describe the real screens and invent nothing.

**Configuration**

- Variables, all added to `KNOWN_CONFIG_ENV_VARS` and all pinned to `""` in
  `TestingConfig`:
  - `OWNER_ANTHROPIC_API_KEY`
  - `OWNER_GEMINI_API_KEY`
  - `OWNER_GEMINI_PAID_TIER` (bool, default false)
  - `OWNER_AI_ANTHROPIC_MODEL` (default `claude-haiku-5-5`)
  - `OWNER_AI_GEMINI_MODEL` (default `gemini-3.8-flash`)
  - `OWNER_AI_TIMEOUT_SECONDS` (default 40)
  - the chain and cap variables above
- Add every variable, commented, to `owner/deploy/oracle-free/.env.example`.

### Feature O1: smarter Leads Finder

- **`discovery.py`**
  - Widen the field mask with `places.rating`, `places.userRatingCount`,
    `places.types`, `places.businessStatus` and `places.googleMapsUri`.
  - These are Enterprise-SKU fields. The phone and website fields already put
    each request in that SKU, so the price does not change.
  - Add optional fields to `DiscoveredPlace`.
- **`POST /leads/discover/rank`**
  - Requires permission `leads.discover`. CSRF comes in the `X-CSRFToken`
    header.
  - JSON in:
    `{segment, area, lang, places: [{idx, name, address, types, rating, reviews, has_phone, has_website, status}]}`.
    At most 20 places, every string bounded.
  - **Phone numbers are not sent to the AI.**
  - Calls `brain(LEADS)`.
  - JSON out: `{results: [{idx, score 0-100, reason, opener}]}`. `opener` is an
    Arabic WhatsApp opening line of at most 400 characters.
  - The server drops any `idx` it did not send, and clamps the score to 0-100.
- **`discover.html` and `static/js/leads-rank.js`**
  - The script is external, because the CSP blocks inline scripts.
  - A "Rank with AI" button fills Score / Why / Opener columns and sorts the
    rows by score.
  - Each opener has a copy button.
  - The opener rides a hidden input into the import.
  - The button is hidden unless a provider is configured for `LEADS`.
- **`POST /leads/discover/import`**
  - When AI fields are posted for a row, store them on the created lead as a
    note or interaction, using whichever existing module fits (notes or
    interactions).
  - The rep then sees the reason and the opener on the lead.

### Feature O2: staff helper

- **`POST /assistant/ask`**
  - Requires `@require_login`. CSRF comes in the header.
  - JSON in:
    `{question <= 1000 chars, history <= 6 turns, page: request path of the screen}`.
  - System prompt: the knowledge files, plus "answer only about using Aura and
    our sales rules; say you don't know otherwise; never ask for or repeat
    customer personal data".
  - Calls `brain(STAFF_HELP)`, with the per-staff daily cap.
  - JSON out: `{reply}`.
- **UI**
  - A floating "مساعد / Help" button in `layout/base.html` for logged-in staff.
    It is hidden unless `STAFF_HELP` has a provider.
  - `static/js/ai-help.js` opens a small chat panel.
  - The panel shows a line: "Don't paste customer phone numbers or personal
    data — this helper uses a free service."
  - Every string uses `_()`, with Arabic in `messages.po`.

### Feature O3: till endpoint for shop analysis (the contract)

`POST /api/licensing/v1/ai/shop-analysis`, in `api_external/routes.py`. It
mirrors `/mail`: the same `is_service_ready` check, the IP rate-limit bucket
`ai_shop_analysis`, and `_get_json_body`.

The service is `owner/app/ai/shop_analysis.py::accept_shop_analysis(body, config)`.
In order:

1. Validate the request first, so a bad request never burns a nonce.
2. `authenticate_device_request(body, nonce_scope="ai_shop_analysis", config=...)`.
   The licence comes from the Installation row, never from the body.
3. Check the per-licence monthly cap and the daily cap.
4. Call `brain(SHOP_ANALYSIS)`.

**The request body is never logged.**

**Request body.** The envelope is what `LicensingClient` already builds.

```json
{
  "contract_version": "...", "request_id": "uuid", "correlation_id": "uuid",
  "timestamp": "iso", "nonce": "...", "installation_id": "...",
  "app_version": "...", "platform": "windows",
  "kind": "report" | "chat",
  "lang": "ar" | "en",
  "question": "string <= 1000 (chat only)",
  "history": [{"role": "user"|"assistant", "content": "<= 2000"}],
  "snapshot": { ... },
  "signature": "..."
}
```

**Validation**

- `snapshot` must be a JSON object, nested no deeper than 4 levels.
- The whole body must be at most 60 KB.
- Owner does not validate the snapshot field by field, so the till can add
  metrics without an Owner release.
- `history` holds at most 10 items.

**Response `200`**

`{"result": "ACCEPTED", "kind": ..., "report": {...} | "reply": "...", "model": "...", "usage": {"used": n, "cap": m}}`

The report is produced by the AI under this JSON schema:

```json
{"headline": "string",
 "performance": ["string"], "trends": ["string"], "customers": ["string"],
 "suggestions": [{"title": "string", "why": "string", "action": "string"}],
 "deep_dive": ["string"], "warnings": ["string"]}
```

**Errors** use `_error_response(code, status)`:

| Status | Code |
|---|---|
| 400 | `INVALID_REQUEST` |
| (as auth sets) | the auth codes, such as `INVALID_SIGNATURE` |
| 429 | `AI_BUDGET_EXHAUSTED` |
| 503 | `AI_NOT_CONFIGURED` |
| 503 | `AI_UPSTREAM_ERROR` (includes an upstream rate limit) |
| 503 | `AI_PRIVACY_REFUSED` |

**System prompt for `report`:**

- use only the numbers in the snapshot, and never invent figures;
- name products;
- give 3-6 concrete actions a small Jordanian shop can do this week.

### Not in scope (Owner)

These are recorded as follow-ups:

- a billing ledger for metered AI;
- AI plans gated by entitlement (a per-licence cap exists instead);
- nightly AI jobs.

## Retail side

Branch `feat/ai-retail`, based on `release/rc17`.

### Setting

- `retail_settings` key `ai_analysis_enabled`, stored as `'0'`/`'1'`, default
  `'0'`.
- Written through the existing `POST /settings/credit` whitelist, which is
  owner-only via `_require_company_admin` and licence
  `retail.settings.update`. This follows the `allow_negative_stock` precedent
  and needs no new route.
- A toggle in `settings-hub.js`, with this explanation in both languages:
  "Your shop's sales figures (never customer names) are sent to our server and
  an AI service to write your analysis."

### `commercial_runtime/licensing_contracts/client.py`

- Add
  `LicensingClient.ai_shop_analysis(*, installation_id, signer, app_version, platform, kind, lang, snapshot, question=None, history=None)`.
- Shape it like `send_mail`. It posts to `/ai/shop-analysis`.

### `core/retail/ai_snapshot.py`

`build_snapshot(conn, cid, branch_id=None, now=None) -> dict`

- **Clock:** use `metrics.py` with the business clock (`business_now`), never
  a bare `datetime.now()`.
- **Windows:** the last 30 days against the preceding 30.
- **Contents:**
  - currency and the `summary()` figures;
  - `revenue_by_day` over 30 days, revenue by weekday, `revenue_by_hour`;
  - the payment mix;
  - `top_products` (15, with units, revenue and profit);
  - slow movers: in stock, with zero or low units sold in 30 days (top 15);
  - the low-stock list (top 15);
  - refunds and the returns rate;
  - total discounts;
  - customers:
    - totals, active in 30 days, new in 30 days;
    - the repeat-buyer share and the average basket;
    - the top 10 as anonymous `C1..C10`, with spend and visit count;
  - the 10 most frequent product pairs in the same sale over 90 days.
- **Scope:** company-scoped always, and branch-scoped when a branch is given.
- **Size:** under 40 KB. Truncate the lists if needed.

### `core/retail/ai_remote.py`

- Builds the signer and the installation id the same way
  `crash_reports/sender.py:210-253` does.
- Calls the client with `max_retries=1` and a 60 s timeout.
- Maps Owner's codes to local errors.
- On Android, where Python has no signer, it reports "not available on this
  device".

### Routes

Every route has `@mt_login_required`, `@mt_require_subsystem('retail')` and
`@mt_require_capability(CAP_REPORTS)`, and is added to the capability-matrix
expectations.

**`GET /ai/status`**

- Keeps `configured` and adds two fields:
  - `enabled` — the opt-in;
  - `remote` — Owner is reachable in principle: an Owner URL is set, an
    installation id is present, and this is not Android.
- `configured` becomes `remote or legacy endpoint set`.

**`POST /ai/chat`**

Keeps its request and response shapes, streaming included. It picks the first
case that applies:

1. **Opt-in on and remote available:** send a `chat` with the snapshot. A
   streaming request gets the whole reply as one `delta`, then `done`.
2. **Opt-in off and remote available:** return 409
   `{"success": false, "error": "...", "code": "AI_NOT_ENABLED"}`.
3. **Legacy `AURA_AI_ENDPOINT_URL` set:** the existing Ollama path, unchanged.
4. **None of these:** the existing 503 "not configured".

**`POST /ai/insights`**

- Builds the snapshot, sends a `report`, and returns the report.
- Saves it atomically to `<AURA_APP_DATA>/ai/insights_<company_id>.json`. This
  is a local cache, not business data, so there is no schema change.

**`GET /ai/insights/latest`**

- Returns the cached report and `generated_at`, or `null`.

### Frontend

- A new nav entry `ai-insights`, labelled "Smart analysis" / "تحليل ذكي", in
  the Reports group, with capability `retail.reports`.
- A new stand-alone file `frontend/ai-insights.js`, modelled on `expenses.js`,
  with a `<script>` tag in `index.html`.
- **Opt-in off:** explain what the screen does and point an admin to Settings.
- **Opt-in on:**
  - Show the latest report: the headline, then sections for Performance,
    Trends, Customers, Suggestions (cards with title, why and action), Deep
    dive and Warnings.
  - An "Analyse now" button.
  - An "Ask a question" button that opens the existing `SubAI` panel.
- **Automation:** opening the screen when the cached report is older than 24
  hours refreshes it.
- Every string goes into `locales/en.json` and `ar.json`. Colours come from
  tokens only.

### Tests

- **Snapshot:**
  - correct figures on a seeded database;
  - company isolation;
  - no customer name anywhere in the serialized snapshot;
  - the size bound.
- **Routes:**
  - 409 when the opt-in is off;
  - the remote path with a fake client;
  - the legacy path still works;
  - the matrix and reachability ratchets pass.
- Run one file per process, with the main-checkout venv.

### Not in scope (Retail)

- **Android.** Python on Android cannot sign Owner requests; Kotlin holds the
  device key (`android_bridge_identity.py`). AI on the phone needs a
  Kotlin-side signed call, following the same pattern as updates. That is a
  separate wave.
- **A daily WhatsApp AI summary.** WhatsApp credentials are not live yet.

## What the owner must do

1. Get a free Gemini API key at aistudio.google.com.
2. Get an Anthropic Console API key, which needs a card.
3. Get a Google Places key (`scripts/ops/setup_places_key.ps1`).
4. Set all three in the server's `.env`, then do a live update.
5. Ship a new till release so shops get the Retail half.

## Acceptance

- Every new test passes, and every existing Owner and Retail suite this work
  touches still passes.
- Every guard is mutation-proved in both directions:
  - R1 refuses the private purpose;
  - a cap refuses at the cap and allows below it;
  - the opt-in denies and allows;
  - no phone number is sent to the AI;
  - no customer name appears in the snapshot.
- Both apps boot.
- **A real end-to-end run with real keys is blocked until the keys exist.**
  The report must say so explicitly, not imply it.
