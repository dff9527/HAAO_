const API_TOKEN_STORAGE_KEY = 'haao_api_token';
export const AUTH_REQUIRED_STORAGE_KEY = 'haao_auth_required';

export type AuthReason = 'api_token_required' | 'login_required' | 'forbidden';

export interface AuthChallenge {
  reason: AuthReason;
  detail: string;
}

export type AuthPromptResult =
  | { action: 'token'; token: string }
  | { action: 'login' }
  | { action: 'dismiss' }
  | null;

let promptHandler: ((challenge: AuthChallenge) => Promise<AuthPromptResult>) | null = null;
let pendingAuthPrompt: Promise<AuthPromptResult> | null = null;
let pendingAuthReason: AuthReason | null = null;
let resolvePromptReady: (() => void) | null = null;
const promptReady = new Promise<void>((resolve) => {
  resolvePromptReady = resolve;
});

async function waitForPromptHandler(): Promise<void> {
  if (promptHandler) return;
  await Promise.race([
    promptReady,
    new Promise<void>((resolve) => {
      window.setTimeout(resolve, 250);
    }),
  ]);
}

export function getStoredApiToken(): string {
  try {
    return localStorage.getItem(API_TOKEN_STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

export function setStoredApiToken(token: string): void {
  try {
    const trimmed = token.trim();
    if (trimmed) {
      localStorage.setItem(API_TOKEN_STORAGE_KEY, trimmed);
    } else {
      localStorage.removeItem(API_TOKEN_STORAGE_KEY);
    }
  } catch {
    // Ignore storage failures (private mode, etc.).
  }
}

export function getAuthRequiredFlag(): AuthReason | null {
  try {
    const value = localStorage.getItem(AUTH_REQUIRED_STORAGE_KEY);
    if (value === 'api_token_required' || value === 'login_required' || value === 'forbidden') {
      return value;
    }
    if (value === '1' || value === 'true') {
      return 'api_token_required';
    }
    return null;
  } catch {
    return null;
  }
}

export function markAuthRequired(reason: AuthReason): void {
  try {
    localStorage.setItem(AUTH_REQUIRED_STORAGE_KEY, reason);
  } catch {
    // Ignore storage failures.
  }
}

export function clearAuthRequiredFlag(): void {
  try {
    localStorage.removeItem(AUTH_REQUIRED_STORAGE_KEY);
  } catch {
    // Ignore storage failures.
  }
}

export function clearStoredCredentials(): void {
  try {
    localStorage.removeItem(API_TOKEN_STORAGE_KEY);
    localStorage.removeItem(AUTH_REQUIRED_STORAGE_KEY);
    localStorage.removeItem('haao_user_id');
    localStorage.removeItem('haao_workspace_id');
  } catch {
    // Ignore storage failures.
  }
}

export function registerAuthPrompt(
  handler: (challenge: AuthChallenge) => Promise<AuthPromptResult>,
): void {
  promptHandler = handler;
  resolvePromptReady?.();
}

/** @deprecated Use registerAuthPrompt */
export function registerApiTokenPrompt(handler: (detail: string) => Promise<string | null>): void {
  registerAuthPrompt(async (challenge) => {
    if (challenge.reason !== 'api_token_required') {
      return { action: 'dismiss' };
    }
    const token = await handler(challenge.detail);
    return token ? { action: 'token', token } : null;
  });
}

export async function promptForAuth(challenge: AuthChallenge): Promise<AuthPromptResult> {
  if (pendingAuthPrompt && pendingAuthReason === challenge.reason) {
    return pendingAuthPrompt;
  }

  pendingAuthReason = challenge.reason;
  pendingAuthPrompt = (async () => {
    try {
      await waitForPromptHandler();
      if (promptHandler) {
        return promptHandler(challenge);
      }
      if (challenge.reason === 'api_token_required') {
        const entered = window.prompt(
          challenge.detail || 'This server requires an API token. Enter your HAAO API token:',
        );
        return entered?.trim() ? { action: 'token', token: entered.trim() } : null;
      }
      if (challenge.reason === 'login_required') {
        const proceed = window.confirm(`${challenge.detail}\n\nOpen the sign-in page?`);
        return proceed ? { action: 'login' } : null;
      }
      window.alert(challenge.detail || 'You do not have access to this workspace.');
      return { action: 'dismiss' };
    } finally {
      pendingAuthPrompt = null;
      pendingAuthReason = null;
    }
  })();

  return pendingAuthPrompt;
}

/** @deprecated Use promptForAuth */
export async function promptForApiToken(detail: string): Promise<string | null> {
  const result = await promptForAuth({ reason: 'api_token_required', detail });
  if (result?.action === 'token') return result.token;
  return null;
}
