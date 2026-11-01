import { defineConfig } from '@playwright/test';

// Account flow against the REAL Pages Functions (wrangler pages dev) with a FAKE Owner that verifies the HMAC.
// Build both variants first (npm run test:accounts does it):
//   dist-on  = PUBLIC_ACCOUNTS_ENABLED=1   dist-off = flag unset
// Four servers:
//   8799 fake Owner
//   8790 dist-on  + ACCOUNTS_ENABLED=1   (the working feature)
//   8791 dist-off + ACCOUNTS_ENABLED=0   (what the public site is today: nothing visible)
//   8792 dist-on  + ACCOUNTS_ENABLED=0   (pages exist in the build, runtime switch is off: all of it must 404)
// wrangler needs Node 22; the repo's local Node is 20, hence the npx node@22 wrapper (see docs/deploy.md).
const WRANGLER = 'npx -y node@22.12.0 node_modules/wrangler/bin/wrangler.js pages dev';
const OWNER = 'http://127.0.0.1:8799/api/website/v1/account';
const SECRET = 'fake-owner-shared-secret-0123456789abcdef'; // pragma: allowlist secret  (the fake's own secret, not a real one)
const bindings = (enabled: string) =>
  [`ACCOUNTS_ENABLED=${enabled}`, `OWNER_ACCOUNT_API_URL=${OWNER}`, 'OWNER_ACCOUNT_KEY_ID=k1', `OWNER_ACCOUNT_SECRET=${SECRET}`,
    'ALLOW_NO_TURNSTILE=1', 'ALLOW_NO_CLIENT_IP=1', 'IP_SALT=test-salt']
    .map((b) => `--binding ${b}`).join(' ');
const server = (dist: string, port: number, enabled: string) => ({
  command: `${WRANGLER} ${dist} --port ${port} --ip 127.0.0.1 --persist-to .wrangler/acct-${port} ${bindings(enabled)}`,
  url: `http://127.0.0.1:${port}/en/`,
  reuseExistingServer: true,
  timeout: 180_000,
});

export default defineConfig({
  testDir: 'tests/accounts',
  workers: 1, // one fake Owner with shared state
  timeout: 60_000,
  reporter: [['list']],
  use: { browserName: 'chromium' },
  webServer: [
    { command: 'node tests/accounts/fake-owner.mjs', url: 'http://127.0.0.1:8799/__health', reuseExistingServer: true, timeout: 30_000 },
    server('dist-on', 8790, '1'),
    server('dist-off', 8791, '0'),
    server('dist-on', 8792, '0'),
  ],
});
