import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { modelDisplayLabel } from '../modelDisplay';
import type { CloudModel } from '../types';

interface Props {
  modelId?: string | null;
  promptVersion?: string | null;
  cloudModels?: CloudModel[];
  compact?: boolean;
}

function shortPromptVersion(version: string): string {
  const trimmed = version.trim();
  if (!trimmed) return '';
  const at = trimmed.lastIndexOf('@');
  const tag = at >= 0 ? trimmed.slice(at + 1) : trimmed;
  return tag.length > 14 ? `${tag.slice(0, 12)}…` : tag;
}

export function RunProvenanceChip({ modelId, promptVersion, cloudModels = [], compact = false }: Props) {
  if (!modelId && !promptVersion) return null;

  const modelLabel = modelId ? modelDisplayLabel(modelId, cloudModels) : null;
  const promptTag = promptVersion ? shortPromptVersion(promptVersion) : null;

  const chip = (
    <span
      className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border border-border bg-muted/60 text-muted-foreground font-mono ${
        compact ? 'max-w-[200px] truncate' : ''
      }`}
    >
      {modelLabel && <span>{modelLabel}</span>}
      {modelLabel && promptTag && <span className="text-muted-foreground/50">·</span>}
      {promptTag && <span className="text-indigo-600 dark:text-indigo-400">{promptTag}</span>}
    </span>
  );

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{chip}</span>
      </TooltipTrigger>
      <TooltipContent>
        <p className="text-xs">Which planner/prompt produced this run</p>
        {modelId && <p className="text-[11px] font-mono mt-1">{modelId}</p>}
        {promptVersion && <p className="text-[11px] font-mono mt-0.5">{promptVersion}</p>}
      </TooltipContent>
    </Tooltip>
  );
}

export function provenanceFromRunStarted(payload: Record<string, unknown> | null | undefined): {
  promptVersion?: string;
} {
  const value = payload?.reasoner_prompt_version;
  return typeof value === 'string' && value.trim() ? { promptVersion: value.trim() } : {};
}
