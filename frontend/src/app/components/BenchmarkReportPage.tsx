import { useEffect, useState } from 'react';
import { ArrowLeft, Beaker, Loader2 } from 'lucide-react';
import {
  BENCHMARK_RUN_COMMAND,
  MOCK_BENCHMARK_REPORT,
  fetchBenchmarkReport,
  formatCostWithVariance,
  formatRateWithVariance,
  type BenchmarkAggregateRow,
  type BenchmarkReport,
} from '../benchmarkReportUtils';

interface Props {
  usingMockData?: boolean;
  onBack: () => void;
}

function MetricRow({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-1 py-2 border-b border-border last:border-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-xs text-foreground tabular-nums text-right max-w-xl leading-relaxed">{value}</span>
    </div>
  );
}

function AggregateSection({
  title,
  row,
}: {
  title: string;
  row: BenchmarkAggregateRow;
}) {
  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="text-xs font-semibold text-foreground">{title}</h2>
        <p className="text-[11px] text-muted-foreground mt-0.5">
          {row.trials_counted} counted trial{row.trials_counted === 1 ? '' : 's'}
          {row.trials_total !== row.trials_counted ? ` (${row.trials_total} total)` : ''}
        </p>
      </div>
      <div className="px-4 py-1">
        <MetricRow label="One-shot rate" value={formatRateWithVariance(row, 'one_shot_rate')} />
        <MetricRow label="Local finish rate" value={formatRateWithVariance(row, 'local_finish_rate')} />
        <MetricRow label="Escalation rate" value={formatRateWithVariance(row, 'escalation_rate')} />
        <MetricRow label="Existing tests still green" value={formatRateWithVariance(row, 'existing_tests_still_green_rate')} />
        <MetricRow label="Median local inference" value={`${row.median_local_inference_sec.toFixed(2)}s (n=${row.trials_counted})`} />
        <MetricRow label="Total cloud cost" value={formatCostWithVariance(row)} />
      </div>
    </div>
  );
}

export function BenchmarkReportPage({ usingMockData = false, onBack }: Props) {
  const [report, setReport] = useState<BenchmarkReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [usedFallback, setUsedFallback] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void fetchBenchmarkReport()
      .then((payload) => {
        if (!active) return;
        if (payload) {
          setReport(payload);
          setUsedFallback(false);
        } else if (usingMockData) {
          setReport(MOCK_BENCHMARK_REPORT);
          setUsedFallback(true);
        } else {
          setReport(null);
          setUsedFallback(false);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [usingMockData]);

  return (
    <div className="flex-1 overflow-y-auto bg-background" data-testid="benchmark-report-page">
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-8 space-y-5">
        <div className="flex items-start gap-3">
          <button
            type="button"
            onClick={onBack}
            className="mt-0.5 h-8 w-8 flex items-center justify-center rounded-lg border border-border hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Back to Insights"
          >
            <ArrowLeft size={14} />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="text-base font-semibold flex items-center gap-2">
              <Beaker size={16} className="text-emerald-600 dark:text-emerald-400" />
              Real-repo benchmark proof
            </h1>
            <p className="text-xs text-muted-foreground mt-1">
              Indicative results from the R-102 task set — rates always include sample size and variance where available.
            </p>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            Loading benchmark report…
          </div>
        ) : !report ? (
          <div
            className="rounded-xl border border-dashed border-border px-4 py-10 text-center space-y-3"
            data-testid="benchmark-empty-state"
          >
            <p className="text-sm font-medium text-foreground">No benchmark report yet</p>
            <p className="text-xs text-muted-foreground max-w-md mx-auto">
              Run the real-repo proof script locally. The latest JSON is read from{' '}
              <code className="font-mono text-[11px] bg-muted px-1 py-0.5 rounded">public/benchmark-report.json</code>{' '}
              after you copy or symlink the output.
            </p>
            <pre className="text-left text-[11px] font-mono bg-muted/60 rounded-lg px-3 py-2 overflow-x-auto max-w-lg mx-auto text-foreground">
              {BENCHMARK_RUN_COMMAND}
            </pre>
          </div>
        ) : (
          <div className="space-y-4" data-testid="benchmark-report-content">
            {usedFallback && (
              <p className="text-xs text-amber-700 dark:text-amber-300 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-950/30 px-3 py-2">
                Showing embedded demo report — copy your latest JSON to public/benchmark-report.json for live numbers.
              </p>
            )}
            <div className="rounded-xl border border-border bg-muted/20 px-4 py-3 text-xs space-y-1">
              <p>
                <span className="text-muted-foreground">Generated:</span>{' '}
                <span className="font-mono">{report.generated_at}</span>
              </p>
              <p>
                <span className="text-muted-foreground">Tasks:</span>{' '}
                {report.task_count} ({report.task_ids.join(', ')}) · {report.trials_per_task} trial(s) each
              </p>
              <p>
                <span className="text-muted-foreground">Repos:</span> {report.repos.join(', ')}
              </p>
              <p>
                <span className="text-muted-foreground">Models:</span>{' '}
                local <span className="font-mono">{report.local_model}</span>
                {' · '}
                cloud <span className="font-mono">{report.cloud_model}</span>
              </p>
            </div>

            <AggregateSection title="Overall" row={report.summary} />

            <div className="space-y-3">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">By repository</h2>
              {Object.entries(report.by_repo).map(([repo, row]) => (
                <AggregateSection key={repo} title={repo} row={row} />
              ))}
            </div>

            <div className="space-y-3">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">By task</h2>
              {Object.entries(report.by_task).map(([taskId, row]) => (
                <AggregateSection key={taskId} title={taskId} row={row} />
              ))}
            </div>

            <p className="text-[11px] text-muted-foreground leading-relaxed">
              Small-n runs are indicative, not headline metrics. Prefer trial ranges and variance over a single percentage.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
