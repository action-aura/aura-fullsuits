-- Demo-request leads. Stored BEFORE any notification so a failed email/webhook
-- never loses a lead. ip_hash is SHA-256(IP_SALT + client IP): used only for
-- the per-IP rate limit; the raw IP is never stored.
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  name TEXT NOT NULL,
  phone TEXT NOT NULL,
  business_type TEXT NOT NULL DEFAULT '',
  business_name TEXT NOT NULL DEFAULT '',
  branches TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  lang TEXT NOT NULL DEFAULT 'en',
  ip_hash TEXT NOT NULL,
  notify_error TEXT
);
CREATE INDEX IF NOT EXISTS leads_ip_time ON leads (ip_hash, created_at);
