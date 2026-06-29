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

  return (
    <div className="rounded-lg border border-border bg-muted/20 px-3 py-2.5 space-y-1.5">
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
    </div>
  );
}
