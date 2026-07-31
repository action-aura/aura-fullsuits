# Phase 8V-P5 — Raw Wire Capture Plan (as implemented and running)

## Method

A WSGI middleware wraps the **real** Owner Flask app's own `wsgi_app` callable, installed at
process startup before `make_server(...).serve_forever()`, scoped to `/api/licensing/v1/*` only.
For every real request that reaches the real app, it captures: timestamp, HTTP method, path,
duration, response status, the parsed request JSON body, and the parsed response JSON body --
writing one JSON line per exchange to a local file.

This is **not** a proxy, a mock, or a unit-test fixture -- it is the actual Owner application
object real Android traffic (over the real `adb reverse` tunnel) actually reaches; the middleware
observes real requests before and real responses after the real route handlers run, changing
nothing about their behavior.

## Redaction

Applied immediately, in-memory, before anything is written to disk: any dict key matching
`license_key`, `signature`, `password`, `totp_secret`, `recovery_code` (case-insensitive) is
replaced with `<redacted>` recursively through the whole payload. The full activation license key
is therefore never written to disk at all, including in the one real request where the product
protocol allows it -- a stricter redaction than the phase brief's own minimum requirement (retain
metadata, redact keys), on the basis that stricter is always safe to do and never worth relaxing for
a validation exercise.

## Storage location (temporary, local, not committed)

`C:\Users\Dell\AppData\Local\Temp\claude\...\scratchpad\capture_raw.jsonl` -- outside the repository
entirely, deleted at session cleanup after the redacted evidence excerpts needed for
`raw-wire-evidence.md` are copied into that document.

## Scope captured this session

Every real check-in this session (Clinic, Retail), the SUSPEND/un-suspend cycle's own check-ins,
the short-offline-policy scenario's check-ins, and the stale-assertion ordering test's real requests
-- see `raw-wire-evidence.md` for the actual captured, redacted excerpts.
