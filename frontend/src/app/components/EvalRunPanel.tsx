import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, FlaskConical, Loader2, Play } from 'lucide-react';
import { apiClient } from '../api/client';
import type { EvalRun } from '../api/types';
import {
  MOCK_EVAL_RUNS,
  MOCK_EVAL_TASK_SETS,
  baselineForRun,
  diffTone,
  formatDiff,
  formatPercent,
  metricsForRun,
  mockRunsFor,
} from '../evalUtils';

type EvalState = 'idle' | 'running' | 'ok' | 'fail';

interface Props {
  modelOptions: string[];
  modelLabel: (modelId: string) => string;
  defaultModelId?: string;
  compact?: boolean;
  usingMockData?: boolean;
}

export function EvalRunPanel({
  modelOptions,
  modelLabel,
  defaultModelId,
  compact = false,
  usingMockData = false,
}: Props) {
  const options = useMemo(() => Array.from(new Set(modelOptions.filter(Boolean))), [modelOptions]);
  const [taskSets, setTaskSets] = useState(usingMockData ? MOCK_EVAL_TASK_SETS : []);
  const [runs, setRuns] = useState<EvalRun[]>(usingMockData ? MOCK_EVAL_RUNS : []);
  const [modelId, setModelId] = useState(defaultModelId || options[0] || '');
  const [taskSetId, setTaskSetId] = useState('r102-smoke');
  const [trials, setTrials] = useState(1);
  const [state, setState] = useState<EvalState>('idle');
  const [message, setMessage] = useState('');
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  useEffect(() => {
    if (!modelId && (defaultModelId || options[0])) {
      setModelId(defaultModelId || options[0] || '');
    }
  }, [defaultModelId, modelId, options]);

  const loadRuns = useCallback(async (activeModel = modelId, activeTaskSet = taskSetId) => {
    if (usingMockData) {
      const items = mockRunsFor(activeModel, activeTaskSet);
      setRuns(items);
      return;
    }
    try {
      const data = await apiClient.listEvalRuns({
        modelId: activeModel || undefined,
        taskSetId: activeTaskSet || undefined,
        limit: 6,
      });
      setRuns(data);
    } catch {
      setRuns(MOCK_EVAL_RUNS);
    }
  }, [modelId, taskSetId, usingMockData]);

  useEffect(() => {
    if (usingMockData) {
      setTaskSets(MOCK_EVAL_TASK_SETS);
      return;
    }
    let active = true;
    apiClient
      .listEvalTaskSets()
      .then((items) => {
        if (!active) return;
        setTaskSets(items);
        if (!items.some((item) => item.id === taskSetId) && items[0]) {
          setTaskSetId(items[0].id);
        }
      })
      .catch(() => {
        if (active) {
          setTaskSets(MOCK_EVAL_TASK_SETS);
          setMessage('Using demo eval task sets.');
        }
      });
    return () => {
      active = false;
    };
  }, [taskSetId, usingMockData]);

  useEffect(() => {
    if (!modelId || !taskSetId) return;
    void loadRuns(modelId, taskSetId);
  }, [loadRuns, modelId, taskSetId]);

  useEffect(() => {
    if (usingMockData) return;
    if (!runs.some((run) => run.status === 'running')) return;
    const timer = window.setInterval(() => {
      void loadRuns();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadRuns, runs, usingMockData]);

  useEffect(() => {
    if (!runs.length) {
      setSelectedRunId(null);
      return;
    }
    if (!selectedRunId || !runs.some((run) => run.id === selectedRunId)) {
      setSelectedRunId(runs[0].id);
    }
  }, [runs, selectedRunId]);

  async function startEval() {
    if (!modelId || !taskSetId) return;
    setState('running');
    setMessage('');
    if (usingMockData) {
      const mockId = `EVAL-mock-${Date.now().toString(36)}`;
      const baseline = runs.find((run) => run.status === 'completed' && !run.regressed) ?? runs[0];
      const previous = baseline ? metricsForRun(baseline) : null;
      const current = { pass_rate: 0.5, one_shot_rate: 0.5, escalation_rate: 0.25, local_finish_rate: 0.5 };
      const diff = previous
        ? {
            pass_rate: current.pass_rate - previous.pass_rate,
            one_shot_rate: current.one_shot_rate - previous.one_shot_rate,
            escalation_rate: current.escalation_rate - previous.escalation_rate,
            local_finish_rate: current.local_finish_rate - previous.local_finish_rate,
          }
        : {};
      const regressed = Boolean(previous && (diff.pass_rate < 0 || diff.one_shot_rate < 0));
      const run: EvalRun = {
        id: mockId,
        model_id: modelId,
        task_set_id: taskSetId,
        status: 'completed',
        trials,
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
        summary: {
          summary: current,
          baseline: {
            run_id: baseline?.id ?? null,
            regressed,
            reason: previous ? (regressed ? 'lower_pass_or_one_shot' : 'no_regression') : 'no_baseline',
            current,
            previous,
            diff,
          },
        },
        baseline_run_id: baseline?.id ?? null,
        regressed,
        error: '',
      };
      setRuns((prev) => [run, ...prev].slice(0, 6));
      setState('ok');
      setMessage('Demo eval completed.');
      return;
    }
    try {
      const run = await apiClient.startEvalRun({
        model_id: modelId,
        task_set_id: taskSetId,
        trials,
      });
      setRuns((prev) => [run, ...prev.filter((item) => item.id !== run.id)].slice(0, 6));
      setState('ok');
      setMessage('Eval run queued.');
      void loadRuns(modelId, taskSetId);
    } catch (error) {
      setState('fail');
      setMessage(error instanceof Error ? error.message : 'Could not start eval run.');
    }
  }

  const selectedRun = runs.find((run) => run.id === selectedRunId) ?? runs[0];
  const selectedMetrics = selectedRun ? metricsForRun(selectedRun) : null;
  const selectedBaseline = selectedRun ? baselineForRun(selectedRun) : null;

  return (
    <div className="rounded-xl border border-border bg-card px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div>
          <h2 className="text-xs font-semibold text-foreground flex items-center gap-1.5">
            <FlaskConical size={13} className="text-emerald-600 dark:text-emerald-400" />
            Test model
          </h2>
          {!compact && (
            <p className="text-[11px] text-muted-foreground mt-0.5">
              R-102 eval runs compare this model against its last completed baseline.
            </p>
          )}
        </div>
        {selectedRun && selectedRun.status !== 'running' && (
          <span
            className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-[11px] ${
              selectedRun.regressed
                ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300'
                : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300'
            }`}
          >
            {selectedRun.regressed ? <AlertTriangle size={12} /> : <CheckCircle2 size={12} />}
            {selectedRun.regressed ? 'Regression' : 'No regression'}
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_84px_auto] gap-2">
        <select
          value={modelId}
          onChange={(event) => setModelId(event.target.value)}
          className="text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground min-w-0"
        >
          {options.map((option) => (
            <option key={option} value={option}>
              {modelLabel(option)}
            </option>
          ))}
        </select>
        <select
          value={taskSetId}
          onChange={(event) => setTaskSetId(event.target.value)}
          className="text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground min-w-0"
        >
          {taskSets.map((taskSet) => (
            <option key={taskSet.id} value={taskSet.id}>
              {taskSet.label} ({taskSet.task_count})
            </option>
          ))}
        </select>
        <input
          type="number"
          min={1}
          max={10}
          value={trials}
          onChange={(event) => setTrials(Math.max(1, Math.min(10, Number(event.target.value) || 1)))}
          className="text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
          aria-label="Trials"
        />
        <button
          type="button"
          onClick={startEval}
          disabled={!modelId || !taskSetId || state === 'running'}
          className="inline-flex items-center justify-center gap-1.5 rounded border border-border bg-foreground text-background px-3 py-1.5 text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          title="Start eval"
        >
          {state === 'running' ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          Run
        </button>
      </div>

      {message && (
        <p className={`text-[11px] mt-2 ${state === 'fail' ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'}`}>
          {message}
        </p>
      )}

      {selectedMetrics && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
          <Metric label="Pass" value={formatPercent(selectedMetrics.pass_rate)} />
          <Metric label="One-shot" value={formatPercent(selectedMetrics.one_shot_rate)} />
          <Metric label="Escalation" value={formatPercent(selectedMetrics.escalation_rate)} />
          <Metric label="Status" value={selectedRun?.status ?? '—'} />
        </div>
      )}

      {selectedBaseline?.previous && selectedBaseline.diff && (
        <div className="mt-3 rounded-lg border border-border bg-muted/20 px-3 py-2.5">
          <p className="text-[11px] text-muted-foreground mb-2">
            vs baseline{' '}
            <span className="font-mono text-foreground">{selectedBaseline.run_id ?? '—'}</span>
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <DiffMetric
              label="Pass"
              current={selectedBaseline.current?.pass_rate ?? 0}
              previous={selectedBaseline.previous.pass_rate ?? 0}
              diff={selectedBaseline.diff.pass_rate ?? 0}
            />
            <DiffMetric
              label="One-shot"
              current={selectedBaseline.current?.one_shot_rate ?? 0}
              previous={selectedBaseline.previous.one_shot_rate ?? 0}
              diff={selectedBaseline.diff.one_shot_rate ?? 0}
            />
            <DiffMetric
              label="Escalation"
              current={selectedBaseline.current?.escalation_rate ?? 0}
              previous={selectedBaseline.previous.escalation_rate ?? 0}
              diff={selectedBaseline.diff.escalation_rate ?? 0}
              invertDiff
            />
          </div>
        </div>
      )}

      {runs.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[11px] min-w-[520px]">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="text-left font-medium py-1.5">Run</th>
                <th className="text-right font-medium py-1.5">Pass</th>
                <th className="text-right font-medium py-1.5">One-shot</th>
                <th className="text-right font-medium py-1.5">Trials</th>
                <th className="text-right font-medium py-1.5">Result</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const metrics = metricsForRun(run);
                const selected = run.id === selectedRunId;
                return (
                  <tr
                    key={run.id}
                    onClick={() => setSelectedRunId(run.id)}
                    className={`border-b border-border last:border-0 cursor-pointer transition-colors ${
                      selected ? 'bg-muted/40' : 'hover:bg-muted/20'
                    } ${run.regressed ? 'text-red-700 dark:text-red-300' : ''}`}
                  >
                    <td className="py-1.5 pr-2 font-mono">{run.id}</td>
                    <td className="py-1.5 text-right tabular-nums">{formatPercent(metrics.pass_rate)}</td>
                    <td className="py-1.5 text-right tabular-nums">{formatPercent(metrics.one_shot_rate)}</td>
                    <td className="py-1.5 text-right tabular-nums">{run.trials}</td>
                    <td className="py-1.5 text-right font-medium">
                      {run.status === 'running' ? 'running' : run.regressed ? 'regressed' : run.status}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-muted/30 px-2.5 py-2">
      <p className="text-[10px] text-muted-foreground uppercase">{label}</p>
      <p className="text-sm font-semibold text-foreground tabular-nums mt-0.5">{value}</p>
    </div>
  );
}

function DiffMetric({
  label,
  current,
  previous,
  diff,
  invertDiff = false,
}: {
  label: string;
  current: number;
  previous: number;
  diff: number;
  invertDiff?: boolean;
}) {
  return (
    <div className="rounded border border-border bg-card px-2 py-1.5">
      <p className="text-[10px] text-muted-foreground uppercase">{label}</p>
      <p className="text-xs font-medium text-foreground tabular-nums mt-0.5">
        {formatPercent(previous)} → {formatPercent(current)}
      </p>
      <p className={`text-[11px] tabular-nums mt-0.5 ${diffTone(diff, invertDiff)}`}>
        {formatDiff(diff)}
      </p>
    </div>
  );
}
