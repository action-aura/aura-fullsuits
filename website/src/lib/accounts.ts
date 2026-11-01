import { LANGS, type Lang } from '../i18n';

// BUILD-time switch. Off (the default): the account pages are not generated and no link to them is rendered,
// so the public site is byte-for-byte what it was before accounts existed. The RUNTIME switch (ACCOUNTS_ENABLED
// on Cloudflare Pages) is separate and lives in functions/_lib/account-pages.ts: it 404s the pages and the API.
// For practice, set both. Either one alone leaves accounts invisible or unreachable, never half-working.
export const ACCOUNTS_ON = import.meta.env.PUBLIC_ACCOUNTS_ENABLED === '1';

const SUB = /^[a-z0-9-]*$/;

/** /en/account/ or /en/account/downloads/ ; kept apart from pagePath() whose slugs are one segment. */
export function accountPath(lang: Lang, sub = ''): string {
  if (!SUB.test(sub)) throw new Error(`bad account sub-path: ${sub}`);
  return sub ? `/${lang}/account/${sub}/` : `/${lang}/account/`;
}

/** getStaticPaths for every account page: both languages when the build switch is on, no pages at all when off. */
export function accountStaticPaths(): Array<{ params: { lang: Lang } }> {
  return ACCOUNTS_ON ? LANGS.map((lang) => ({ params: { lang } })) : [];
}
