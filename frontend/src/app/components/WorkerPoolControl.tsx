import { useMemo, useState } from 'react';
import { ChevronDown, Circle, Loader2, Play, Square } from 'lucide-react';
import type { AutoWorkerStatus } from '../api/client';
import { activeWorkerCount, enrichWorkerSlots, workerDisplayNumber } from '../throughputUtils';
import type { Ticket } from '../types';

interface Props {
  workerStatus: AutoWorkerStatus | null;
  tickets: Ticket[];
  pending: boolean;
  projectPathReady: boolean;
  onToggle: () => void;
}

export function WorkerPoolControl({
  workerStatus,
  tickets,
  pending,
  projectPathReady,
  onToggle,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const running = Boolean(workerStatus?.running);
  const slots = useMemo(
    () => (workerStatus ? enrichWorkerSlots(workerStatus, tickets) : []),
    [tickets, workerStatus],
  );
  const activeCount = activeWorkerCount(slots);
  const maxWorkers = workerStatus?.max_workers ?? (slots.length || 1);
  const error = workerStatus?.last_error ?? '';

  return (
    <div className="relative ml-auto flex items-center gap-1.5">
      <button
        type="button"
        onClick={onToggle}
        disabled={pending || !projectPathReady}
        title={
          !projectPathReady
            ? 'Set a repository path before enabling auto-run'
            : error
              ? `Auto-run last error: ${error}`
              : running
                ? 'Stop the worker pool'
                : 'Start the worker pool for this project'
        }
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
          running
            ? 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
            : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
        }`}
      >
        {pending ? (
          <Loader2 size={12} className="animate-spin" />
        ) : running ? (
          <Square size={10} className="fill-current" />
        ) : (
          <Play size={12} />
        )}
        Auto-run
        {running && !pending && (
          <Circle size={7} className="fill-emerald-500 text-emerald-500 animate-pulse" />
        )}
        {!running && error && <Circle size={7} className="fill-red-500 text-red-500" />}
      </button>

      {(running || slots.length > 1) && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-expanded={expanded}
          title="Worker pool status"
        >
          <span className="tabular-nums">{activeCount}/{maxWorkers}</span>
          <span className="hidden sm:inline">workers</span>
          <ChevronDown size={12} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </button>
      )}

      {expanded && (
        <div className="absolute right-4 top-[calc(100%-2px)] z-20 mt-1 w-72 rounded-lg border border-border bg-card shadow-md p-2 space-y-1">
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground px-1">
            Worker pool · max {maxWorkers}
          </p>
          {slots.map((slot) => {
            const busy = Boolean(slot.ticket_id);
            return (
              <div
                key={slot.worker_id}
                className="flex items-center gap-2 rounded-md border border-border px-2 py-1.5 text-[11px]"
              >
                <Circle
                  size={8}
                  className={busy ? 'fill-blue-500 text-blue-500' : 'fill-zinc-300 text-zinc-300 dark:fill-zinc-600 dark:text-zinc-600'}
                />
                <span className="font-mono text-muted-foreground">Worker {workerDisplayNumber(slot.worker_id)}</span>
                <span className="flex-1 truncate text-foreground">
                  {busy ? slot.ticket_id : 'idle'}
                </span>
                {slot.last_skipped_reason === 'target_file_conflict' && (
                  <span className="text-amber-600 dark:text-amber-400 shrink-0">conflict skip</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
