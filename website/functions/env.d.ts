export interface D1Result { meta: { last_row_id: number; changes?: number } }
export interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  run(): Promise<D1Result>;
  first<T = Record<string, unknown>>(): Promise<T | null>;
}
export interface D1Database { prepare(sql: string): D1PreparedStatement }
export interface Env {
  DB: D1Database;
  TURNSTILE_SECRET?: string;
  IP_SALT: string;
  ALLOW_NO_TURNSTILE?: string;
  // Comma list of providers, e.g. "smtp,owner". Unset or "none" sends nothing.
  NOTIFY_PROVIDER?: string;
  OWNER_INTAKE_URL?: string;
  OWNER_INTAKE_SECRET?: string;
  NOTIFY_WEBHOOK_URL?: string;
  SMTP_HOST?: string;
  SMTP_PORT?: string;
  SMTP_USER?: string;
  SMTP_PASS?: string;
  NOTIFY_TO?: string;
  // Customer accounts (BFF to Owner). ACCOUNTS_ENABLED is "1" or "true" to switch the feature on;
  // anything else (unset, "0", "") means every account page and API answers 404.
  ACCOUNTS_ENABLED?: string;
  OWNER_ACCOUNT_API_URL?: string;
  OWNER_ACCOUNT_KEY_ID?: string;
  // Pages secret (wrangler pages secret put). Never in wrangler.toml.
  OWNER_ACCOUNT_SECRET?: string;
  // Local dev only: wrangler pages dev sends no CF-Connecting-IP. "1" lets the BFF use 127.0.0.1 then.
  ALLOW_NO_CLIENT_IP?: string;
}
