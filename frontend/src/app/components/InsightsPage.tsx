import { useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  XAxis,
  YAxis,
} from 'recharts';
import { BarChart3, Cloud, Cpu, Loader2, TrendingUp, GitPullRequest, DollarSign } from 'lucide-react';
import { apiClient } from '../api/client';
import type { InsightsPayload, InsightsRange } from '../api/types';
import {
  MOCK_INSIGHTS,
  formatCostStatusNote,
  formatCycleTimeHours,
  formatPercent,
  formatTaskTypeMix,
  insightsHasData,
  scorecardNeedsCaveat,
} from '../insightsUtils';
import { formatHoursSaved } from '../trustUtils';
import { DEFAULT_LOCAL_MODELS, formatCloudCost } from '../constants';
import { modelDisplayLabel } from '../modelDisplay';
import type { CloudModel } from '../types';
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from './ui/chart';
import { EvalRunPanel } from './EvalRunPanel';

const RANGE_OPTIONS: Array<{ id: InsightsRange; label: string }> = [
  { id: '7d', label: '7d' },
  { id: '30d', label: '30d' },
  { id: 'all', label: 'All' },
];

const throughputChartConfig = {
  count: { label: 'Tickets done', color: 'hsl(var(--chart-1))' },
} satisfies ChartConfig;

const costChartConfig = {
  total_usd: { label: 'Total', color: 'hsl(var(--chart-2))' },
} satisfies ChartConfig;

const splitChartConfig = {
  local: { label: 'Local', color: 'hsl(199 89% 48%)' },
  cloud: { label: 'Cloud', color: 'hsl(38 92% 50%)' },
} satisfies ChartConfig;

function shortDate(iso: string): string {
  const parts = iso.split('-');
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : iso;
}

interface Props {
  projectId: string;
  cloudModels: CloudModel[];
  usingMockData: boolean;
}

function MetricCard({
  label,
  value,
  note,
  icon: Icon,
}: {
  label: string;
  value: string;
  note?: string;
  icon: typeof TrendingUp;
}) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground uppercase tracking-wide">
        <Icon size={12} />
        {label}
      </div>
      <p className="text-xl font-semibold text-foreground mt-1 tabular-nums">{value}</p>
      {note && <p className="text-[11px] text-muted-foreground mt-1 leading-snug">{note}</p>}
    </div>
  );
}

export function InsightsPage({ projectId, cloudModels, usingMockData }: Props) {
  const [range, setRange] = useState<InsightsRange>('30d');
  const [data, setData] = useState<InsightsPayload | null>(usingMockData ? MOCK_INSIGHTS : null);
  const [loading, setLoading] = useState(!usingMockData);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (usingMockData) {
      setData({ ...MOCK_INSIGHTS, project_id: projectId, range });
      setLoading(false);
      setError(false);
      return;
    }
    let active = true;
    setLoading(true);
    setError(false);
    apiClient
      .getInsights(projectId, range)
      .then((payload) => {
        if (active) setData(payload);
      })
      .catch(() => {
        if (active) {
          setData(MOCK_INSIGHTS);
          setError(true);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, range, usingMockData]);

  const splitData = useMemo(() => {
    if (!data) return [];
    return [
      { name: 'local', value: data.local_vs_cloud.local.count, fill: 'var(--color-local)' },
      { name: 'cloud', value: data.local_vs_cloud.cloud.count, fill: 'var(--color-cloud)' },
    ].filter((item) => item.value > 0);
  }, [data]);

  const throughputSeries = useMemo(
    () => (data?.throughput.series ?? []).map((point) => ({ ...point, label: shortDate(point.date) })),
    [data],
  );

  const costSeries = useMemo(
    () => (data?.cost.series ?? []).map((point) => ({ ...point, label: shortDate(point.date) })),
    [data],
  );

  const empty = data ? !insightsHasData(data) : false;
  const evalModelOptions = useMemo(
    () => Array.from(new Set([
      ...(data?.model_scorecard.map((row) => row.model_id) ?? []),
      ...cloudModels.map((model) => model.id),
      ...DEFAULT_LOCAL_MODELS,
    ])),
    [cloudModels, data],
  );

  return (
    <div className="flex-1 overflow-y-auto bg-background">
      <div className="mx-auto max-w-4xl px-4 sm:px-6 py-8 space-y-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-base font-semibold flex items-center gap-2">
              <BarChart3 size={16} className="text-violet-600 dark:text-violet-400" />
              Insights
            </h1>
            <p className="text-xs text-muted-foreground mt-1">
              Throughput, cycle time, escalation, cost, and per-model scorecards.
            </p>
          </div>
          <div className="flex items-center gap-1 rounded-lg border border-border bg-muted/40 p-0.5">
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                onClick={() => setRange(option.id)}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  range === option.id
                    ? 'bg-card text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <p className="text-xs text-amber-700 dark:text-amber-300 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-950/30 px-3 py-2">
            Could not reach the API — showing demo insights.
          </p>
        )}

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            Loading insights…
          </div>
        ) : empty || !data ? (
          <>
            <EvalRunPanel
              compact
              usingMockData={usingMockData}
              modelOptions={evalModelOptions}
              defaultModelId={evalModelOptions[0]}
              modelLabel={(modelId) => modelDisplayLabel(modelId, cloudModels)}
            />
            <div className="rounded-xl border border-dashed border-border px-4 py-12 text-center">
              <p className="text-sm font-medium text-foreground">No insights yet</p>
              <p className="text-xs text-muted-foreground mt-1">
                Ship a few tickets — metrics appear after runs complete in this range.
              </p>
            </div>
          </>
        ) : (
          <>
            <EvalRunPanel
              compact
              usingMockData={usingMockData}
              modelOptions={evalModelOptions}
              defaultModelId={evalModelOptions[0]}
              modelLabel={(modelId) => modelDisplayLabel(modelId, cloudModels)}
            />

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
              <MetricCard
                label="Throughput"
                value={String(data.throughput.total_done)}
                note="Tickets accepted in range"
                icon={TrendingUp}
              />
              <MetricCard
                label="Median cycle time"
                value={formatCycleTimeHours(data.cycle_time.median_hours)}
                note={`n=${data.cycle_time.sample_size} accepted tickets`}
                icon={BarChart3}
              />
              <MetricCard
                label="Time to first PR"
                value={formatCycleTimeHours(data.time_to_first_pr?.median_hours ?? 0)}
                note={`n=${data.time_to_first_pr?.sample_size ?? 0} tickets with PRs`}
                icon={GitPullRequest}
              />
              <MetricCard
                label="Escalation rate"
                value={formatPercent(data.escalation_rate.rate)}
                note={`${data.escalation_rate.escalations} of ${data.escalation_rate.runs} runs`}
                icon={Cpu}
              />
              <MetricCard
                label="Total cost"
                value={formatCloudCost(data.cost.total_usd) ?? '$0.0000'}
                note={formatCostStatusNote(data.cost.total_usd, data.cost.by_status)}
                icon={Cloud}
              />
            </div>

            {data.roi && (
              <div className="rounded-xl border border-border bg-card px-4 py-4">
                <div className="flex items-center gap-1.5 mb-3">
                  <DollarSign size={13} className="text-emerald-600 dark:text-emerald-400" />
                  <h2 className="text-xs font-semibold text-foreground">ROI estimate</h2>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">Hours saved</p>
                    <p className="font-semibold tabular-nums">{formatHoursSaved(data.roi.estimated_hours_saved)}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">Cloud spend</p>
                    <p className="font-semibold tabular-nums">{formatCloudCost(data.roi.cloud_cost_usd) ?? '$0.0000'}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">Net value</p>
                    <p className="font-semibold tabular-nums">{formatCloudCost(data.roi.estimated_net_value_usd) ?? '$0.0000'}</p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase text-muted-foreground">Interventions</p>
                    <p className="font-semibold tabular-nums">
                      {data.roi.intervention_count ?? data.escalation_rate.escalations}
                    </p>
                  </div>
                </div>
                <p className="text-[11px] text-muted-foreground mt-3">{data.roi.method}</p>
              </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <div className="rounded-xl border border-border bg-card px-4 py-4">
                <h2 className="text-xs font-semibold text-foreground mb-3">Throughput over time</h2>
                {throughputSeries.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-8 text-center">No completed tickets in range.</p>
                ) : (
                  <ChartContainer config={throughputChartConfig} className="h-[220px] w-full aspect-auto">
                    <BarChart data={throughputSeries} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                      <CartesianGrid vertical={false} />
                      <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={11} />
                      <YAxis allowDecimals={false} tickLine={false} axisLine={false} fontSize={11} width={28} />
                      <ChartTooltip content={<ChartTooltipContent />} />
                      <Bar dataKey="count" fill="var(--color-count)" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ChartContainer>
                )}
              </div>

              <div className="rounded-xl border border-border bg-card px-4 py-4">
                <h2 className="text-xs font-semibold text-foreground mb-3">Cost over time</h2>
                {costSeries.length === 0 ? (
                  <p className="text-xs text-muted-foreground py-8 text-center">No model-call spend in range.</p>
                ) : (
                  <ChartContainer config={costChartConfig} className="h-[220px] w-full aspect-auto">
                    <LineChart data={costSeries} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                      <CartesianGrid vertical={false} />
                      <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={11} />
                      <YAxis tickLine={false} axisLine={false} fontSize={11} width={36} />
                      <ChartTooltip content={<ChartTooltipContent />} />
                      <Line
                        type="monotone"
                        dataKey="total_usd"
                        stroke="var(--color-total_usd)"
                        strokeWidth={2}
                        dot={false}
                      />
                    </LineChart>
                  </ChartContainer>
                )}
              </div>
            </div>

            <div className="rounded-xl border border-border bg-card px-4 py-4">
              <h2 className="text-xs font-semibold text-foreground mb-1">Local vs cloud</h2>
              <p className="text-[11px] text-muted-foreground mb-3">
                {data.local_vs_cloud.total_model_calls} model calls in range.
              </p>
              {splitData.length === 0 ? (
                <p className="text-xs text-muted-foreground py-6 text-center">No model calls yet.</p>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,200px)_1fr] gap-4 items-center">
                  <ChartContainer config={splitChartConfig} className="h-[180px] w-full max-w-[200px] mx-auto aspect-square">
                    <PieChart>
                      <ChartTooltip content={<ChartTooltipContent hideLabel />} />
                      <Pie data={splitData} dataKey="value" nameKey="name" innerRadius={48} outerRadius={72} strokeWidth={2}>
                        {splitData.map((entry) => (
                          <Cell key={entry.name} fill={entry.fill} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ChartContainer>
                  <div className="space-y-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5 text-foreground">
                        <span className="w-2 h-2 rounded-full bg-sky-500" />
                        Local
                      </span>
                      <span className="tabular-nums text-muted-foreground">
                        {data.local_vs_cloud.local.count} ({formatPercent(data.local_vs_cloud.local.share)})
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5 text-foreground">
                        <span className="w-2 h-2 rounded-full bg-amber-500" />
                        Cloud
                      </span>
                      <span className="tabular-nums text-muted-foreground">
                        {data.local_vs_cloud.cloud.count} ({formatPercent(data.local_vs_cloud.cloud.share)})
                        {data.local_vs_cloud.cloud.cost_usd > 0
                          ? ` · ${formatCloudCost(data.local_vs_cloud.cloud.cost_usd)}`
                          : ''}
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="rounded-xl border border-border bg-card overflow-hidden">
              <div className="px-4 py-3 border-b border-border">
                <h2 className="text-xs font-semibold text-foreground">Model scorecard</h2>
                <p className="text-[11px] text-muted-foreground mt-0.5">
                  Low sample size or easy task mix can make cheap models look better — check n, types, and overrides.
                </p>
              </div>
              {data.model_scorecard.length === 0 ? (
                <p className="text-xs text-muted-foreground px-4 py-8 text-center">No model runs in range.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs min-w-[720px]">
                    <thead>
                      <tr className="bg-muted/50 border-b border-border">
                        <th className="text-left font-semibold text-muted-foreground px-4 py-2">Model</th>
                        <th className="text-right font-semibold text-muted-foreground px-3 py-2">Success</th>
                        <th className="text-right font-semibold text-muted-foreground px-3 py-2">Retries</th>
                        <th className="text-right font-semibold text-muted-foreground px-3 py-2">Escalations</th>
                        <th className="text-right font-semibold text-muted-foreground px-3 py-2">Cost</th>
                        <th className="text-right font-semibold text-muted-foreground px-3 py-2">n</th>
                        <th className="text-left font-semibold text-muted-foreground px-3 py-2">Task mix</th>
                        <th className="text-right font-semibold text-muted-foreground px-4 py-2">Overrides</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.model_scorecard.map((row) => (
                        <tr key={row.model_id} className="border-b border-border last:border-0">
                          <td className="px-4 py-2.5 text-foreground">
                            <span className="font-medium">{modelDisplayLabel(row.model_id, cloudModels)}</span>
                            <span className="block font-mono text-[10px] text-muted-foreground mt-0.5">{row.model_id}</span>
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums">{formatPercent(row.success_rate)}</td>
                          <td className="px-3 py-2.5 text-right tabular-nums">{row.retries}</td>
                          <td className="px-3 py-2.5 text-right tabular-nums">{row.escalations}</td>
                          <td className="px-3 py-2.5 text-right tabular-nums">
                            {formatCloudCost(row.cost_usd) ?? '$0.0000'}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums">{row.sample_size}</td>
                          <td className="px-3 py-2.5 text-muted-foreground">{formatTaskTypeMix(row.task_type_mix)}</td>
                          <td className="px-4 py-2.5 text-right tabular-nums">{row.human_override_count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {data.model_scorecard.some(scorecardNeedsCaveat) && (
                <p className="text-[11px] text-amber-700 dark:text-amber-300 px-4 py-2 border-t border-border bg-amber-50/40 dark:bg-amber-950/20">
                  Rows with n &lt; 5 or human overrides may not compare fairly across models.
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
