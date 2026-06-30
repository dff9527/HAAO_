import { useEffect, useMemo, useState } from 'react';
import { Loader2, Shield } from 'lucide-react';
import { apiClient } from '../api/client';
import type { IdentityContext, RetentionPolicy, RetentionPurgeCounts } from '../api/types';
import { canManageTeam, forbiddenMessage, isForbiddenError, roleLabel } from '../teamPlaneUtils';

const FORM_INPUT_CLASS =
  'text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground';

type RetentionField = keyof RetentionPolicy;

const FIELD_LABELS: Array<{ key: RetentionField; label: string }> = [
  { key: 'run_events_days', label: 'Run events' },
  { key: 'ticket_logs_days', label: 'Ticket logs' },
  { key: 'diffs_days', label: 'Diffs' },
  { key: 'prompts_days', label: 'Prompts' },
  { key: 'attachments_days', label: 'Attachments' },
];

interface Props {
  identityContext: IdentityContext;
  onForbidden?: (message: string) => void;
}

function defaultPolicy(): RetentionPolicy {
  return {
    run_events_days: null,
    ticket_logs_days: null,
    diffs_days: null,
    prompts_days: null,
    attachments_days: null,
  };
}

export function RetentionPanel({ identityContext, onForbidden }: Props) {
  const canManage = canManageTeam(identityContext);
  const workspaceId = identityContext.workspace_id;
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [purging, setPurging] = useState(false);
  const [policy, setPolicy] = useState<RetentionPolicy>(defaultPolicy());
  const [draft, setDraft] = useState<RetentionPolicy>(defaultPolicy());
  const [purgeCounts, setPurgeCounts] = useState<RetentionPurgeCounts | null>(null);
  const [message, setMessage] = useState('');

  const isDirty = useMemo(
    () => JSON.stringify(policy) !== JSON.stringify(draft),
    [draft, policy],
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    apiClient
      .getRetentionPolicy(workspaceId)
      .then((next) => {
        if (!active) return;
        setPolicy(next);
        setDraft(next);
      })
      .catch((error) => {
        if (!active) return;
        if (isForbiddenError(error)) {
          onForbidden?.(forbiddenMessage(error));
        } else {
          setMessage(error instanceof Error ? error.message : 'Could not load retention policy.');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onForbidden, workspaceId]);

  function updateMode(key: RetentionField, mode: 'keep' | 'days') {
    if (mode === 'keep') {
      setDraft((prev) => ({ ...prev, [key]: null }));
      return;
    }
    setDraft((prev) => ({ ...prev, [key]: prev[key] ?? 30 }));
  }

  function updateDays(key: RetentionField, value: string) {
    const parsed = Number.parseInt(value, 10);
    setDraft((prev) => ({
      ...prev,
      [key]: Number.isFinite(parsed) && parsed > 0 ? parsed : 1,
    }));
  }

  async function savePolicy() {
    setSaving(true);
    setMessage('');
    try {
      const saved = await apiClient.updateRetentionPolicy(draft, workspaceId);
      setPolicy(saved);
      setDraft(saved);
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setMessage(error instanceof Error ? error.message : 'Could not save retention policy.');
      }
    } finally {
      setSaving(false);
    }
  }

  async function purgeNow() {
    setPurging(true);
    setMessage('');
    try {
      const counts = await apiClient.purgeRetentionNow(workspaceId);
      setPurgeCounts(counts);
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setMessage(error instanceof Error ? error.message : 'Could not run retention purge.');
      }
    } finally {
      setPurging(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="rounded border border-border bg-muted/30 px-2.5 py-2 text-[11px] text-muted-foreground">
        Keep only what you need. This policy controls how long operational logs and artifacts stay in HAAO for this workspace.
      </div>
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
          <Loader2 size={12} className="animate-spin" />
          Loading retention policy…
        </div>
      ) : (
        <div className="space-y-2">
          {FIELD_LABELS.map(({ key, label }) => {
            const isKeep = draft[key] == null;
            return (
              <div key={key} className="rounded border border-border bg-background px-3 py-2.5">
                <div className="flex flex-wrap items-center gap-2 justify-between">
                  <span className="text-xs font-medium text-foreground">{label}</span>
                  <div className="flex items-center gap-1.5">
                    <label className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <input
                        type="radio"
                        name={`${key}-mode`}
                        checked={isKeep}
                        disabled={!canManage || saving}
                        onChange={() => updateMode(key, 'keep')}
                      />
                      Keep forever
                    </label>
                    <label className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <input
                        type="radio"
                        name={`${key}-mode`}
                        checked={!isKeep}
                        disabled={!canManage || saving}
                        onChange={() => updateMode(key, 'days')}
                      />
                      Retain
                    </label>
                    <input
                      type="number"
                      min={1}
                      value={isKeep ? '' : String(draft[key] ?? '')}
                      disabled={!canManage || saving || isKeep}
                      onChange={(e) => updateDays(key, e.target.value)}
                      className={`${FORM_INPUT_CLASS} w-20`}
                      placeholder="days"
                    />
                    <span className="text-[11px] text-muted-foreground">days</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void savePolicy()}
          disabled={!canManage || saving || !isDirty}
          className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save retention policy'}
        </button>
        <button
          type="button"
          onClick={() => void purgeNow()}
          disabled={!canManage || purging}
          className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
        >
          {purging ? <Loader2 size={11} className="animate-spin" /> : <Shield size={11} />}
          Run purge now
        </button>
      </div>
      {purgeCounts && (
        <div className="rounded border border-border bg-background px-3 py-2.5 text-[11px] text-muted-foreground space-y-0.5">
          <p className="text-foreground font-medium">Purge results</p>
          <p>Run events deleted: {purgeCounts.run_events_deleted}</p>
          <p>Ticket logs deleted: {purgeCounts.ticket_logs_deleted}</p>
          <p>Ticket diffs redacted: {purgeCounts.ticket_diffs_redacted}</p>
          <p>Requirement prompts redacted: {purgeCounts.requirement_prompts_redacted}</p>
          <p>Chat messages redacted: {purgeCounts.chat_messages_redacted}</p>
          <p>Attachments deleted: {purgeCounts.attachments_deleted}</p>
        </div>
      )}
      {!canManage && (
        <p className="text-[11px] text-muted-foreground">
          Retention policy changes require owner/admin. Your role is {roleLabel(identityContext.role)}.
        </p>
      )}
      {message && <p className="text-[11px] text-red-600 dark:text-red-400">{message}</p>}
    </div>
  );
}
