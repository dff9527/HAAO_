import { test, expect } from '@playwright/test';

test.describe('HAAO smoke', () => {
  test('loads home board shell', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByLabel('Main navigation')).toBeVisible();
    await expect(page.getByTestId('nav-home')).toBeVisible();
  });

  test('navigates primary pages', async ({ page }) => {
    await page.goto('/');
    for (const id of ['nav-activity', 'nav-insights', 'nav-decisions', 'nav-inbox', 'nav-settings']) {
      await page.getByTestId(id).click();
      await expect(page.getByTestId(id)).toHaveAttribute('aria-current', 'page');
    }
  });

  test('opens settings cloud models section', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-settings').click();
    await expect(page.getByText('Cloud models & API keys')).toBeVisible();
  });

  test('opens a ticket detail from mock board', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('ticket-open-T-014')).toBeVisible({ timeout: 15_000 });
    await page.getByTestId('ticket-open-T-014').click();
    await expect(page.getByTestId('ticket-detail')).toBeVisible();
  });
});

test.describe('visual baselines', () => {
  test('home board baseline', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByLabel('Main navigation')).toBeVisible();
    await expect(page.getByTestId('ticket-card-T-014')).toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveScreenshot('home-board.png', { maxDiffPixelRatio: 0.02 });
  });

  test('settings baseline', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-settings').click();
    await expect(page.getByTestId('settings-cloud-models')).toBeVisible();
    await expect(page).toHaveScreenshot('settings-page.png', { maxDiffPixelRatio: 0.02 });
  });

  test('decisions baseline', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-decisions').click();
    await expect(page.getByTestId('decision-group-gate1_scope')).toBeVisible();
    await expect(page).toHaveScreenshot('decisions-page.png', { maxDiffPixelRatio: 0.02 });
  });

  test('activity baseline', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-activity').click();
    await expect(page.getByTestId('activity-run-RUN-demo-1')).toBeVisible();
    await page.getByTestId('activity-run-RUN-demo-1').click();
    await expect(page).toHaveScreenshot('activity-page.png', { maxDiffPixelRatio: 0.02 });
  });

  test('benchmark report baseline', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-insights').click();
    await page.getByTestId('insights-benchmark-link').click();
    await expect(page.getByTestId('benchmark-report-content')).toBeVisible();
    await expect(page).toHaveScreenshot('benchmark-report.png', { maxDiffPixelRatio: 0.02 });
  });
});
