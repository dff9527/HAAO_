import { useLayoutEffect, useState } from 'react';
import { apiClient } from '../api/client';
import {
  clearStoredCredentials,
  registerAuthPrompt,
  type AuthChallenge,
  type AuthPromptResult,
} from '../api/authToken';

type Resolver = (value: AuthPromptResult) => void;

export function AuthPrompt() {
  const [open, setOpen] = useState(false);
  const [challenge, setChallenge] = useState<AuthChallenge | null>(null);
  const [tokenInput, setTokenInput] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [resolver, setResolver] = useState<Resolver | null>(null);

  useLayoutEffect(() => {
    registerAuthPrompt((nextChallenge) => new Promise<AuthPromptResult>((resolve) => {
      setChallenge(nextChallenge);
      setTokenInput('');
      setLoginError(null);
      setLoginLoading(false);
      setResolver(() => resolve);
      setOpen(true);
    }));
  }, []);

  function closeWith(value: AuthPromptResult) {
    resolver?.(value);
    setResolver(null);
    setOpen(false);
    setTokenInput('');
    setChallenge(null);
    setLoginError(null);
    setLoginLoading(false);
  }

  async function handleSignIn() {
    setLoginLoading(true);
    setLoginError(null);
    try {
      const url = await apiClient.getOidcLoginUrl();
      closeWith({ action: 'login' });
      window.location.assign(url);
    } catch {
      setLoginError('Could not start sign-in. Try again or reset stored credentials.');
      setLoginLoading(false);
    }
  }

  function handleReset() {
    clearStoredCredentials();
    window.location.reload();
  }

  if (!open || !challenge) return null;

  const title = challenge.reason === 'api_token_required'
    ? 'API token required'
    : challenge.reason === 'login_required'
      ? 'Sign in required'
      : 'Access denied';

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 px-4">
      <div
        className="w-full max-w-md rounded-xl border border-border bg-card p-4 shadow-lg"
        role="dialog"
        aria-labelledby="auth-prompt-title"
        aria-modal="true"
        data-testid="auth-prompt-dialog"
      >
        <h2 id="auth-prompt-title" className="text-sm font-semibold text-foreground">
          {title}
        </h2>
        <p className="text-xs text-muted-foreground mt-1.5 leading-relaxed" data-testid="auth-prompt-detail">
          {challenge.detail}
        </p>

        {challenge.reason === 'api_token_required' && (
          <input
            type="password"
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
            placeholder="Paste your HAAO API token"
            autoFocus
            data-testid="auth-prompt-token-input"
            className="mt-3 w-full font-mono text-xs bg-muted border border-border rounded px-2.5 py-2 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                const token = tokenInput.trim();
                if (token) closeWith({ action: 'token', token });
              }
            }}
          />
        )}

        {challenge.reason === 'login_required' && loginError && (
          <p className="mt-2 text-xs text-destructive">{loginError}</p>
        )}

        <button
          type="button"
          onClick={handleReset}
          data-testid="auth-prompt-reset-link"
          className="mt-3 text-[11px] text-muted-foreground underline underline-offset-2 hover:text-foreground"
        >
          Reset / clear stored credentials
        </button>

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => closeWith(null)}
            className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors"
          >
            Cancel
          </button>
          {challenge.reason === 'api_token_required' && (
            <button
              type="button"
              onClick={() => {
                const token = tokenInput.trim();
                if (token) closeWith({ action: 'token', token });
              }}
              disabled={!tokenInput.trim()}
              className="text-xs px-2.5 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              Save &amp; retry
            </button>
          )}
          {challenge.reason === 'login_required' && (
            <button
              type="button"
              onClick={() => void handleSignIn()}
              disabled={loginLoading}
              data-testid="auth-prompt-sign-in-button"
              className="text-xs px-2.5 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {loginLoading ? 'Redirecting…' : 'Sign in'}
            </button>
          )}
          {challenge.reason === 'forbidden' && (
            <button
              type="button"
              onClick={() => closeWith({ action: 'dismiss' })}
              className="text-xs px-2.5 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90"
            >
              OK
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
