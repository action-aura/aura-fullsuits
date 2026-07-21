# Phase 6 -- Distributed Rate Limiting Design (Part M)

## Mechanism
`owner_rate_limit_counters`, a fixed-window counter table: `UNIQUE(bucket_key, window_start)`. `check_and_increment()` computes the current window's start (`floor(now / window_seconds) * window_seconds`), looks up or creates the counter row for `(policy:bucket, window_start)`, and raises `RateLimitExceeded(retry_after_seconds)` if the count is already at the policy's `max_requests`. Because every Owner worker process shares the same Postgres database, this counter is inherently **distributed** across processes/workers -- the same property that makes the nonce store correct under concurrency (ADR-6.3).

## Policies (data, not scattered constants -- Part M's explicit instruction)
```python
POLICIES = {
    "activation": (max_requests=10, window_seconds=60),
    "activation_invalid_license": (max_requests=5, window_seconds=300),   # tighter -- brute-force resistance
    "activation_invalid_signature": (max_requests=5, window_seconds=300), # tighter -- signature-attack resistance
    "check_in": (max_requests=30, window_seconds=60),
    "signing_keys": (max_requests=60, window_seconds=60),
    "service_info": (max_requests=60, window_seconds=60),
}
```
Defined once in `app/licensing_service/ratelimit.py::POLICIES`, referenced by name from every route -- changing a limit is a one-line edit, not a hunt through the codebase.

## Bucketing dimensions
Primary bucket key today is source IP (`request.remote_addr`). `safe_bucket_component()` provides a SHA-256-prefix hashing helper specifically so a future bucket keyed on a license identifier or device fingerprint **never** stores the plaintext secret in the rate-limit table -- verified: `test_phase6_security_controls.py::test_rate_limit_never_stores_plaintext_secret` constructs a real license-key-shaped string, buckets it, and asserts the plaintext never appears in any `RateLimitCounter` row.

## Resistance (Part M's explicit threat list)
- **License-key brute force**: the `activation_invalid_license` policy specifically tightens the budget the moment a request fails with a license-related rejection (`routes.py::activations` calls `ratelimit.check_and_increment("activation_invalid_license", ...)` inside the `ActivationRejected` handler when the reason is license-related) -- repeated wrong guesses exhaust budget fast.
- **Signature-failure flooding**: same pattern for `activation_invalid_signature`.
- **High-rate activation retries**: the base `activation` policy caps overall throughput per source regardless of outcome.
- **Single-IP distributed key guessing**: covered by the per-IP bucket directly.
- **Single-key multi-IP guessing**: not detectable by an IP-only bucket -- explicitly named as a residual risk (see `phase6-residual-risk-register.md`); a future phase could add a license-HMAC-prefix-derived bucket dimension (never plaintext) to catch this pattern across IPs.

## `Retry-After`
Every `429` response includes a `Retry-After` header computed from the exact remaining time in the current fixed window (`routes.py`, every rate-limited route).

## Fail-closed
Same as replay protection: `OWNER_DISTRIBUTED_RATE_LIMIT_REQUIRED=true` is the default and is hard-enforced in production-like mode. Since the limiter's backing store is the same PostgreSQL database the whole app depends on to run, there is no meaningful "rate limiting silently disabled because its backend is down" state distinct from "the whole app is down" -- another structural benefit of ADR-6.3's choice.
