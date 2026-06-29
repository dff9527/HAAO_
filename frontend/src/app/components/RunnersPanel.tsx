import { useEffect, useState } from 'react';
import { Copy, Loader2, Plus, RefreshCw, ShieldOff } from 'lucide-react';
import { apiClient } from '../api/client';
import type { IdentityContext, RunnerRecord } from '../api/types';
import {
  MOCK_RUNNERS,
  canManageTeam,
  forbiddenMessage,
  formatRelativeHeartbeat,
  isForbiddenError,
  mockTeamPlaneEnabled,
  normalizeRunner,
  runnerStatusBadgeClass,
} from '../teamPlaneUtils';

const FORM_INPUT_CLASS =
  'text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground';

interface Props {
  identityContext: IdentityContext;
  onForbidden?: (message: string) => void;
}

export function RunnersPanel({ identityContext, onForbidden }: Props) {
  const canManage = canManageTeam(identityContext);
  const workspaceId = identityContext.workspace_id;
  const [runners, setRunners] = useState<RunnerRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [label, setLabel] = useState('local-runner');
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
  const [message, setMessage] = useState('');

  async function refresh() {
    setLoading(true);
    try {
      const items = mockTeamPlaneEnabled()
        ? MOCK_RUNNERS
        : await apiClient.listRunners(workspaceId);
      setRunners(items.map(normalizeRunner));
    } catch {
      setRunners(mockTeamPlaneEnabled() ? MOCK_RUNNERS.map(normalizeRunner) : []);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [workspaceId]);

  async function issueToken() {
    setBusy(true);
    setMessage('');
    setIssuedToken(null);
    try {
      if (mockTeamPlaneEnabled()) {
        const mockRunner: RunnerRecord = {
          id: `runner-${Date.now().toString(36)}`,
          workspace_id: workspaceId,
          label: label.trim() || 'local-runner',
          created_at: new Date().toISOString(),
          last_heartbeat_at: null,
          status: 'offline',
          active_lease: null,
        };
        setRunners((prev) => [mockRunner, ...prev]);
        setIssuedToken(`hrun_mock_${Date.now().toString(36)}`);
        return;
      }
      const result = await apiClient.registerRunner({
        workspace_id: workspaceId,
        label: label.trim() || 'local-runner',
      });
      setRunners((prev) => [normalizeRunner(result.runner), ...prev]);
      setIssuedToken(result.token);
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setMessage(error instanceof Error ? error.message : 'Could not issue runner token.');
      }
    } finally {
      setBusy(false);
    }
  }

  async function revokeRunner(runnerId: string) {
    setBusy(true);
    setMessage('');
    try {
      if (mockTeamPlaneEnabled()) {
        setRunners((prev) =>
          prev.map((runner) =>
            runner.id === runnerId
              ? normalizeRunner({ ...runner, revoked_at: new Date().toISOString(), status: 'revoked' })
              : runner,
          ),
        );
        return;
      }
      const updated = await apiClient.revokeRunner(runnerId);
      setRunners((prev) => prev.map((runner) => (runner.id === runnerId ? normalizeRunner(updated) : runner)));
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setMessage(error instanceof Error ? error.message : 'Could not revoke runner.');
      }
    } finally {
      setBusy(false);
    }
  }

  async function rotateRunner(runnerId: string) {
    const existing = runners.find((runner) => runner.id === runnerId);
    if (existing?.label) setLabel(existing.label);
    await revokeRunner(runnerId);
    await issueToken();
  }

  function copyToken() {
    if (!issuedToken) return;
    void navigator.clipboard.writeText(issuedToken);
  }

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-muted-foreground">
        Registered execution runners for this workspace. Code runs on the runner — the control plane only orchestrates.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
        >
          {loading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          Refresh
        </button>
      </div>
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
          <Loader2 size={12} className="animate-spin" />
          Loading runners…
        </div>
      ) : runners.length === 0 ? (
        <p className="text-[11px] text-muted-foreground py-2">No runners registered.</p>
      ) : (
        <div className="space-y-2">
          {runners.map((runner) => (
            <div
              key={runner.id}
              className="flex flex-wrap items-center gap-2 rounded border border-border bg-background px-2.5 py-2"
            >
              <span className="text-xs font-medium text-foreground">{runner.label}</span>
              <span className="font-mono text-[11px] text-muted-foreground">{runner.id}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${runnerStatusBadgeClass(runner.status)}`}>
                {runner.status}
              </span>
              <span className="text-[10px] text-muted-foreground" title={runner.last_heartbeat_at ?? undefined}>
                heartbeat {formatRelativeHeartbeat(runner.last_heartbeat_at)}
              </span>
              {runner.active_lease?.ticket_id && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300">
                  lease · {runner.active_lease.ticket_id}
                </span>
              )}
              {canManage && runner.status !== 'revoked' && (
                <div className="ml-auto flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => void rotateRunner(runner.id)}
                    disabled={busy}
                    className="text-[10px] px-2 py-1 rounded border border-border hover:bg-muted disabled:opacity-50"
                    title="Revoke and issue a new token"
                  >
                    Rotate
                  </button>
                  <button
                    type="button"
                    onClick={() => void revokeRunner(runner.id)}
                    disabled={busy}
                    className="h-7 w-7 flex items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                    aria-label={`Revoke ${runner.label}`}
                  >
                    <ShieldOff size={12} />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {canManage && (
        <div className="rounded border border-border bg-background px-3 py-3 space-y-2">
          <div className="text-xs font-medium text-foreground">Issue runner token</div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Runner label"
              className={`flex-1 min-w-[160px] ${FORM_INPUT_CLASS}`}
            />
            <button
              type="button"
              onClick={() => void issueToken()}
              disabled={busy}
              className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              {busy ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
              Issue token
            </button>
          </div>
          {issuedToken && (
            <div className="rounded border border-amber-200 dark:border-amber-900 bg-amber-50/50 dark:bg-amber-950/30 px-2.5 py-2 space-y-1">
              <p className="text-[11px] text-amber-800 dark:text-amber-200">
                Copy this token now — it will not be shown again.
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-[10px] font-mono break-all text-foreground">{issuedToken}</code>
                <button
                  type="button"
                  onClick={copyToken}
                  className="h-7 w-7 flex items-center justify-center rounded border border-border hover:bg-muted"
                  aria-label="Copy token"
                >
                  <Copy size={12} />
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {!canManage && (
        <p className="text-[11px] text-muted-foreground">Runner token lifecycle requires owner or admin.</p>
      )}
      {message && <p className="text-[11px] text-red-600 dark:text-red-400">{message}</p>}
    </div>
  );
}
