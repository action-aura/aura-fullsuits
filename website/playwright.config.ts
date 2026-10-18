import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: 'tests/site',
  webServer: { command: 'npm run preview', url: 'http://localhost:4321/en/', reuseExistingServer: true, timeout: 60_000 },
  use: { baseURL: 'http://localhost:4321' },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
