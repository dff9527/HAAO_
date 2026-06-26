import { useEffect, useState } from 'react';
import { registerApiTokenPrompt } from '../api/authToken';

export function ApiTokenPrompt() {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState('');
  const [tokenInput, setTokenInput] = useState('');
  const [resolver, setResolver] = useState<((value: string | null) => void) | null>(null);

  useEffect(() => {
    registerApiTokenPrompt((message) => new Promise((resolve) => {
      setDetail(message || 'This server requires an API token.');
      setTokenInput('');
      setResolver(() => resolve);
      setOpen(true);
    }));
  }, []);

  function closeWith(value: string | null) {
    resolver?.(value);
    setResolver(null);
    setOpen(false);
    setTokenInput('');
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <div
        className="w-full max-w-md rounded-xl border border-border bg-card p-4 shadow-lg"
        role="dialog"
        aria-labelledby="api-token-title"
        aria-modal="true"
      >
        <h2 id="api-token-title" className="text-sm font-semibold text-foreground">
          API token required
        </h2>
        <p className="text-xs text-muted-foreground mt-1.5 leading-relaxed">{detail}</p>
        <input
          type="password"
          value={tokenInput}
          onChange={(event) => setTokenInput(event.target.value)}
          placeholder="Paste your HAAO API token"
          autoFocus
          className="mt-3 w-full font-mono text-xs bg-muted border border-border rounded px-2.5 py-2 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              closeWith(tokenInput.trim() || null);
            }
          }}
        />
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => closeWith(null)}
            className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => closeWith(tokenInput.trim() || null)}
            disabled={!tokenInput.trim()}
            className="text-xs px-2.5 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            Save &amp; retry
          </button>
        </div>
      </div>
    </div>
  );
}
