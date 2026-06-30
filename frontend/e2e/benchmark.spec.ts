import { test, expect } from '@playwright/test';
import { waitForMockBoard } from './helpers';

test.describe('benchmark report view (mock)', () => {
  test('renders report with sample size and variance framing', async ({ page }) => {
    await waitForMockBoard(page);
    await page.getByTestId('nav-insights').click();
    await page.getByTestId('insights-benchmark-link').click();

    await expect(page.getByTestId('benchmark-report-page')).toBeVisible();
    await expect(page.getByTestId('benchmark-report-content')).toBeVisible();
    await expect(page.getByText('66.7% (n=6)')).toBeVisible();
    await expect(page.getByText(/σ²|trial mean/i).first()).toBeVisible();
    await expect(page.getByRole('heading', { name: 'click' })).toBeVisible();
    await expect(page.getByText('Small-n runs are indicative')).toBeVisible();
  });
});
