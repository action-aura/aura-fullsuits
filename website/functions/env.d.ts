export interface D1Result { meta: { last_row_id: number } }
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
  NOTIFY_PROVIDER?: 'none' | 'webhook';
  NOTIFY_WEBHOOK_URL?: string;
}
