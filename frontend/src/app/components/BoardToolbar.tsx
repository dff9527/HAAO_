import { forwardRef } from 'react';
import { AlertTriangle, Circle, Filter, Loader2, Play, Search, Settings, Square, X } from 'lucide-react';
import type { BoardFilters, BoardPriorityFilter, BoardTypeFilter } from '../boardFilters';
import { hasActiveBoardFilters } from '../boardFilters';

interface Props {
  attentionCount: number;
  attentionFilter: boolean;
  onToggleAttentionFilter: () => void;
  boardLive: boolean;
  autoRunRunning: boolean;
  autoRunPending: boolean;
  autoRunError: string;
  onToggleAutoRun: () => void;
  projectPathReady: boolean;
  modelsConfigured: boolean;
  ticketCount: number;
  filteredCount: number;
  loading: boolean;
  filters: BoardFilters;
  onFiltersChange: (filters: BoardFilters) => void;
  onClearFilters: () => void;
  onOpenSetup: () => void;
}

export const BoardToolbar = forwardRef<HTMLInputElement, Props>(function BoardToolbar({
  attentionCount,
  attentionFilter,
  onToggleAttentionFilter,
  boardLive,
  autoRunRunning,
  autoRunPending,
  autoRunError,
  onToggleAutoRun,
  projectPathReady,
  modelsConfigured,
  ticketCount,
  filteredCount,
  loading,
  filters,
  onFiltersChange,
  onClearFilters,
  onOpenSetup,
}, searchInputRef) {
  const showSetupGuide = !loading && (!projectPathReady || !modelsConfigured);
  const filtersActive = hasActiveBoardFilters(filters);

  return (
    <div className="shrink-0 border-b border-border bg-card/80 px-4 py-2 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[180px] max-w-md">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <input
            ref={searchInputRef}
            type="search"
            value={filters.query}
            onChange={(event) => onFiltersChange({ ...filters, query: event.target.value })}
            placeholder="Search tickets…"
            aria-label="Search tickets by ID or title"
            className="w-full h-8 rounded-lg border border-border bg-background pl-8 pr-8 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
          />
          {filters.query && (
            <button
              type="button"
              onClick={() => onFiltersChange({ ...filters, query: '' })}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              aria-label="Clear search"
            >
              <X size={12} />
            </button>
          )}
        </div>

        <select
          value={filters.type}
          onChange={(event) => onFiltersChange({ ...filters, type: event.target.value as BoardTypeFilter })}
          aria-label="Filter by ticket type"
          className="h-8 rounded-lg border border-border bg-background px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="all">All types</option>
          <option value="feature">Feature</option>
          <option value="bugfix">Bugfix</option>
          <option value="refactor">Refactor</option>
          <option value="test">Test</option>
          <option value="chore">Chore</option>
        </select>

        <select
          value={filters.priority}
          onChange={(event) => onFiltersChange({ ...filters, priority: event.target.value as BoardPriorityFilter })}
          aria-label="Filter by priority"
          className="h-8 rounded-lg border border-border bg-background px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="all">All priorities</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>

        <button
          type="button"
          onClick={onToggleAttentionFilter}
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
            attentionFilter
              ? 'border-primary bg-primary/10 text-primary'
              : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
          }`}
        >
          <Filter size={12} />
          Needs you
          {attentionCount > 0 && (
            <span className={`tabular-nums px-1.5 py-0 rounded-full text-[10px] ${
              attentionFilter ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground'
            }`}
            >
              {attentionCount}
            </span>
          )}
        </button>

        {filtersActive && (
          <button
            type="button"
            onClick={onClearFilters}
            className="text-[11px] text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
          >
            Clear filters
          </button>
        )}

        <button
          type="button"
          onClick={onToggleAutoRun}
          disabled={autoRunPending || !projectPathReady}
          title={
            !projectPathReady
              ? 'Set a repository path before enabling auto-run'
              : autoRunError
                ? `Auto-run last error: ${autoRunError}`
                : autoRunRunning
                  ? 'Auto-run is on — the orchestrator picks up Ready tickets and runs them. Click to stop.'
                  : 'Start auto-run — the orchestrator dispatches and runs Ready tickets for this project.'
          }
          className={`ml-auto inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
            autoRunRunning
              ? 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
              : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
          }`}
        >
          {autoRunPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : autoRunRunning ? (
            <Square size={10} className="fill-current" />
          ) : (
            <Play size={12} />
          )}
          Auto-run
          {autoRunRunning && !autoRunPending && (
            <Circle size={7} className="fill-emerald-500 text-emerald-500 animate-pulse" />
          )}
          {!autoRunRunning && autoRunError && (
            <Circle size={7} className="fill-red-500 text-red-500" />
          )}
        </button>

        {boardLive && (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Circle size={8} className="fill-emerald-500 text-emerald-500 animate-pulse" />
            Live updates
          </span>
        )}

        {!projectPathReady && (
          <span className="inline-flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-300">
            <AlertTriangle size={12} />
            Set repository path in project settings before running work
          </span>
        )}
      </div>

      {!loading && filtersActive && (
        <p className="text-[11px] text-muted-foreground">
          Showing {filteredCount} of {ticketCount} tickets
          <span className="hidden sm:inline"> · Press <kbd className="px-1 py-0.5 rounded border border-border bg-muted font-mono text-[10px]">/</kbd> to search</span>
        </p>
      )}

      {showSetupGuide && (
        <div className="rounded-lg border border-dashed border-border bg-muted/30 px-3 py-2.5">
          <p className="text-xs font-medium text-foreground">First-time setup</p>
          <ol className="mt-1.5 space-y-1 text-[11px] text-muted-foreground list-decimal list-inside">
            {!projectPathReady && (
              <li>Open project settings and set your git repository path.</li>
            )}
            {!modelsConfigured && (
              <li>
                <button
                  type="button"
                  onClick={onOpenSetup}
                  className="inline-flex items-center gap-1 text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                >
                  <Settings size={11} />
                  Connect local model endpoints
                </button>
                {' '}on the Setup page.
              </li>
            )}
            {ticketCount === 0 && projectPathReady && modelsConfigured && (
              <li>Use <span className="font-medium text-foreground">Add work → New requirement</span> to create your first tickets.</li>
            )}
          </ol>
        </div>
      )}
    </div>
  );
});
