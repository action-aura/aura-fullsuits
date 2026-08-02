# Phase 9.5E — Expense Attachment Security Contract

Binding rules for `app/expenses/attachments.py`. No malware/virus scanning is integrated or claimed anywhere in this module or its docs — allowlisting (MIME + magic bytes) only, per the governing spec's explicit instruction.

## Storage

Private filesystem directory (`OWNER_EXPENSE_ATTACHMENT_DIR`, default `var/expense-attachments/`, `var/expense-attachments-test/` in `TestingConfig`) — never under `static/` or any Flask-served path. Every read is mediated by `read_attachment_bytes()`, which performs no authorization itself (the caller — the download route — must authorization-check `attachment.expense_id` first) but does perform safe, traversal-proof path resolution: `os.path.realpath()` the resolved candidate and verify it's still inside the storage root via `os.path.commonpath()` before ever opening a file.

## Storage key, never a client-supplied path

`storage_key = f"{expense_id}/{uuid4().hex}"` — entirely server-generated. `original_filename` is stored only for the `Content-Disposition` header on download, sanitized separately via `_sanitize_display_filename()` (strips directory components, null bytes, and any character outside `[A-Za-z0-9._-]`) — no client-supplied string ever contributes to a filesystem path.

## Content validation (upload)

1. Non-empty (`ATTACHMENT_EMPTY`).
2. `<= EXPENSE_ATTACHMENT_MAX_BYTES` (default 10MB — `ATTACHMENT_TOO_LARGE`).
3. `declared_content_type` in the allowlist (`application/pdf`, `image/jpeg`, `image/png` only — `ATTACHMENT_TYPE_NOT_ALLOWED`). HTML, SVG, executables, and any script-capable type are structurally impossible to upload — they're simply not in the allowlist.
4. Magic-byte signature check (`_detect_magic_bytes()`) confirms the actual byte content matches the declared type (`%PDF-`, `\xff\xd8\xff`, `\x89PNG\r\n\x1a\n`) — `ATTACHMENT_CONTENT_MISMATCH` on any mismatch, including a same-extension file whose real content is something else entirely.

## Logging/audit discipline

`upload_attachment()`'s audit record carries only `content_type` and `size_bytes` — never the raw bytes, the storage path, or the original filename. Application logs never receive attachment bytes either (nothing in this module calls a logger with `content`).

## Archival

`archive_attachment()` sets `status=ARCHIVED`; `read_attachment_bytes()` refuses to serve an archived attachment (`ATTACHMENT_ARCHIVED`) even if the caller is otherwise authorized — archival is a real access change, not just a UI hide.

## Duplicate signal (separate concern)

`content_hash` (SHA-256 of the raw bytes) feeds `app/expenses/duplicates.py`'s `EXACT_ATTACHMENT_HASH` signal and the approval fingerprint's attachment-hash set — the same hash serves both purposes, never computed twice.
