// Builds the site twice (accounts on / off) into dist-on and dist-off, then runs the Playwright account suite
// against wrangler pages dev + the fake Owner. One command, so the two builds can never be stale.
import { spawnSync } from 'node:child_process';

const run = (cmd, env = {}) => {
  const r = spawnSync(cmd, { shell: true, stdio: 'inherit', env: { ...process.env, ...env } });
  if (r.status !== 0) process.exit(r.status ?? 1);
};
run('npx astro build', { PUBLIC_ACCOUNTS_ENABLED: '1', AURA_OUT_DIR: 'dist-on' });
run('npx astro build', { PUBLIC_ACCOUNTS_ENABLED: '', AURA_OUT_DIR: 'dist-off' });
run(`npx playwright test -c playwright.accounts.config.ts ${process.argv.slice(2).join(' ')}`);
