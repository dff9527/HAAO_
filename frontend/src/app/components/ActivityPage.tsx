import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Filter,
  Loader2,
  Radio,
  RotateCcw,
  ShieldAlert,
  ShieldX,
  GitMerge,
  Send,
} from 'lucide-react';
import { apiClient } from '../api/client';
import type { RunEvent } from '../api/types';
import {
  type ActivityRun,
  MOCK_RUN_EVENTS,
  eventNeedsAttention,
  formatEventTime,
  formatRunDuration,
  groupEventsIntoRuns,
} from '../activityUtils';
import { formatCloudCost } from '../constants';
import { modelDisplayLabel } from '../modelDisplay';
import { DiffViewer } from './DiffViewer';
import { RunProvenanceChip, provenanceFromRunStarted } from './RunProvenanceChip';
import { SandboxBadge } from './SandboxBadge';
import { formatConflictEventMessage } from '../throughputUtils';
import type { CloudModel, Ticket } from '../types';

type AttentionFilter = 'all' | 'attention';

interface Props {
  projectId: string;
  tickets: Ticket[];
  cloudModels: CloudModel[];
  usingMockData: boolean;
  onSelectTicket?: (ticketId: string) => void;
}

function CostStatusBadge({ status }: { status: 'actual' | 'estimated' | 'unknown' | null }) {
  if (!status) return null;
  const classes = {
    actual: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800',
    estimated: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300 border-amber-200 dark:border-amber-800',
    unknown: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400 border-border',
  }[status];
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full border font-medium uppercase tracking-wide ${classes}`}>
      {status}
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome: ActivityRun['outcome'] }) {
  const copy: Record<ActivityRun['outcome'], { label: string; className: string }> = {
    running: { label: 'running', className: 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300' },
    passed: { label: 'passed', className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300' },
    failed: { label: 'failed', className: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300' },
    escalated: { label: 'escalated', className: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300' },
    cancelled: { label: 'cancelled', className: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400' },
  };
  const item = copy[outcome];
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide ${item.className}`}>
      {item.label}
    </span>
  );
}

function payloadString(payload: Record<string, unknown> | null | undefined, key: string): string | undefined {
  const value = payload?.[key];
  return typeof value === 'string' ? value : undefined;
}

function payloadNumber(payload: Record<string, unknown> | null | undefined, key: string): number | undefined {
  const value = payload?.[key];
  return typeof value === 'number' ? value : undefined;
}

function payloadBoolean(payload: Record<string, unknown> | null | undefined, key: string): boolean | undefined {
  const value = payload?.[key];
  return typeof value === 'boolean' ? value : undefined;
}

function RunEventItem({
  event,
  cloudModels,
}: {
  event: RunEvent;
  cloudModels: CloudModel[];
}) {
  const payload = event.payload ?? {};
  const attention = eventNeedsAttention(event);

  const shellClass = attention
    ? 'border-amber-300 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/20'
    : 'border-border bg-card';

  const header = (
    <div className="flex flex-wrap items-center gap-2 text-[11px]">
      <span className="font-mono text-muted-foreground">{formatEventTime(event.ts)}</span>
      <span className="font-medium text-foreground">{event.event_type.replace(/_/g, ' ')}</span>
      {event.model_id && (
        <span className="text-muted-foreground">{modelDisplayLabel(event.model_id, cloudModels)}</span>
      )}
      {event.cost_status && <CostStatusBadge status={event.cost_status} />}
    </div>
  );

  switch (event.event_type) {
    case 'run_started':
      return (
        <div className={`rounded-lg border px-3 py-2 ${shellClass}`}>
          {header}
          <p className="text-xs text-muted-foreground mt-1">Ticket entered execution ({payloadString(payload, 'status') ?? 'started'}).</p>
          {payloadString(payload, 'reasoner_prompt_version') && (
            <div className="mt-1.5">
              <RunProvenanceChip
                modelId={event.model_id}
                promptVersion={payloadString(payload, 'reasoner_prompt_version')}
                cloudModels={cloudModels}
                compact
              />
            </div>
          )}
        </div>
      );
    case 'model_call':
      return (
        <div className={`rounded-lg border px-3 py-2 ${shellClass}`}>
          {header}
          <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted-foreground">
            {(event.input_tokens != null || event.output_tokens != null) && (
              <span>
                {event.input_tokens ?? 0} in / {event.output_tokens ?? 0} out tokens
              </span>
            )}
            {formatCloudCost(event.cost_usd ?? undefined) && (
              <span className="tabular-nums">{formatCloudCost(event.cost_usd ?? undefined)}</span>
            )}
            {payloadString(payload, 'target_file') && (
              <span className="font-mono">{payloadString(payload, 'target_file')}</span>
            )}
            {payloadString(payload, 'edit_mode') && (
              <span>{payloadString(payload, 'edit_mode')}</span>
            )}
          </div>
        </div>
      );
    case 'diff_produced': {
      const diff = payloadString(payload, 'diff') ?? '';
      return (
        <div className={`rounded-lg border px-3 py-2 space-y-2 ${shellClass}`}>
          {header}
          {diff ? <DiffViewer diff={diff} title="produced diff" /> : (
            <p className="text-xs text-muted-foreground">Diff produced ({payloadNumber(payload, 'line_count') ?? 0} lines).</p>
          )}
        </div>
      );
    }
    case 'dod_check': {
      const passed = payloadString(payload, 'status') === 'pass';
      return (
        <div className={`rounded-lg border px-3 py-2 ${shellClass}`}>
          {header}
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
            <span className={`font-medium ${passed ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
              {passed ? 'PASS' : 'FAIL'}
            </span>
            {payloadString(payload, 'command') && (
              <span className="font-mono text-muted-foreground">{payloadString(payload, 'command')}</span>
            )}
            <SandboxBadge payload={payload} compact />
          </div>
          {payloadString(payload, 'output_tail') && (
            <pre className="mt-2 text-[11px] font-mono bg-muted/50 rounded px-2 py-1.5 overflow-x-auto whitespace-pre-wrap text-foreground">
              {payloadString(payload, 'output_tail')}
            </pre>
          )}
        </div>
      );
    }
    case 'retry':
      return (
        <div className={`rounded-lg border px-3 py-2 ${shellClass}`}>
          {header}
          <p className="text-xs text-amber-700 dark:text-amber-300 mt-1">
            Attempt {payloadNumber(payload, 'attempt') ?? '?'} of {payloadNumber(payload, 'retry_budget') ?? '?'}
            {payloadString(payload, 'reason') ? ` — ${payloadString(payload, 'reason')}` : ''}
          </p>
        </div>
      );
    case 'escalation':
      return (
        <div className="rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50/70 dark:bg-amber-950/30 px-3 py-2">
          {header}
          <p className="text-xs text-amber-800 dark:text-amber-200 mt-1">
            {payloadString(payload, 'reason') ?? 'Escalated'}
            {payloadString(payload, 'escalated_to') ? ` → ${payloadString(payload, 'escalated_to')}` : ''}
          </p>
        </div>
      );
    case 'egress_attempt':
      return (
        <div data-testid="run-event-egress_attempt" className="rounded-lg border border-orange-300 dark:border-orange-800 bg-orange-50/80 dark:bg-orange-950/30 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] text-orange-800 dark:text-orange-200 font-medium">
            <ShieldAlert size={12} />
            <span>
              {payloadBoolean(payload, 'blocked') === false
                ? 'Allowed a network attempt during tests'
                : 'Blocked a network attempt during tests'}
            </span>
          </div>
          {payloadString(payload, 'destination') && (
            <p className="text-xs font-mono mt-1 break-all text-orange-900 dark:text-orange-100">
              {payloadString(payload, 'destination')}
            </p>
          )}
          {payloadString(payload, 'command') && (
            <p className="text-[11px] text-orange-800 dark:text-orange-200 mt-1 font-mono">{payloadString(payload, 'command')}</p>
          )}
          {payloadString(payload, 'reason') && (
            <p className="text-[11px] text-orange-700 dark:text-orange-300 mt-1">{payloadString(payload, 'reason')}</p>
          )}
          <div className="mt-1.5">
            <SandboxBadge payload={payload} compact />
          </div>
        </div>
      );
    case 'attachment_egress': {
      const provider = payloadString(payload, 'provider') ?? 'provider';
      const model = payloadString(payload, 'model');
      const attachmentId = payloadString(payload, 'attachment_id');
      const target = model ? `${provider}/${model}` : provider;
      return (
        <div className="rounded-lg border border-violet-300 dark:border-violet-800 bg-violet-50/80 dark:bg-violet-950/30 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] text-violet-800 dark:text-violet-200 font-medium">
            <Send size={12} />
            <span>Sent this attachment to {target}</span>
          </div>
          {attachmentId && (
            <p className="text-xs font-mono mt-1 break-all text-violet-900 dark:text-violet-100">
              {attachmentId}
            </p>
          )}
          {payloadString(payload, 'ref') && (
            <p className="text-[11px] text-violet-700 dark:text-violet-300 mt-1">
              Ref: {payloadString(payload, 'ref')}
            </p>
          )}
        </div>
      );
    }
    case 'conflict':
      return (
        <div className="rounded-lg border border-violet-300 dark:border-violet-800 bg-violet-50/80 dark:bg-violet-950/30 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] text-violet-800 dark:text-violet-200 font-medium">
            <GitMerge size={12} />
            <span>{formatConflictEventMessage(payload)}</span>
          </div>
          {payloadString(payload, 'detail') && (
            <p className="text-[11px] text-violet-700 dark:text-violet-300 mt-1">{payloadString(payload, 'detail')}</p>
          )}
        </div>
      );
    case 'diff_scope_reject':
      return (
        <div data-testid="run-event-diff_scope_reject" className="rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50/80 dark:bg-amber-950/30 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] text-amber-800 dark:text-amber-200 font-medium">
            <ShieldX size={12} />
            <span>Rejected an out-of-scope edit</span>
          </div>
          {payloadString(payload, 'path') && (
            <p className="text-xs font-mono mt-1 break-all text-amber-900 dark:text-amber-100">{payloadString(payload, 'path')}</p>
          )}
          {payloadString(payload, 'detail') && (
            <p className="text-[11px] text-amber-700 dark:text-amber-300 mt-1">{payloadString(payload, 'detail')}</p>
          )}
        </div>
      );
    case 'rollback':
      return (
        <div className="rounded-lg border border-sky-300 dark:border-sky-800 bg-sky-50/80 dark:bg-sky-950/30 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[11px] text-sky-800 dark:text-sky-200 font-medium">
            <RotateCcw size={12} />
            <span>Rolled back workspace changes after a safety check</span>
          </div>
          {payloadString(payload, 'detail') && (
            <p className="text-[11px] text-sky-700 dark:text-sky-300 mt-1">{payloadString(payload, 'detail')}</p>
          )}
        </div>
      );
    case 'error':
      return (
        <div className="rounded-lg border border-red-300 dark:border-red-800 bg-red-50/80 dark:bg-red-950/30 px-3 py-2">
          {header}
          <p className="text-xs text-red-700 dark:text-red-300 mt-1 whitespace-pre-wrap">
            {payloadString(payload, 'error') ?? payloadString(payload, 'message') ?? 'An error occurred.'}
          </p>
        </div>
      );
    case 'report': {
      const kind = payloadString(payload, 'report_kind');
      const blocked = kind === 'blocked';
      const needsYou = kind === 'needs_you';
      return (
        <div className={`rounded-lg border px-3 py-2 ${
          blocked || needsYou
            ? 'border-amber-300 dark:border-amber-800 bg-amber-50/70 dark:bg-amber-950/30'
            : shellClass
        }`}>
          {header}
          <p className="text-xs mt-1">
            <span className={`font-medium ${blocked ? 'text-red-700 dark:text-red-300' : needsYou ? 'text-amber-700 dark:text-amber-300' : 'text-emerald-700 dark:text-emerald-300'}`}>
              {kind === 'done' ? 'Done' : blocked ? 'Blocked' : needsYou ? 'Needs you' : kind ?? 'Report'}
            </span>
            {payloadString(payload, 'reason') ? ` — ${payloadString(payload, 'reason')}` : ''}
          </p>
        </div>
      );
    }
    case 'run_finished':
      return (
        <div className={`rounded-lg border px-3 py-2 ${shellClass}`}>
          {header}
          <p className="text-xs text-muted-foreground mt-1">
            {payloadBoolean(payload, 'cancelled')
              ? 'Run cancelled.'
              : payloadBoolean(payload, 'escalated')
                ? 'Run escalated.'
                : payloadBoolean(payload, 'passed')
                  ? 'Run completed successfully.'
                  : 'Run finished without passing.'}
          </p>
        </div>
      );
    default:
      return (
        <div className={`rounded-lg border px-3 py-2 ${shellClass}`}>
          {header}
        </div>
      );
  }
}

function RunCard({
  run,
  expanded,
  onToggle,
  ticketTitle,
  cloudModels,
  onSelectTicket,
}: {
  run: ActivityRun;
  expanded: boolean;
  onToggle: () => void;
  ticketTitle?: string;
  cloudModels: CloudModel[];
  onSelectTicket?: (ticketId: string) => void;
}) {
  const duration = formatRunDuration(run.startedAt, run.endedAt);
  const costLabel = formatCloudCost(run.totalCostUsd > 0 ? run.totalCostUsd : undefined);
  const startedEvent = run.events.find((event) => event.event_type === 'run_started');
  const provenance = provenanceFromRunStarted(startedEvent?.payload ?? undefined);
  const sandboxEvent = [...run.events].reverse().find((event) => {
    const payload = event.payload ?? {};
    return payload.stage === 'sandbox' || typeof payload.sandbox_primitive === 'string' || typeof payload.sandbox_mode === 'string';
  });

  return (
    <div
      data-testid={`activity-run-${run.runId}`}
      className={`rounded-xl border bg-card overflow-hidden ${run.hasAttention ? 'border-amber-300 dark:border-amber-800' : 'border-border'}`}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left px-4 py-3 hover:bg-muted/30 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-start gap-2">
          {expanded ? <ChevronDown size={14} className="mt-0.5 shrink-0 text-muted-foreground" /> : <ChevronRight size={14} className="mt-0.5 shrink-0 text-muted-foreground" />}
          <div className="flex-1 min-w-0 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              {run.ticketId && (
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectTicket?.(run.ticketId!);
                  }}
                  className="font-mono text-xs font-semibold text-primary hover:underline"
                >
                  {run.ticketId}
                </button>
              )}
              {ticketTitle && <span className="text-sm text-foreground truncate">{ticketTitle}</span>}
              <OutcomeBadge outcome={run.outcome} />
              {run.hasAttention && (
                <span className="inline-flex items-center gap-0.5 text-[10px] text-amber-700 dark:text-amber-300">
                  <AlertTriangle size={10} /> attention
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
              <span className="font-mono">{run.runId}</span>
              {duration && <span>{duration}</span>}
              {run.models.length > 0 && (
                <span>
                  {run.models.map((model) => modelDisplayLabel(model, cloudModels)).join(' · ')}
                </span>
              )}
              {costLabel && <span className="tabular-nums">{costLabel}</span>}
              <CostStatusBadge status={run.costStatus} />
              {(run.models[0] || provenance.promptVersion) && (
                <RunProvenanceChip
                  modelId={run.models[0]}
                  promptVersion={provenance.promptVersion}
                  cloudModels={cloudModels}
                  compact
                />
              )}
              {sandboxEvent && <SandboxBadge payload={sandboxEvent.payload ?? {}} compact />}
            </div>
          </div>
        </div>
      </button>
      {expanded && (
        <div className="px-4 pb-4 space-y-2 border-t border-border bg-muted/10">
          {run.events.map((event) => (
            <RunEventItem key={event.id} event={event} cloudModels={cloudModels} />
          ))}
        </div>
      )}
    </div>
  );
}

export function ActivityPage({ projectId, tickets, cloudModels, usingMockData, onSelectTicket }: Props) {
  const [events, setEvents] = useState<RunEvent[]>(usingMockData ? MOCK_RUN_EVENTS : []);
  const [loading, setLoading] = useState(!usingMockData);
  const [live, setLive] = useState(false);
  const [ticketFilter, setTicketFilter] = useState('');
  const [attentionFilter, setAttentionFilter] = useState<AttentionFilter>('all');
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(new Set());
  const lastEventIdRef = useRef(0);

  const ticketTitleById = useMemo(
    () => Object.fromEntries(tickets.map((ticket) => [ticket.id, ticket.title])),
    [tickets],
  );

  const ticketOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const ticket of tickets) ids.add(ticket.id);
    for (const event of events) {
      if (event.ticket_id) ids.add(event.ticket_id);
    }
    return [...ids].sort();
  }, [events, tickets]);

  const mergeEvents = useCallback((incoming: RunEvent[]) => {
    if (!incoming.length) return;
    setEvents((prev) => {
      const known = new Set(prev.map((event) => event.id));
      const appended = incoming.filter((event) => !known.has(event.id));
      if (!appended.length) return prev;
      const merged = [...prev, ...appended].sort((a, b) => a.id - b.id);
      lastEventIdRef.current = merged[merged.length - 1]?.id ?? lastEventIdRef.current;
      return merged;
    });
  }, []);

  const loadInitial = useCallback(async () => {
    if (usingMockData) {
      setEvents(MOCK_RUN_EVENTS);
      lastEventIdRef.current = MOCK_RUN_EVENTS[MOCK_RUN_EVENTS.length - 1]?.id ?? 0;
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const initial = await apiClient.listRunEvents({ projectId, limit: 500 });
      setEvents(initial);
      lastEventIdRef.current = initial[initial.length - 1]?.id ?? 0;
    } catch {
      setEvents(MOCK_RUN_EVENTS);
      lastEventIdRef.current = MOCK_RUN_EVENTS[MOCK_RUN_EVENTS.length - 1]?.id ?? 0;
    } finally {
      setLoading(false);
    }
  }, [projectId, usingMockData]);

  const refreshEvents = useCallback(async () => {
    if (usingMockData) return;
    const after = lastEventIdRef.current;
    if (!after) return;
    try {
      const incoming = await apiClient.listRunEvents({
        projectId,
        after,
      });
      mergeEvents(incoming);
    } catch {
      // Keep the last known stream during transient failures.
    }
  }, [mergeEvents, projectId, usingMockData]);

  useEffect(() => {
    setExpandedRuns(new Set());
    void loadInitial();
  }, [loadInitial]);

  useEffect(() => {
    if (usingMockData) {
      setLive(false);
      return;
    }
    setLive(true);
    const intervalId = window.setInterval(() => {
      void refreshEvents();
    }, 5000);
    return () => {
      window.clearInterval(intervalId);
      setLive(false);
    };
  }, [refreshEvents, usingMockData]);

  const runs = useMemo(() => {
    let grouped = groupEventsIntoRuns(events);
    if (ticketFilter) {
      grouped = grouped.filter((run) => run.ticketId === ticketFilter);
    }
    if (attentionFilter === 'attention') {
      grouped = grouped.filter((run) => run.hasAttention);
    }
    return grouped;
  }, [attentionFilter, events, ticketFilter]);

  useEffect(() => {
    if (!runs.length) return;
    setExpandedRuns((prev) => {
      if (prev.size > 0) return prev;
      return new Set([runs[0].runId]);
    });
  }, [runs]);

  function toggleRun(runId: string) {
    setExpandedRuns((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  }

  return (
    <div className="flex-1 overflow-y-auto bg-background" data-testid="activity-page">
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-8 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-base font-semibold flex items-center gap-2">
              <Activity size={16} className="text-sky-600 dark:text-sky-400" />
              Activity
            </h1>
            <p className="text-xs text-muted-foreground mt-1">
              Live execution runs grouped by run ID — model calls, diffs, DoD checks, egress audits, and reports.
            </p>
          </div>
          {live && (
            <span className="inline-flex items-center gap-1.5 text-[11px] text-emerald-600 dark:text-emerald-400 shrink-0">
              <Radio size={11} className="animate-pulse" />
              Live
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-muted/30 px-3 py-2.5">
          <Filter size={12} className="text-muted-foreground shrink-0" />
          <select
            value={ticketFilter}
            onChange={(e) => setTicketFilter(e.target.value)}
            className="text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring text-foreground min-w-[120px]"
            aria-label="Filter by ticket"
          >
            <option value="">All tickets</option>
            {ticketOptions.map((ticketId) => (
              <option key={ticketId} value={ticketId}>
                {ticketId}{ticketTitleById[ticketId] ? ` — ${ticketTitleById[ticketId]}` : ''}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setAttentionFilter((value) => (value === 'attention' ? 'all' : 'attention'))}
            className={`text-xs px-2.5 py-1 rounded border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
              attentionFilter === 'attention'
                ? 'border-amber-300 dark:border-amber-700 bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-200'
                : 'border-border hover:bg-muted text-muted-foreground'
            }`}
          >
            Errors / needs attention
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            Loading activity…
          </div>
        ) : runs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border px-4 py-10 text-center">
            <p className="text-sm text-foreground font-medium">No runs yet</p>
            <p className="text-xs text-muted-foreground mt-1">
              Start work on a ticket from the board — execution events will stream here.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {runs.map((run) => (
              <RunCard
                key={run.runId}
                run={run}
                expanded={expandedRuns.has(run.runId)}
                onToggle={() => toggleRun(run.runId)}
                ticketTitle={run.ticketId ? ticketTitleById[run.ticketId] : undefined}
                cloudModels={cloudModels}
                onSelectTicket={onSelectTicket}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
