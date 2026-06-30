interface Props {
  payload: Record<string, unknown>;
  compact?: boolean;
}

export function sandboxLabelFromPayload(payload: Record<string, unknown>): { label: string; degraded: boolean } | null {
  const stage = payload.stage;
  const reason = typeof payload.reason === 'string' ? payload.reason : '';
  const primitive = typeof payload.primitive === 'string' ? payload.primitive : '';
  const kind = typeof payload.kind === 'string' ? payload.kind : '';
  const mode = typeof payload.sandbox_mode === 'string' ? payload.sandbox_mode : '';
  const strictMode = mode === 'strict' || reason === 'sandbox_strict_unavailable';

  if (stage === 'sandbox' || reason.startsWith('sandbox') || reason === 'network_blocked_by_sandbox') {
    if (reason === 'sandbox_strict_unavailable') {
      return { label: 'Strict requested, ran in weaker mode (runtime unavailable)', degraded: true };
    }
    if (reason === 'sandbox_unavailable' || primitive === 'none' || primitive === 'local') {
      return { label: 'Tests ran best-effort (sandbox unavailable)', degraded: true };
    }
    if (strictMode || primitive === 'docker') {
      return { label: 'Tests ran in strict sandbox', degraded: false };
    }
    if (reason === 'network_blocked_by_sandbox' || ['docker', 'unshare'].includes(primitive)) {
      return { label: 'Tests ran sandboxed (network restricted)', degraded: false };
    }
    if (kind === 'egress_attempt' && payload.blocked === true) {
      return { label: 'Tests ran sandboxed (network restricted)', degraded: false };
    }
  }

  if (typeof payload.sandbox_primitive === 'string') {
    const value = payload.sandbox_primitive;
    if (!value || value === 'none' || value === 'local') {
      return { label: 'Tests ran best-effort (no sandbox)', degraded: true };
    }
    if (value === 'docker' && strictMode) {
      return { label: 'Tests ran in strict sandbox', degraded: false };
    }
    return { label: `Tests ran in ${value} sandbox`, degraded: false };
  }

  return null;
}

export function SandboxBadge({ payload, compact = false }: Props) {
  const copy = sandboxLabelFromPayload(payload);
  if (!copy) return null;

  const className = copy.degraded
    ? 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-800'
    : 'bg-sky-50 text-sky-800 border-sky-200 dark:bg-sky-950 dark:text-sky-200 dark:border-sky-800';

  return (
    <span
      className={`inline-flex text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${className} ${compact ? 'max-w-[220px] truncate' : ''}`}
    >
      {copy.label}
    </span>
  );
}
