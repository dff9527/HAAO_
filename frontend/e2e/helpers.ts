import { expect, type Page } from '@playwright/test';

export async function waitForMockBoard(page: Page) {
  await page.goto('/');
  await expect(page.getByTestId('nav-home')).toBeVisible();
  await expect(page.getByText('Cannot reach the API')).toBeVisible();
  await expect(page.getByTestId('ticket-card-T-014')).toBeVisible({ timeout: 15_000 });
}

export async function openTicket(page: Page, ticketId: string) {
  const opener = page.getByTestId(`ticket-open-${ticketId}`);
  await opener.scrollIntoViewIfNeeded();
  await opener.click();
  await expect(page.getByTestId('ticket-detail')).toBeVisible();
}

export async function closeTicketDetail(page: Page) {
  await page.getByLabel('Close ticket details').click();
  await expect(page.getByTestId('ticket-detail')).toBeHidden();
}
