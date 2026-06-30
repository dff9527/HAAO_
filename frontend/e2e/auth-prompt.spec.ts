import { test, expect } from '@playwright/test';
import { fulfillHealthyApiRoute, mockHealthyApi, mockTicketsAuthChallenge } from './auth-helpers';

test.describe('auth prompt recovery (mock)', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('haao-onboarding-dismissed', '1');
    });
  });

  test('self-heals stale auth_required flag when backend is healthy', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('haao_auth_required', 'api_token_required');
      localStorage.setItem('haao_api_token', 'stale-token');
    });
    await mockHealthyApi(page);
    await page.goto('/');

    await expect(page.getByTestId('nav-home')).toBeVisible();
    await expect(page.getByTestId('auth-prompt-dialog')).toHaveCount(0);
    await expect(page.getByText('Cannot reach the API')).toHaveCount(0);

    const authRequired = await page.evaluate(() => localStorage.getItem('haao_auth_required'));
    expect(authRequired).toBeNull();
  });

  test('shows token prompt for api_token_required and proceeds with a valid token', async ({ page }) => {
    await mockTicketsAuthChallenge(page, {
      status: 401,
      reason: 'api_token_required',
      detail: 'API token required',
    });
    await page.goto('/');

    const dialog = page.getByTestId('auth-prompt-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('heading', { name: 'API token required' })).toBeVisible();
    await expect(page.getByTestId('auth-prompt-token-input')).toBeVisible();

    await page.getByTestId('auth-prompt-token-input').fill('valid-token');
    await dialog.getByRole('button', { name: 'Save & retry' }).click();

    await expect(dialog).toHaveCount(0);
    await expect(page.getByText('Cannot reach the API')).toHaveCount(0);
    await expect(page.getByTestId('nav-home')).toBeVisible();
  });

  test('shows sign-in prompt for login_required instead of token input', async ({ page }) => {
    await mockTicketsAuthChallenge(page, {
      status: 401,
      reason: 'login_required',
      detail: 'Login required',
    });
    await page.goto('/');

    const dialog = page.getByTestId('auth-prompt-dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('heading', { name: 'Sign in required' })).toBeVisible();
    await expect(page.getByTestId('auth-prompt-sign-in-button')).toBeVisible();
    await expect(page.getByTestId('auth-prompt-token-input')).toHaveCount(0);
  });

  test('reset link clears stored credentials and reloads to a working app', async ({ page }) => {
    let ticketsChallengeActive = true;

    await page.route('**/api/**', async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      const isTicketsList = pathname.endsWith('/tickets') || pathname.includes('/tickets?');

      if (isTicketsList && ticketsChallengeActive) {
        return route.fulfill({
          status: 401,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'API token required', reason: 'api_token_required' }),
        });
      }

      return fulfillHealthyApiRoute(route);
    });

    await page.goto('/');
    await expect(page.getByTestId('auth-prompt-dialog')).toBeVisible();
    ticketsChallengeActive = false;

    await page.getByTestId('auth-prompt-reset-link').click();
    await page.waitForLoadState('load');

    await expect(page.getByTestId('auth-prompt-dialog')).toHaveCount(0);
    await expect(page.getByText('Cannot reach the API')).toHaveCount(0);

    const storage = await page.evaluate(() => ({
      token: localStorage.getItem('haao_api_token'),
      authRequired: localStorage.getItem('haao_auth_required'),
    }));
    expect(storage.token).toBeNull();
    expect(storage.authRequired).toBeNull();
  });
});
