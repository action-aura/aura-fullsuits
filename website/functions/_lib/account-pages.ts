// Runtime switch for the account PAGES. The pages are static files; this middleware sits in front of
// /en/account/* and /ar/account/* and answers 404 unless ACCOUNTS_ENABLED is on, so flipping the Pages
// variable hides every account page at once without a rebuild. (The build-time flag PUBLIC_ACCOUNTS_ENABLED
// decides whether the pages and the links to them exist in the build at all.)
// Account pages carry a session, so they are never cached by a shared cache and never indexed.
import type { Env } from '../env';
import { accountsEnabled, notFound } from './account-gate';

export async function accountPagesMiddleware(ctx: { env: Env; next: () => Promise<Response> }): Promise<Response> {
  if (!accountsEnabled(ctx.env)) return notFound();
  const res = await ctx.next();
  const out = new Response(res.body, res);
  out.headers.set('cache-control', 'no-store');
  out.headers.set('x-robots-tag', 'noindex, nofollow');
  return out;
}
