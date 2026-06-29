import type { DiffStats } from './types';

export function parseDiffStats(raw: unknown): DiffStats | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const record = raw as Record<string, unknown>;
  if (typeof record.files_touched !== 'number') return undefined;
  return {
    files_touched: record.files_touched,
    lines_added: typeof record.lines_added === 'number' ? record.lines_added : 0,
    lines_removed: typeof record.lines_removed === 'number' ? record.lines_removed : 0,
    out_of_scope_files: Array.isArray(record.out_of_scope_files)
      ? record.out_of_scope_files.filter((item): item is string => typeof item === 'string')
      : [],
  };
}
