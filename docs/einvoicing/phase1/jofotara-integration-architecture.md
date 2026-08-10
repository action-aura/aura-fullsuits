# JoFotara e-invoicing — Phase 1 architecture

## Why this exists

Jordan's ISTD (Income and Sales Tax Department) requires businesses to
report invoices to its JoFotara national e-invoicing system. Aura Retail
and Aura Clinic had no such capability. This wave adds it, opt-in, default
OFF, with zero effect on any install that doesn't turn it on.

## Module layout

```
commercial_runtime/einvoicing/          ALL shared logic lives here, once
  schema.py         einvoice_* tables (lives inside each product's own DB)
  settings.py       per-company config + three-layer enabled/disabled resolution
  killswitch.py      file-based kill switch, clone of .autosync/PAUSED
  credentials.py     encrypted client_id/client_secret storage
  sequence.py        gapless per-company ISTD sequence number allocator
  document.py        canonical, product-agnostic EInvoiceDocument model
  ubl.py              generic OASIS UBL 2.1 XML serializer
  qr.py               QR code rendering (segno)
  outbox.py           state machine + repository over einvoice_outbox
  audit.py            submission audit trail (table + JSONL mirror)
  worker.py           OutboxWorker — the background submission job
  routes.py           make_einvoicing_blueprint(...) — the HTTP surface
  providers/
    base.py           EInvoiceProvider interface + SubmissionResult
    mock.py            Phase 1 default — zero network calls
    direct_istd.py     Phase 2 stub — raises NotImplementedError

products/{retail,clinic}/backend/       thin, product-specific wiring only
  core/{retail,clinic}/einvoice_adapter.py   builds EInvoiceDocument from
                                               that product's own tables
  app.py             registers the blueprint, owns the per-company worker
                       registry
  api/*_api.py        one post-commit enqueue call per sale/invoice

products/{retail,clinic}/frontend/
  einvoicing.html/js  standalone settings/credentials/queue admin page
  subsystem-*.js       receipt QR block, gated on saleData.einvoice presence
```

**Non-negotiable boundary:** nothing in `commercial_runtime/einvoicing/`
imports anything from `products/`. Nothing in it ever contacts the Owner
Control Center — see `product-to-owner-data-boundary-einvoicing.md`.

## Why the database and not a new file

`commercial_runtime/backup/service.py` only ever snapshots `registry.db`
and `<product_code>.db`. A separate `einvoicing.db` would not be backed up,
and losing the outbox on a restore would risk re-submitting already-cleared
invoices to the tax authority. All `einvoice_*` tables therefore live
inside each product's own `retail.db` / `clinic.db`, added via
`CREATE TABLE IF NOT EXISTS` only — see `invoice-numbering-audit.md` and
`../../release/wave1b/schema-migration-and-data-safety-report.md`'s
`ensure_schema_version` mechanism, which both products already used.

## Why one shared module instead of two per-product implementations

Retail and Clinic need the identical pipeline: allocate a number, build a
document, submit it, retry it, record what happened. Duplicating that in
two products invites drift (a bug fixed in one silently persisting in the
other). Only the parts that are genuinely product-specific — reading a
`sales` row vs a `clinic_invoices` row — are product-side code
(`einvoice_adapter.py`).

## Why the pipeline is asynchronous, not inline with checkout

The whole product line is offline-first: a sale/invoice completes and
prints with no mandatory network call. JoFotara expects real-time-ish
clearance, which collides with that invariant. The resolution: sale/invoice
completion enqueues a row locally (one fast SQLite write, no network) and
returns immediately; `OutboxWorker` submits it later, out of the request
path entirely. See `outbox-state-machine.md` and `worker.py`'s docstring.

## Why per-company, not per-installation

`commercial_runtime/identity/registry_db.py`'s registry is genuinely
multi-tenant — one install can host more than one company. There is no
single company_id to bind at process-boot time. `OutboxWorker` instances
are created lazily, one per company, cached in a per-process dict in each
product's `app.py`, and resumed automatically at boot for any company that
already has the feature on (`_resume_einvoicing_workers()`).
