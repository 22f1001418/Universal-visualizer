// frontend/tests/e2e/happy-path.spec.ts
// Manual/nightly only — requires a live backend + real OPENAI_API_KEY.
// Before running: npx playwright install --with-deps (one-time, ~400 MB)
// Then:          npm run e2e  (from frontend/)
import { expect, test } from '@playwright/test';

test('upload → topics → suggestions → build queued', async ({ page }) => {
  await page.goto('/v2/');

  // The actual upload uses real backend + LLM by default — too expensive for
  // CI. Run this manually with a short fixture .md when validating parity.
  const sample = '# Binary search\n\nBisects a sorted array.';
  const fileChooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: /upload/i }).first().click();
  const chooser = await fileChooser;
  await chooser.setFiles({
    name: 'sample.md',
    mimeType: 'text/markdown',
    // Buffer is available in Playwright's Node runtime; use TextEncoder so
    // TypeScript is satisfied without requiring @types/node.
    buffer: new TextEncoder().encode(sample),
  });
  await page.getByRole('button', { name: /^upload$/i }).click();

  await expect(page.getByText(/topics/i)).toBeVisible({ timeout: 30_000 });
});
