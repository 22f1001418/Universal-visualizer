import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  use: {
    baseURL: 'http://localhost:8001',
    headless: true,
  },
  webServer: {
    // Caller is expected to have a backend already running.
    // Set REUSE_BACKEND=1 to skip auto-spawning.
    command: 'echo "expect backend on :8001"',
    url: 'http://localhost:8001/healthz',
    reuseExistingServer: true,
    timeout: 5_000,
  },
});
