const API_TOKEN_STORAGE_KEY = 'haao_api_token';

let promptHandler: ((detail: string) => Promise<string | null>) | null = null;

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

export function registerApiTokenPrompt(handler: (detail: string) => Promise<string | null>): void {
  promptHandler = handler;
}

export async function promptForApiToken(detail: string): Promise<string | null> {
  if (promptHandler) {
    return promptHandler(detail);
  }
  const entered = window.prompt(
    detail || 'This server requires an API token. Enter your HAAO API token:',
  );
  return entered?.trim() || null;
}
