"""Multi-device sync client (Task 5 of the multi-device-sync-foundation
plan): the product-side transport (`relay_client.py`) and background
push/pull loop (`sync_service.py`) that talk to Owner's `/api/sync/v1/push`
and `/pull` routes (Task 2). Mirrors the shape of
`commercial_runtime/licensing_contracts/` (client + scheduler) deliberately
-- same retry/backoff/timeout/TLS-verify/canonicalization approach, same
threading.Timer background-loop shape -- but is its own package: sync has a
different wire contract (event batches, not license assertions) and a
different local persistence target (a product's own `sync_outbox`/
`sync_cursor` tables, not `licensing_state`).
"""
