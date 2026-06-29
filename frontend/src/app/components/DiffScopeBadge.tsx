import { AlertTriangle } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import type { DiffStats } from '../types';

interface Props {
  stats: DiffStats;
  compact?: boolean;
}

export function DiffScopeBadge({ stats, compact = false }: Props) {
  const outOfScope = stats.out_of_scope_files ?? [];
  const hasWarning = outOfScope.length > 0;
  const label = hasWarning
    ? 'Touched files outside scope'
    : `minimal (${stats.files_touched} file${stats.files_touched === 1 ? '' : 's'}, +${stats.lines_added}/−${stats.lines_removed})`;

  const className = hasWarning
    ? 'bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-800'
    : 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-800';

  const badge = (
    <span
      className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${className} ${compact ? 'max-w-[180px] truncate' : ''}`}
    >
      {hasWarning && <AlertTriangle size={10} className="shrink-0" />}
      {label}
    </span>
  );

  if (!hasWarning) return badge;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{badge}</span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <p className="text-xs font-medium mb-1">Out-of-scope files</p>
        <ul className="text-[11px] font-mono space-y-0.5">
          {outOfScope.map((path) => (
            <li key={path}>{path}</li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  );
}
