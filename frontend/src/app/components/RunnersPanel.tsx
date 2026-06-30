import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, Copy, Loader2, Plus, RefreshCw, ShieldOff } from 'lucide-react';
import { apiClient } from '../api/client';
import type { IdentityContext, RunnerRecord } from '../api/types';
import {
  MOCK_RUNNERS,
  activeRunners,
  buildRunnerStartCommand,
  canManageTeam,
  forbiddenMessage,
  formatRelativeHeartbeat,
  hasConnectedRunner,
  isForbiddenError,
  mockTeamPlaneEnabled,
  normalizeRunner,
  runnerStatusBadgeClass,
} from '../teamPlaneUtils';

const FORM_INPUT_CLASS =
  'text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground';
const RUNNER_POLL_MS = 30_000;

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
  const [issuedRunnerId, setIssuedRunnerId] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [copied, setCopied] = useState<'token' | 'command' | null>(null);

  const visibleRunners = useMemo(() => activeRunners(runners.map(normalizeRunner)), [runners]);
  const connected = hasConnectedRunner(visibleRunners);

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

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refresh();
    }, RUNNER_POLL_MS);
    return () => window.clearInterval(timer);
  }, [workspaceId]);

  async function addRunner() {
    setBusy(true);
    setMessage('');
    setIssuedToken(null);
    setIssuedRunnerId(null);
    const runnerLabel = label.trim() || 'local-runner';
    try {
      if (mockTeamPlaneEnabled()) {
        const runnerId = `runner-${Date.now().toString(36)}`;
        const mockRunner: RunnerRecord = {
          id: runnerId,
          workspace_id: workspaceId,
          label: runnerLabel,
          created_at: new Date().toISOString(),
          last_heartbeat_at: null,
          status: 'offline',
          active_lease: null,
        };
        setRunners((prev) => [mockRunner, ...prev]);
        setIssuedRunnerId(runnerId);
        setIssuedToken(`hrun_mock_${Date.now().toString(36)}`);
        return;
      }
      const result = await apiClient.registerRunner({
        workspace_id: workspaceId,
        label: runnerLabel,
      });
      setRunners((prev) => [normalizeRunner(result.runner), ...prev]);
      setIssuedRunnerId(result.runner.id);
      setIssuedToken(result.token);
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setMessage(error instanceof Error ? error.message : 'Could not register runner.');
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

  async function copyText(text: string, kind: 'token' | 'command') {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(kind);
      window.setTimeout(() => setCopied((current) => (current === kind ? null : current)), 2000);
    } catch {
      setMessage('Could not copy to clipboard.');
    }
  }

  const runCommand = issuedToken && issuedRunnerId
    ? buildRunnerStartCommand({
        token: issuedToken,
        runnerId: issuedRunnerId,
        workspaceId,
        label: label.trim() || 'local-runner',
      })
    : null;

  return (
    <div className="space-y-3">
      <div
        className={`rounded border px-2.5 py-2 flex items-start gap-2 ${
          connected
            ? 'border-emerald-200 dark:border-emerald-900 bg-emerald-50/50 dark:bg-emerald-950/30'
            : 'border-amber-200 dark:border-amber-900 bg-amber-50/50 dark:bg-amber-950/30'
        }`}
      >
        {connected ? (
          <CheckCircle2 size={14} className="shrink-0 text-emerald-600 dark:text-emerald-400 mt-0.5" />
        ) : (
          <AlertCircle size={14} className="shrink-0 text-amber-600 dark:text-amber-400 mt-0.5" />
        )}
        <div className="space-y-0.5">
          <p className={`text-[11px] font-medium ${connected ? 'text-emerald-800 dark:text-emerald-200' : 'text-amber-800 dark:text-amber-200'}`}>
            {connected ? 'Runner connected' : 'No runner connected'}
          </p>
          <p className="text-[10px] text-muted-foreground">
            {connected
              ? 'Hosted jobs execute on your registered runner. The control plane only orchestrates.'
              : 'Hosted execution is paused; self-host still works. Register a runner on a machine with repo access and provider keys.'}
          </p>
        </div>
      </div>
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
      {loading && visibleRunners.length === 0 ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
          <Loader2 size={12} className="animate-spin" />
          Loading runners…
        </div>
      ) : visibleRunners.length === 0 ? (
        <p className="text-[11px] text-muted-foreground py-2">No runners registered yet.</p>
      ) : (
        <div className="space-y-2">
          {visibleRunners.map((runner) => (
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
              {canManage && (
                <button
                  type="button"
                  onClick={() => void revokeRunner(runner.id)}
                  disabled={busy}
                  className="ml-auto h-7 w-7 flex items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                  aria-label={`Revoke ${runner.label}`}
                  title="Revoke runner"
                >
                  <ShieldOff size={12} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {canManage && (
        <div className="rounded border border-border bg-background px-3 py-3 space-y-2">
          <div className="text-xs font-medium text-foreground">Add a runner</div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Runner label"
              className={`flex-1 min-w-[160px] ${FORM_INPUT_CLASS}`}
            />
            <button
              type="button"
              onClick={() => void addRunner()}
              disabled={busy}
              className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              {busy ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
              Add runner
            </button>
          </div>
          {issuedToken && (
            <div className="rounded border border-amber-200 dark:border-amber-900 bg-amber-50/50 dark:bg-amber-950/30 px-2.5 py-2 space-y-2">
              <p className="text-[11px] text-amber-800 dark:text-amber-200">
                Copy this token now — it will not be shown again.
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-[10px] font-mono break-all text-foreground">{issuedToken}</code>
                <button
                  type="button"
                  onClick={() => void copyText(issuedToken, 'token')}
                  className="h-7 w-7 flex items-center justify-center rounded border border-border hover:bg-muted"
                  aria-label="Copy token"
                  title="Copy token"
                >
                  <Copy size={12} />
                </button>
              </div>
              {copied === 'token' && (
                <p className="text-[10px] text-emerald-600 dark:text-emerald-400">Token copied.</p>
              )}
              {runCommand && (
                <div className="space-y-1">
                  <p className="text-[11px] text-foreground">Run this on your machine:</p>
                  <pre className="text-[10px] font-mono whitespace-pre-wrap break-all rounded border border-border bg-muted/40 px-2 py-1.5 text-foreground">
                    {runCommand}
                  </pre>
                  <button
                    type="button"
                    onClick={() => void copyText(runCommand, 'command')}
                    className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border hover:bg-muted transition-colors"
                  >
                    <Copy size={10} />
                    {copied === 'command' ? 'Command copied' : 'Copy command'}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {!canManage && (
        <p className="text-[11px] text-muted-foreground">Runner registration requires owner or admin.</p>
      )}
      {message && <p className="text-[11px] text-red-600 dark:text-red-400">{message}</p>}
    </div>
  );
}
