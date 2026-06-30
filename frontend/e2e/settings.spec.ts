import { test, expect } from '@playwright/test';
import { waitForMockBoard } from './helpers';

test.describe('Settings surfaces (mock)', () => {
  test('cloud models section and team plane members/runners when enabled', async ({ page }) => {
    await waitForMockBoard(page);
    await page.getByTestId('nav-settings').click();
    await expect(page.getByTestId('nav-settings')).toHaveAttribute('aria-current', 'page');

    await expect(page.getByTestId('settings-cloud-models')).toBeVisible();
    await expect(page.getByText('Default model per role')).toBeVisible();

    const members = page.getByTestId('settings-members');
    await members.locator('summary').click();
    await expect(members.getByText('Alex Owner')).toBeVisible();

    const runners = page.getByTestId('settings-runners');
    await expect(runners).toBeVisible();
    await expect(runners.getByText('mac-studio-runner')).toBeVisible();
  });
});
