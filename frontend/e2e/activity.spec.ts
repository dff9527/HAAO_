import { test, expect } from '@playwright/test';
import { waitForMockBoard } from './helpers';

test.describe('Activity observability (mock)', () => {
  test('shows a run with safety and egress audit lines', async ({ page }) => {
    await waitForMockBoard(page);
    await page.getByTestId('nav-activity').click();
    await expect(page.getByTestId('activity-page')).toBeVisible();

    const run = page.getByTestId('activity-run-RUN-demo-1');
    await expect(run).toBeVisible();
    await run.click();

    await expect(run.getByTestId('run-event-egress_attempt').first()).toBeVisible();
    await expect(run.getByText('Blocked a network attempt during tests').first()).toBeVisible();
    await expect(run.getByTestId('run-event-diff_scope_reject')).toBeVisible();
    await expect(run.getByText('Rejected an out-of-scope edit')).toBeVisible();
  });
});
