// Static site. Languages are plain route folders (/ar/, /en/) rather than
// Astro's i18n router: the root "/" is our own indexable language chooser,
// and the SEO plan forbids a server-side language redirect.
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://actionaura.me',
  // Tests build the same site twice (accounts on / off) into separate folders; production never sets this.
  outDir: process.env.AURA_OUT_DIR || './dist',
  trailingSlash: 'always',
  build: { format: 'directory' },
  integrations: [
    sitemap({
      // Alternates per locale in the sitemap, as the SEO plan requires (hreflang in head AND sitemap).
      i18n: { defaultLocale: 'en', locales: { ar: 'ar', en: 'en' } },
      // 404 and the noindex demo-sent pages must not be submitted (Search Console flags noindex URLs in a sitemap).
      filter: (page) => !page.includes('/404') && !page.includes('/contact/sent/') && !page.includes('/account/'),
      // The root chooser has no locale: the i18n option groups it with /ar/ and /en/ and lists it as a second 'en'
      // (duplicate hreflang). It is the x-default in every page head, so it is dropped from sitemap alternates.
      serialize: (item) => (item.url === 'https://actionaura.me/'
        ? { ...item, links: undefined }
        : { ...item, links: item.links?.filter((l) => l.url !== 'https://actionaura.me/') }),
    }),
  ],
});
