import type { EvalRun, EvalTaskSet } from './api/types';

export interface EvalBaselineComparison {
  run_id?: string | null;
  regressed?: boolean;
  reason?: string;
  current?: Record<string, number>;
  previous?: Record<string, number> | null;
  diff?: Record<string, number>;
}

export const MOCK_EVAL_TASK_SETS: EvalTaskSet[] = [
  {
    id: 'r102-smoke',
    label: 'R-102 smoke',
    description: 'One checked-in R-102 task for a quick harness smoke test.',
    task_ids: ['C-01'],
    task_count: 1,
    source: 'mock',
  },
  {
    id: 'r102-active',
    label: 'R-102 active',
    description: 'Reviewed R-102 benchmark tasks from the checked-in manifest.',
    task_ids: ['C-01', 'C-03', 'T-15'],
    task_count: 3,
    source: 'mock',
  },
];

export const MOCK_EVAL_RUNS: EvalRun[] = [
  {
    id: 'EVAL-mock-baseline',
    model_id: 'qwen3-coder-next',
    task_set_id: 'r102-smoke',
    status: 'completed',
    trials: 1,
    started_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
    finished_at: new Date(Date.now() - 3 * 86_400_000 + 120_000).toISOString(),
    summary: {
      summary: { pass_rate: 1, one_shot_rate: 1, escalation_rate: 0, local_finish_rate: 1 },
      baseline: { run_id: null, regressed: false, reason: 'no_baseline', diff: {} },
    },
    baseline_run_id: null,
    regressed: false,
    error: '',
  },
  {
    id: 'EVAL-mock-regressed',
    model_id: 'qwen3-coder-next',
    task_set_id: 'r102-smoke',
    status: 'completed',
    trials: 1,
    started_at: new Date(Date.now() - 86_400_000).toISOString(),
    finished_at: new Date(Date.now() - 86_400_000 + 180_000).toISOString(),
    summary: {
      summary: { pass_rate: 0, one_shot_rate: 0, escalation_rate: 1, local_finish_rate: 0 },
      baseline: {
        run_id: 'EVAL-mock-baseline',
        regressed: true,
        reason: 'lower_pass_or_one_shot',
        current: { pass_rate: 0, one_shot_rate: 0, escalation_rate: 1, local_finish_rate: 0 },
        previous: { pass_rate: 1, one_shot_rate: 1, escalation_rate: 0, local_finish_rate: 1 },
        diff: { pass_rate: -1, one_shot_rate: -1, escalation_rate: 1, local_finish_rate: -1 },
      },
    },
    baseline_run_id: 'EVAL-mock-baseline',
    regressed: true,
    error: '',
  },
];

export function metricsForRun(run: EvalRun): Record<string, number> {
  const summary = run.summary?.summary;
  if (!summary || typeof summary !== 'object') {
    return { pass_rate: 0, one_shot_rate: 0, escalation_rate: 0 };
  }
  const record = summary as Record<string, unknown>;
  return {
    pass_rate: numberMetric(record.pass_rate ?? record.local_finish_rate),
    one_shot_rate: numberMetric(record.one_shot_rate),
    escalation_rate: numberMetric(record.escalation_rate),
  };
}

export function baselineForRun(run: EvalRun): EvalBaselineComparison | null {
  const baseline = run.summary?.baseline;
  if (!baseline || typeof baseline !== 'object') return null;
  return baseline as EvalBaselineComparison;
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function formatDiff(value: number): string {
  const points = Math.round(value * 100);
  if (points === 0) return '±0pp';
  return `${points > 0 ? '+' : ''}${points}pp`;
}

export function diffTone(value: number, invert = false): string {
  const positive = invert ? value < 0 : value > 0;
  const negative = invert ? value > 0 : value < 0;
  if (positive) return 'text-emerald-600 dark:text-emerald-400';
  if (negative) return 'text-red-600 dark:text-red-400';
  return 'text-muted-foreground';
}

function numberMetric(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

export function mockRunsFor(modelId: string, taskSetId: string): EvalRun[] {
  return MOCK_EVAL_RUNS.filter(
    (run) => run.model_id === modelId && run.task_set_id === taskSetId,
  );
}
