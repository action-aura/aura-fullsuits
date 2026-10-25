# Website -> Owner account API: request signing, v1

Step 2 (customer website accounts), slice CA1. The Pages Function (the website's
server side) calls `POST https://owner.actionaura.me/api/website/v1/account/<action>`.
A browser never calls it. Every call is signed with a shared secret.

## The header

    X-Aura-Site-Signature: v1,kid=<kid>,ts=<unix seconds>,nonce=<16-64 chars>,sig=<64 hex>

* `kid` -- which secret slot signed it (`k1` by default; `k2` is the second slot used
  while rotating). Set by `OWNER_WEBSITE_ACCOUNT_KEY_ID[_2]` on Owner.
* `ts` -- whole UNIX seconds, decimal. Owner accepts +-60 seconds.
* `nonce` -- 16 to 64 characters from `A-Za-z0-9_-`, used once ever. Owner remembers it
  (scope `website_account`); a second request with the same nonce is refused.
* `sig` -- lower-case hex of the HMAC-SHA256 below.

## What is signed

    canonical = "v1\n" + METHOD + "\n" + PATH + "\n" + ts + "\n" + nonce + "\n" + hex(sha256(body))
    sig       = hex( HMAC-SHA256( secret[kid], canonical ) )

* `METHOD` upper-case (always `POST`), `PATH` the path only (no query, no host), exactly
  as requested, e.g. `/api/website/v1/account/sign-in`.
* `body` the exact request bytes (UTF-8 JSON). Sign what you send; do not re-serialise.
* Lines are joined with a single `\n` (LF), no trailing newline.

## Known-answer vector (cross-check the TypeScript against this)

    secret : aura-kat-secret-0123456789abcdefghijklmnopqrstuv
    kid    : k1
    method : POST
    path   : /api/website/v1/account/sign-in
    ts     : 1790000000
    nonce  : kat-nonce-0123456789ab
    body   : {"email":"owner@haddad.example","password":"Tr0ub4dor-and-3-horses","client_ip":"203.0.113.5","lang":"en"}

    sha256(body) = 9c16876004ff573925af9b456c4f2c01da3eb1c3a9cc51bb0eb61109bba27448
    sig          = 729cdd1adc289d537d1bd21a5fd2844adcb1ab6e5fde59f8b669623a5e19bb0a

    X-Aura-Site-Signature: v1,kid=k1,ts=1790000000,nonce=kat-nonce-0123456789ab,sig=729cdd1adc289d537d1bd21a5fd2844adcb1ab6e5fde59f8b669623a5e19bb0a

Computed independently with `openssl dgst -sha256 -hmac` and by
`tests/test_customer_accounts_api.py::test_known_answer_vector`, which fails if the
Python implementation ever drifts from this file.

## Endpoints (all POST, JSON in, JSON out)

| Path suffix | Body | Success | Failures |
|---|---|---|---|
| `/sign-in` | email, password, client_ip, user_agent | 200 `session_token`, `account` | 401 `invalid_credentials`, 429 `rate_limited` |
| `/sign-out` | session_token | 200 | -- |
| `/me` | session_token | 200 `account` | 401 `session_invalid` |
| `/password/forgot` | email, client_ip, lang | 202 (always the same) | 429 `rate_limited` (per-IP only) |
| `/password/set` | token, password, client_ip | 200 | 400 `token_invalid`, 422 `password_policy` + `reason` |
| `/password/change` | session_token, current_password, new_password, client_ip | 200 new `session_token` | 401, 422, 429 |
| `/email/confirm` | token, client_ip | 200 | 400 `token_invalid` |

`client_ip` is the visitor's address (Cloudflare `CF-Connecting-IP`); Owner trusts it
only because the request carried a valid signature. Refusals before the signature
passes: 404 (feature off: no real secret configured on Owner), 401 `unauthorized`
(bad/expired/replayed signature -- always the same answer, no detail).

Password rules (customer policy): at least 10 characters, at most 128, not in the common
list, not containing the account email, legal name or trade name. `reason` is one of
`too_short`, `too_long`, `too_common`, `contains_personal`.

Sessions: idle 60 minutes, absolute 12 hours. The website keeps `session_token` in an
HttpOnly cookie; Owner stores only its hash.
