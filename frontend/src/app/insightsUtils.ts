import { formatCloudCost } from './constants';
import type { InsightsCostStatus, InsightsPayload } from './api/types';

export function formatCostStatusNote(
  totalUsd: number,
  byStatus: Record<InsightsCostStatus, number>,
): string {
  const amount = formatCloudCost(totalUsd) ?? '$0.0000';
  if (totalUsd <= 0) return `${amount} · no spend recorded`;

  const labels: Array<{ status: InsightsCostStatus; word: string }> = [
    { status: 'actual', word: 'actual' },
    { status: 'estimated', word: 'estimated' },
    { status: 'unknown', word: 'unknown' },
  ];
  const active = labels.filter((item) => (byStatus[item.status] ?? 0) > 0);
  if (active.length === 0) return amount;
  if (active.length === 1) return `${amount} · ${active[0].word} only`;

  const parts = active.map((item) => {
    const share = (byStatus[item.status] ?? 0) / totalUsd;
    if (share >= 0.5) return `mostly ${item.word}`;
    if (share >= 0.2) return `some ${item.word}`;
    return `a little ${item.word}`;
  });
  return `${amount} · ${parts.join(' / ')}`;
}

export function formatCycleTimeHours(hours: number): string {
  if (hours <= 0) return '—';
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  if (hours < 48) return `${hours.toFixed(1)}h`;
  return `${(hours / 24).toFixed(1)}d`;
}

export function formatPercent(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

export function formatTaskTypeMix(mix: Record<string, number>): string {
  const entries = Object.entries(mix);
  if (!entries.length) return '—';
  return entries.map(([type, count]) => `${type} (${count})`).join(', ');
}

export function insightsHasData(data: InsightsPayload): boolean {
  return (
    data.throughput.total_done > 0
    || data.cost.total_usd > 0
    || data.model_scorecard.length > 0
    || data.local_vs_cloud.total_model_calls > 0
  );
}

export function scorecardNeedsCaveat(row: InsightsPayload['model_scorecard'][number]): boolean {
  return row.sample_size < 5 || row.human_override_count > 0;
}

export const MOCK_INSIGHTS: InsightsPayload = {
  project_id: 'default',
  range: '30d',
  generated_at: new Date().toISOString(),
  throughput: {
    total_done: 8,
    series: [
      { date: '2026-06-20', count: 2 },
      { date: '2026-06-22', count: 3 },
      { date: '2026-06-24', count: 3 },
    ],
  },
  cycle_time: {
    sample_size: 8,
    avg_hours: 18.5,
    median_hours: 14.0,
  },
  escalation_rate: {
    runs: 12,
    escalations: 2,
    rate: 0.167,
  },
  local_vs_cloud: {
    total_model_calls: 24,
    local: { count: 16, share: 0.667, cost_usd: 0 },
    cloud: { count: 8, share: 0.333, cost_usd: 12.3 },
  },
  cost: {
    total_usd: 12.3,
    by_status: { actual: 8.1, estimated: 3.7, unknown: 0.5 },
    series: [
      { date: '2026-06-20', actual: 2.1, estimated: 0.8, unknown: 0, total_usd: 2.9 },
      { date: '2026-06-22', actual: 3.5, estimated: 1.4, unknown: 0.2, total_usd: 5.1 },
      { date: '2026-06-24', actual: 2.5, estimated: 1.5, unknown: 0.3, total_usd: 4.3 },
    ],
  },
  model_scorecard: [
    {
      model_id: 'qwen3-coder-next',
      sample_size: 6,
      runs: 6,
      model_calls: 12,
      successful_runs: 5,
      success_rate: 0.833,
      retries: 3,
      escalations: 0,
      cost_usd: 0,
      cost_by_status: { actual: 0, estimated: 0, unknown: 0 },
      task_type_mix: { feature: 4, bugfix: 2 },
      human_override_count: 1,
    },
    {
      model_id: 'anthropic:claude-sonnet-4-6',
      sample_size: 3,
      runs: 3,
      model_calls: 6,
      successful_runs: 2,
      success_rate: 0.667,
      retries: 2,
      escalations: 2,
      cost_usd: 12.3,
      cost_by_status: { actual: 8.1, estimated: 3.7, unknown: 0.5 },
      task_type_mix: { feature: 2, chore: 1 },
      human_override_count: 0,
    },
  ],
};
