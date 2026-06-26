import type { MouseEvent } from 'react';
import { ExternalLink } from 'lucide-react';

interface Props {
  prUrl: string;
  prStatus?: string;
  compact?: boolean;
  onClick?: (event: MouseEvent) => void;
}

export function PrLinkBadge({ prUrl, prStatus, compact = false, onClick }: Props) {
  const label = prStatus ? `PR ${prStatus}` : 'PR opened';

  if (compact) {
    return (
      <a
        href={prUrl}
        target="_blank"
        rel="noopener noreferrer"
        onClick={onClick}
        className="inline-flex items-center gap-0.5 text-[10px] px-1 py-0 rounded font-medium bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300 hover:underline"
        title={prUrl}
      >
        {label}
        <ExternalLink size={9} className="shrink-0" />
      </a>
    );
  }

  return (
    <a
      href={prUrl}
      target="_blank"
      rel="noopener noreferrer"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded border border-violet-200 dark:border-violet-800 bg-violet-50/80 dark:bg-violet-950/40 text-violet-700 dark:text-violet-300 hover:bg-violet-100 dark:hover:bg-violet-900/50 transition-colors"
      title={prUrl}
    >
      <ExternalLink size={11} className="shrink-0" />
      <span className="font-medium">{label}</span>
    </a>
  );
}
