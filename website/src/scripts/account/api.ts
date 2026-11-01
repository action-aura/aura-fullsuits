// Browser side of the account BFF. One tiny wrapper so every page talks to /api/account/* the same way:
// same-origin, JSON, the custom header the CSRF guard looks for, and never a throw (status 0 = network failure).
export type Strings = Record<string, any>;
export type Lang = 'ar' | 'en';

export interface ApiResult<T = Record<string, any>> {
  status: number;
  body: T;
  retryAfter: number | null;
}

export const lang = (): Lang => (document.documentElement.lang === 'ar' ? 'ar' : 'en');

export const accountUrl = (sub = ''): string => `/${lang()}/account/${sub ? sub + '/' : ''}`;

export function strings(): Strings {
  const node = document.getElementById('acct-t');
  return node?.textContent ? JSON.parse(node.textContent) : {};
}

export async function post(path: string, payload: Record<string, unknown> = {}): Promise<ApiResult> {
  try {
    const res = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'content-type': 'application/json', 'x-aura-account': '1' },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    const ra = Number(res.headers.get('retry-after'));
    return { status: res.status, body, retryAfter: Number.isFinite(ra) && ra > 0 ? ra : null };
  } catch {
    return { status: 0, body: {}, retryAfter: null };
  }
}

/** Words for failures every page shares. Page-specific ones (wrong password, ...) are handled before this. */
export function commonError(res: ApiResult, t: Strings): string {
  if (res.status === 0) return t.common.errorNetwork;
  if (res.status === 429) return t.common.errorRate;
  if (res.status === 503) return t.common.errorUnavailable;
  return t.common.errorGeneric;
}

export function fill(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}
