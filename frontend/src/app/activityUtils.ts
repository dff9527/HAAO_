import type { RunEvent, RunEventCostStatus } from './api/types';

export type ActivityRunOutcome = 'running' | 'passed' | 'failed' | 'escalated' | 'cancelled';

export interface ActivityRun {
  runId: string;
  ticketId?: string;
  requirementId?: string;
  events: RunEvent[];
  startedAt: string;
  endedAt?: string;
  models: string[];
  totalCostUsd: number;
  costStatus: RunEventCostStatus | null;
  outcome: ActivityRunOutcome;
  hasAttention: boolean;
  lastEventId: number;
}

function payloadString(payload: Record<string, unknown> | null | undefined, key: string): string | undefined {
  const value = payload?.[key];
  return typeof value === 'string' ? value : undefined;
}

export function eventNeedsAttention(event: RunEvent): boolean {
  if (
    event.event_type === 'error'
    || event.event_type === 'egress_attempt'
    || event.event_type === 'diff_scope_reject'
    || event.event_type === 'rollback'
    || event.event_type === 'conflict'
    || event.event_type === 'escalation'
  ) {
    return true;
  }
  if (event.event_type === 'report') {
    const kind = payloadString(event.payload, 'report_kind');
    return kind === 'blocked' || kind === 'needs_you';
  }
  return false;
}

function mergeCostStatus(current: RunEventCostStatus | null, next: RunEventCostStatus | null | undefined): RunEventCostStatus | null {
  if (!next) return current;
  if (!current) return next;
  if (current === 'actual' || next === 'actual') return 'actual';
  if (current === 'estimated' || next === 'estimated') return 'estimated';
  return 'unknown';
}

function deriveOutcome(events: RunEvent[]): ActivityRunOutcome {
  const finished = events.find((event) => event.event_type === 'run_finished');
  if (!finished) return 'running';
  const payload = finished.payload ?? {};
  if (payload.cancelled === true) return 'cancelled';
  if (payload.escalated === true) return 'escalated';
  if (payload.passed === true) return 'passed';
  return 'failed';
}

export function groupEventsIntoRuns(events: RunEvent[]): ActivityRun[] {
  const byRun = new Map<string, RunEvent[]>();

  for (const event of events) {
    const key = event.run_id || `orphan-${event.ticket_id ?? 'unknown'}-${event.id}`;
    const bucket = byRun.get(key);
    if (bucket) bucket.push(event);
    else byRun.set(key, [event]);
  }

  const runs: ActivityRun[] = [];
  for (const [runId, runEvents] of byRun) {
    const ordered = [...runEvents].sort((a, b) => a.id - b.id);
    const started = ordered.find((event) => event.event_type === 'run_started') ?? ordered[0];
    const finished = ordered.find((event) => event.event_type === 'run_finished');
    const models = [...new Set(ordered.map((event) => event.model_id).filter((model): model is string => Boolean(model)))];
    let totalCostUsd = 0;
    let costStatus: RunEventCostStatus | null = null;
    for (const event of ordered) {
      if (event.event_type === 'model_call' && typeof event.cost_usd === 'number') {
        totalCostUsd += event.cost_usd;
        costStatus = mergeCostStatus(costStatus, event.cost_status ?? null);
      }
    }

    runs.push({
      runId,
      ticketId: started.ticket_id ?? undefined,
      requirementId: started.requirement_id ?? undefined,
      events: ordered,
      startedAt: started.ts,
      endedAt: finished?.ts,
      models,
      totalCostUsd,
      costStatus,
      outcome: deriveOutcome(ordered),
      hasAttention: ordered.some(eventNeedsAttention),
      lastEventId: ordered[ordered.length - 1]?.id ?? started.id,
    });
  }

  return runs.sort((a, b) => b.lastEventId - a.lastEventId);
}

export function formatRunDuration(startedAt: string, endedAt?: string): string | null {
  if (!endedAt) return null;
  const start = Date.parse(startedAt);
  const end = Date.parse(endedAt);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

export function formatEventTime(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export const MOCK_RUN_EVENTS: RunEvent[] = [
  {
    id: 1,
    project_id: 'default',
    requirement_id: 'R-001',
    ticket_id: 'T-001',
    run_id: 'RUN-demo-1',
    event_type: 'run_started',
    ts: new Date(Date.now() - 120_000).toISOString(),
    model_id: 'qwen3-coder-next',
    payload: { status: 'in_progress', reasoner_prompt_version: 'decomposer@v2.3.1' },
  },
  {
    id: 2,
    project_id: 'default',
    requirement_id: 'R-001',
    ticket_id: 'T-001',
    run_id: 'RUN-demo-1',
    event_type: 'model_call',
    ts: new Date(Date.now() - 110_000).toISOString(),
    model_id: 'qwen3-coder-next',
    input_tokens: 1800,
    output_tokens: 420,
    cost_usd: 0,
    cost_status: 'unknown',
    payload: { target_file: 'calc.py', edit_mode: 'whole_file', used_cloud_usage: false },
  },
  {
    id: 3,
    project_id: 'default',
    requirement_id: 'R-001',
    ticket_id: 'T-001',
    run_id: 'RUN-demo-1',
    event_type: 'diff_produced',
    ts: new Date(Date.now() - 100_000).toISOString(),
    model_id: 'qwen3-coder-next',
    payload: {
      diff: 'diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-def add_one(value):\n+def add_one(value):\n+    return value + 1\n',
      line_count: 6,
      target_files: ['calc.py'],
    },
  },
  {
    id: 4,
    project_id: 'default',
    requirement_id: 'R-001',
    ticket_id: 'T-001',
    run_id: 'RUN-demo-1',
    event_type: 'egress_attempt',
    ts: new Date(Date.now() - 96_000).toISOString(),
    payload: {
      stage: 'sandbox',
      kind: 'egress_attempt',
      blocked: true,
      reason: 'network_blocked_by_sandbox',
      primitive: 'docker',
      command: 'pytest calc_test.py',
      detail: 'Network blocked during sandboxed DoD run',
    },
  },
  {
    id: 5,
    project_id: 'default',
    ticket_id: 'T-015',
    run_id: 'RUN-demo-3',
    event_type: 'conflict',
    ts: new Date(Date.now() - 94_000).toISOString(),
    payload: {
      detail: 'Ticket held because another leased ticket overlaps target_files',
      conflicting_ticket_ids: ['T-012'],
      kind: 'file_overlap',
    },
  },
  {
    id: 6,
    project_id: 'default',
    requirement_id: 'R-001',
    ticket_id: 'T-001',
    run_id: 'RUN-demo-1',
    event_type: 'egress_attempt',
    ts: new Date(Date.now() - 95_000).toISOString(),
    payload: {
      blocked: true,
      destination: 'https://pypi.org/simple/requests/',
      command: 'pip install requests',
      detail: 'Network blocked during DoD test run',
    },
  },
  {
    id: 7,
    project_id: 'default',
    requirement_id: 'R-001',
    ticket_id: 'T-001',
    run_id: 'RUN-demo-1',
    event_type: 'diff_scope_reject',
    ts: new Date(Date.now() - 92_000).toISOString(),
    payload: {
      path: 'README.md',
      detail: 'Edit outside ticket target_files',
    },
  },
  {
    id: 8,
    project_id: 'default',
    requirement_id: 'R-001',
    ticket_id: 'T-001',
    run_id: 'RUN-demo-1',
    event_type: 'dod_check',
    ts: new Date(Date.now() - 90_000).toISOString(),
    model_id: 'qwen3-coder-next',
    payload: { command: 'pytest calc_test.py', status: 'pass', expected: 'pass', output_tail: '1 passed', sandbox_primitive: 'docker' },
  },
  {
    id: 9,
    project_id: 'default',
    requirement_id: 'R-001',
    ticket_id: 'T-001',
    run_id: 'RUN-demo-1',
    event_type: 'report',
    ts: new Date(Date.now() - 80_000).toISOString(),
    payload: { report_kind: 'needs_you', reason: 'diff_review_required', status: 'diff_pending' },
  },
  {
    id: 10,
    project_id: 'default',
    requirement_id: 'R-001',
    ticket_id: 'T-001',
    run_id: 'RUN-demo-1',
    event_type: 'run_finished',
    ts: new Date(Date.now() - 75_000).toISOString(),
    model_id: 'qwen3-coder-next',
    payload: { passed: true, escalated: false },
  },
  {
    id: 11,
    project_id: 'default',
    ticket_id: 'T-013',
    run_id: 'RUN-demo-2',
    event_type: 'run_started',
    ts: new Date(Date.now() - 40_000).toISOString(),
    model_id: 'gemma-4-26b-a4b',
    payload: { status: 'in_progress', reasoner_prompt_version: 'coder@v1.8.0' },
  },
  {
    id: 12,
    project_id: 'default',
    ticket_id: 'T-013',
    run_id: 'RUN-demo-2',
    event_type: 'egress_attempt',
    ts: new Date(Date.now() - 36_000).toISOString(),
    payload: {
      stage: 'sandbox',
      reason: 'sandbox_unavailable',
      primitive: 'local',
      command: 'pytest tests/test_rate_limit.py',
      detail: 'Network isolation unavailable; running DoD/test command without a sandbox.',
    },
  },
  {
    id: 13,
    project_id: 'default',
    ticket_id: 'T-013',
    run_id: 'RUN-demo-2',
    event_type: 'rollback',
    ts: new Date(Date.now() - 35_000).toISOString(),
    payload: { detail: 'Restored worktree after diff scope rejection' },
  },
  {
    id: 14,
    project_id: 'default',
    ticket_id: 'T-013',
    run_id: 'RUN-demo-2',
    event_type: 'run_finished',
    ts: new Date(Date.now() - 30_000).toISOString(),
    model_id: 'gemma-4-26b-a4b',
    payload: { passed: false, escalated: false },
  },
];
