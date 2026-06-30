import { test, expect } from '@playwright/test';
import { closeTicketDetail, openTicket, waitForMockBoard } from './helpers';

test.describe('core delivery flow (mock)', () => {
  test('chat proposal → Gate 1 approve → running ticket → Gate 2 accept', async ({ page }) => {
    await waitForMockBoard(page);

    await expect(page.getByTestId('chat-panel')).toBeVisible();
    await expect(page.getByTestId('chat-proposal-R-006')).toBeVisible();
    await expect(page.getByText('filed it as a proposal')).toBeVisible();

    await page.getByTestId('chat-message-input').fill('Add rate limiting to the login endpoint');
    await page.getByTestId('chat-send').click();
    await expect(page.getByText('demo mode')).toBeVisible();

    await openTicket(page, 'T-014');
    await expect(page.getByTestId('gate1-approve')).toBeVisible();
    await page.getByTestId('gate1-approve').click();
    await closeTicketDetail(page);
    await openTicket(page, 'T-014');
    await expect(page.getByTestId('gate1-approve')).toBeHidden();
    await closeTicketDetail(page);

    await openTicket(page, 'T-012');
    const detail = page.getByTestId('ticket-detail');
    await expect(detail.getByText('Activity log')).toBeVisible();
    await expect(detail.getByText('Running: pytest tests/test_crypto.py').first()).toBeVisible();
    await closeTicketDetail(page);

    await openTicket(page, 'T-011');
    await expect(page.getByTestId('acceptance-checklist')).toBeVisible();
    await expect(page.getByTestId('gate2-accept')).toBeVisible();
    await page.getByTestId('gate2-accept').click();
    await closeTicketDetail(page);
    await openTicket(page, 'T-011');
    await expect(page.getByTestId('gate2-accept')).toBeHidden();
    await closeTicketDetail(page);
  });
});
