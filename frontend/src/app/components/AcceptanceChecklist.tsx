import { CheckCircle2, Circle, Loader2 } from 'lucide-react';
import type { AcceptanceSummary } from '../api/types';

interface Props {
  summary: AcceptanceSummary | null;
  loading?: boolean;
}

export function AcceptanceChecklist({ summary, loading }: Props) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
        <Loader2 size={12} className="animate-spin" />
        Loading acceptance checklist…
      </div>
    );
  }
  if (!summary) return null;
  const supplyChain = summary.supply_chain;
  const hasSupplyChainSignal = Boolean(
    supplyChain
    && (
      supplyChain.changed_manifests.length > 0
      || supplyChain.added_deps.length > 0
      || supplyChain.findings.length > 0
    ),
  );

  return (
    <div className="rounded-lg border border-border bg-muted/20 px-3 py-2.5 space-y-1.5" data-testid="acceptance-checklist">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">Acceptance checklist</p>
      <ul className="space-y-1">
        {summary.checks.map((check) => (
          <li key={check.id} className="flex items-start gap-2 text-xs">
            {check.passed ? (
              <CheckCircle2 size={13} className="text-emerald-600 dark:text-emerald-400 shrink-0 mt-0.5" />
            ) : (
              <Circle size={13} className="text-muted-foreground shrink-0 mt-0.5" />
            )}
            <span className={check.passed ? 'text-foreground' : 'text-muted-foreground'}>
              {check.label}
              <span className="text-[10px] text-muted-foreground ml-1">({check.detail})</span>
            </span>
          </li>
        ))}
      </ul>
      {summary.recommendation !== 'ready' && (
        <p className="text-[11px] text-amber-700 dark:text-amber-300 pt-1">
          Recommendation: {summary.recommendation.replace(/_/g, ' ')}
        </p>
      )}
      {hasSupplyChainSignal && supplyChain && (
        <div className="mt-2 rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50/70 dark:bg-amber-950/30 px-2.5 py-2 space-y-1.5">
          <p className="text-[11px] font-semibold text-amber-800 dark:text-amber-200">
            Review dependency changes
          </p>
          {supplyChain.changed_manifests.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-300">Changed manifests</p>
              <p className="text-xs font-mono text-amber-900 dark:text-amber-100 break-all">
                {supplyChain.changed_manifests.join(', ')}
              </p>
            </div>
          )}
          {supplyChain.added_deps.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-300">Added dependencies</p>
              {supplyChain.added_deps.map((dep, index) => (
                <p key={`${dep.manifest ?? 'dep'}-${dep.name ?? index}`} className="text-xs text-amber-900 dark:text-amber-100">
                  <span className="font-mono">{dep.name ?? 'dependency'}</span>
                  {dep.version ? ` ${dep.version}` : ''}
                  {dep.manifest ? ` (${dep.manifest})` : ''}
                </p>
              ))}
            </div>
          )}
          {supplyChain.findings.length > 0 && (
            <div className="space-y-1">
              <p className="text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-300">Findings</p>
              {supplyChain.findings.map((finding, index) => (
                <p key={`${finding.source ?? 'finding'}-${finding.package ?? index}`} className="text-xs text-amber-900 dark:text-amber-100">
                  {finding.severity ? `${finding.severity.toUpperCase()} · ` : ''}
                  {finding.source ?? 'checker'}
                  {finding.package ? ` · ${finding.package}` : ''}
                  {finding.detail ? ` — ${finding.detail}` : ''}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
