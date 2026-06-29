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
    const ticket = page.getByText('T-001').first();
    if (await ticket.isVisible()) {
      await ticket.click();
      await expect(page.getByLabel('Close ticket details')).toBeVisible();
    }
  });
});

test.describe('visual baselines', () => {
  test('home board baseline', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByLabel('Main navigation')).toBeVisible();
    await expect(page).toHaveScreenshot('home-board.png', { maxDiffPixelRatio: 0.02 });
  });

  test('settings baseline', async ({ page }) => {
    await page.goto('/');
    await page.getByTestId('nav-settings').click();
    await expect(page.getByText('Default model per role')).toBeVisible();
    await expect(page).toHaveScreenshot('settings-page.png', { maxDiffPixelRatio: 0.02 });
  });
});
