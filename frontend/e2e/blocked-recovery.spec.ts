import { test, expect } from '@playwright/test';
import { closeTicketDetail, openTicket, waitForMockBoard } from './helpers';

test.describe('blocked ticket recovery (mock)', () => {
  test('shows recovery options on blocked ticket', async ({ page }) => {
    await waitForMockBoard(page);
    await openTicket(page, 'T-013');

    const menu = page.getByTestId('blocked-recovery-menu');
    await expect(menu).toBeVisible();
    await expect(menu.getByText('Recovery options')).toBeVisible();
    await expect(menu.getByRole('button', { name: 'Split into smaller tickets' })).toBeVisible();
    await expect(menu.getByRole('button', { name: 'Change model & retry' })).toBeVisible();
    await expect(menu.getByRole('button', { name: 'Abandon ticket' })).toBeVisible();
    await closeTicketDetail(page);
  });

  test('split action updates blocked ticket', async ({ page }) => {
    await waitForMockBoard(page);
    await openTicket(page, 'T-013');
    await page.getByTestId('blocked-recovery-split-input').fill('Split README edits from login route');
    await page.getByRole('button', { name: 'Split into smaller tickets' }).click();
    await expect(page.getByText('Split T-013 into smaller tickets')).toBeVisible();
    await closeTicketDetail(page);
  });

  test('change model and retry in demo mode', async ({ page }) => {
    await waitForMockBoard(page);
    await openTicket(page, 'T-013');
    await page.getByTestId('blocked-recovery-model-select').selectOption({ index: 0 });
    await page.getByRole('button', { name: 'Change model & retry' }).click();
    await expect(page.getByText('Model changed and ticket retried')).toBeVisible();
    await closeTicketDetail(page);
  });

  test('abandon blocked ticket in demo mode', async ({ page }) => {
    await waitForMockBoard(page);
    await openTicket(page, 'T-013');
    await page.getByTestId('blocked-recovery-abandon-input').fill('Out of scope for this sprint');
    await page.getByRole('button', { name: 'Abandon ticket' }).click();
    await expect(page.getByText('Ticket abandoned')).toBeVisible();
    await closeTicketDetail(page);
  });
});
