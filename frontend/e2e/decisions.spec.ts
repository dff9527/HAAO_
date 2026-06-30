import { test, expect } from '@playwright/test';
import { waitForMockBoard } from './helpers';

test.describe('PO Decision Center (mock)', () => {
  test('renders gate, acceptance, blocked, and high-risk groups', async ({ page }) => {
    await waitForMockBoard(page);
    await page.getByTestId('nav-decisions').click();
    await expect(page.getByTestId('nav-decisions')).toHaveAttribute('aria-current', 'page');

    await expect(page.getByTestId('decision-group-gate1_scope')).toBeVisible();
    await expect(page.getByTestId('decision-group-gate2_acceptance')).toBeVisible();
    await expect(page.getByTestId('decision-group-blocked')).toBeVisible();
    await expect(page.getByTestId('decision-group-high_risk')).toBeVisible();

    await expect(page.getByTestId('decision-item-T-019')).toBeVisible();
    await expect(page.getByTestId('decision-item-T-003')).toBeVisible();
    await expect(page.getByTestId('decision-item-T-002')).toBeVisible();
    await expect(page.getByText('Nothing here right now.')).toBeVisible();
  });
});
