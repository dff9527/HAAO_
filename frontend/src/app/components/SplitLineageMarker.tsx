import { GitBranch } from 'lucide-react';

interface Props {
  parentId: string;
  onClick?: () => void;
}

export function SplitLineageMarker({ parentId, onClick }: Props) {
  const className =
    'inline-flex items-center gap-0.5 text-[10px] px-1 py-0 rounded border border-violet-200 dark:border-violet-800 bg-violet-50 dark:bg-violet-950 text-violet-700 dark:text-violet-300 font-mono';

  const label = (
    <>
      <GitBranch size={9} className="shrink-0" />
      split from {parentId}
    </>
  );

  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={`${className} hover:bg-violet-100 dark:hover:bg-violet-900 transition-colors`}>
        {label}
      </button>
    );
  }

  return <span className={className}>{label}</span>;
}
