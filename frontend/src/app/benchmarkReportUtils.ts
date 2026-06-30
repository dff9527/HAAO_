export interface TrialGroupStats {
  n: number;
  mean: number;
  min: number;
  max: number;
  range: number;
  variance: number;
  stdev: number;
  ci95_half_width: number;
}

export interface BenchmarkAggregateRow {
  trials_total: number;
  trials_counted: number;
  sample_size: number;
  trial_sample_size?: number;
  one_shot: number;
  local_finish: number;
  escalated: number;
  existing_tests_still_green: number;
  one_shot_rate: number;
  local_finish_rate: number;
  escalation_rate: number;
  existing_tests_still_green_rate: number;
  median_local_inference_sec: number;
  total_cloud_cost_usd: number;
  trial_group_stats: Record<string, TrialGroupStats>;
}

export interface BenchmarkReport {
  benchmark: string;
  generated_at: string;
  manifest?: string;
  repos: string[];
  task_ids: string[];
  task_count: number;
  local_model: string;
  cloud_model: string;
  cost_status: string;
  trials_per_task: number;
  summary: BenchmarkAggregateRow;
  by_repo: Record<string, BenchmarkAggregateRow>;
  by_task: Record<string, BenchmarkAggregateRow>;
}

export const BENCHMARK_RUN_COMMAND =
  'PYTHONPATH=. python scripts/wave4_real_repo_benchmark_report.py --trials 2';

function trialStats(row: BenchmarkAggregateRow, metric: string): TrialGroupStats | undefined {
  return row.trial_group_stats?.[metric];
}

export function formatRatePercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/** Honest framing: always show n; add variance/range when trial sample > 1. */
export function formatRateWithVariance(
  row: BenchmarkAggregateRow,
  rateKey: 'one_shot_rate' | 'local_finish_rate' | 'escalation_rate' | 'existing_tests_still_green_rate',
): string {
  const rate = row[rateKey];
  const stats = trialStats(row, rateKey);
  const counted = row.trials_counted;
  const base = `${formatRatePercent(rate)} (n=${counted})`;
  if (!stats || stats.n <= 1) return base;
  return `${base} · trial mean ${formatRatePercent(stats.mean)}, σ² ${stats.variance.toFixed(4)}, range ${formatRatePercent(stats.min)}–${formatRatePercent(stats.max)}`;
}

export function formatCostWithVariance(row: BenchmarkAggregateRow): string {
  const stats = trialStats(row, 'cloud_cost_usd');
  const base = `$${row.total_cloud_cost_usd.toFixed(4)} (n=${row.trials_counted})`;
  if (!stats || stats.n <= 1) return base;
  return `${base} · σ² ${stats.variance.toFixed(6)}, range $${stats.min.toFixed(4)}–$${stats.max.toFixed(4)}`;
}

export async function fetchBenchmarkReport(): Promise<BenchmarkReport | null> {
  try {
    const response = await fetch('/benchmark-report.json', { cache: 'no-store' });
    if (!response.ok) return null;
    return (await response.json()) as BenchmarkReport;
  } catch {
    return null;
  }
}

function makeStats(n: number, mean: number, min: number, max: number): TrialGroupStats {
  const variance = n > 1 ? 0.0225 : 0;
  const stdev = n > 1 ? Math.sqrt(variance) : 0;
  return {
    n,
    mean,
    min,
    max,
    range: max - min,
    variance,
    stdev,
    ci95_half_width: n > 1 ? 1.96 * stdev / Math.sqrt(n) : 0,
  };
}

function makeAggregateRow(overrides: Partial<BenchmarkAggregateRow> & Pick<BenchmarkAggregateRow, 'trials_counted'>): BenchmarkAggregateRow {
  const trialsCounted = overrides.trials_counted;
  const trialSample = overrides.trial_sample_size ?? 2;
  const statsN = trialSample;
  return {
    trials_total: overrides.trials_total ?? trialsCounted,
    trials_counted: trialsCounted,
    sample_size: trialsCounted,
    trial_sample_size: trialSample,
    one_shot: overrides.one_shot ?? Math.round(trialsCounted * 0.67),
    local_finish: overrides.local_finish ?? Math.round(trialsCounted * 0.83),
    escalated: overrides.escalated ?? 1,
    existing_tests_still_green: overrides.existing_tests_still_green ?? Math.round(trialsCounted * 0.83),
    one_shot_rate: overrides.one_shot_rate ?? 0.6667,
    local_finish_rate: overrides.local_finish_rate ?? 0.8333,
    escalation_rate: overrides.escalation_rate ?? 0.1667,
    existing_tests_still_green_rate: overrides.existing_tests_still_green_rate ?? 0.8333,
    median_local_inference_sec: overrides.median_local_inference_sec ?? 15.2,
    total_cloud_cost_usd: overrides.total_cloud_cost_usd ?? 0.15,
    trial_group_stats: overrides.trial_group_stats ?? {
      one_shot_rate: makeStats(statsN, 0.6667, 0.3333, 1),
      local_finish_rate: makeStats(statsN, 0.8333, 0.6667, 1),
      escalation_rate: makeStats(statsN, 0.1667, 0, 0.3333),
      existing_tests_still_green_rate: makeStats(statsN, 0.8333, 0.6667, 1),
      local_inference_sec: makeStats(statsN, 15.2, 12.1, 18.4),
      cloud_cost_usd: makeStats(statsN, 0.025, 0.01, 0.04),
    },
  };
}

export const MOCK_BENCHMARK_REPORT: BenchmarkReport = {
  benchmark: 'Wave-11-real-repo-proof',
  generated_at: '2026-06-29T12:00:00+00:00',
  manifest: 'benchmarks/r102_manifest.json',
  repos: ['click', 'tablib', 'marshmallow'],
  task_ids: ['C-01', 'T-15', 'M-03'],
  task_count: 3,
  local_model: 'qwen3-coder-next',
  cloud_model: 'claude-sonnet-4-6',
  cost_status: 'estimated',
  trials_per_task: 2,
  summary: makeAggregateRow({ trials_counted: 6, trials_total: 6 }),
  by_repo: {
    click: makeAggregateRow({ trials_counted: 2, one_shot_rate: 1, local_finish_rate: 1, escalation_rate: 0 }),
    tablib: makeAggregateRow({ trials_counted: 2, one_shot_rate: 0.5, local_finish_rate: 0.5, escalation_rate: 0.5 }),
    marshmallow: makeAggregateRow({ trials_counted: 2, one_shot_rate: 0.5, local_finish_rate: 1, escalation_rate: 0 }),
  },
  by_task: {
    'C-01': makeAggregateRow({ trials_counted: 2, one_shot_rate: 1, local_finish_rate: 1, escalation_rate: 0 }),
    'T-15': makeAggregateRow({ trials_counted: 2, one_shot_rate: 0, local_finish_rate: 0, escalation_rate: 1 }),
    'M-03': makeAggregateRow({ trials_counted: 2, one_shot_rate: 1, local_finish_rate: 1, escalation_rate: 0 }),
  },
};
