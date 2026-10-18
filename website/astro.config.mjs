// Static site. Languages are plain route folders (/ar/, /en/) rather than
// Astro's i18n router: the root "/" is our own indexable language chooser,
// and the SEO plan forbids a server-side language redirect.
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://actionaura.me',
  trailingSlash: 'always',
  build: { format: 'directory' },
  integrations: [
    sitemap({
      filter: (page) => !page.includes('/404'),
    }),
  ],
});
