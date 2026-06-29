import { useMemo } from 'react';
import { AlertTriangle, GitBranch, Loader2 } from 'lucide-react';
import type { TicketGraphPayload } from '../api/types';
import type { Ticket } from '../types';

interface Props {
  graph: TicketGraphPayload | null;
  tickets: Ticket[];
  loading?: boolean;
  onSelectTicket: (ticketId: string) => void;
}

function nodeTitle(ticketId: string, tickets: Ticket[]): string {
  return tickets.find((ticket) => ticket.id === ticketId)?.title ?? ticketId;
}

export function DependencyGraphPanel({ graph, tickets, loading = false, onSelectTicket }: Props) {
  const nodesById = useMemo(
    () => Object.fromEntries((graph?.nodes ?? []).map((node) => [node.id, node])),
    [graph?.nodes],
  );

  const ready = graph?.ready ?? [];
  const blocked = graph?.blocked ?? [];
  const edges = graph?.edges ?? [];

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-6 text-xs text-muted-foreground border-b border-border">
        <Loader2 size={14} className="animate-spin" />
        Loading dependency graph…
      </div>
    );
  }

  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="px-4 py-3 text-xs text-muted-foreground border-b border-border">
        No ticket dependencies yet for this project.
      </div>
    );
  }

  return (
    <div className="border-b border-border bg-muted/20 px-4 py-3 space-y-3">
      <div className="flex items-center gap-2">
        <GitBranch size={14} className="text-muted-foreground" />
        <h2 className="text-xs font-semibold text-foreground">Dependencies</h2>
        <span className="text-[11px] text-muted-foreground tabular-nums">
          {ready.length} ready · {blocked.length} blocked
        </span>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <section className="rounded-lg border border-emerald-200 dark:border-emerald-900 bg-emerald-50/40 dark:bg-emerald-950/20 p-2.5">
          <h3 className="text-[10px] uppercase tracking-wide text-emerald-700 dark:text-emerald-300 mb-1.5">Ready to run</h3>
          {ready.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">Nothing is ready right now.</p>
          ) : (
            <ul className="space-y-1">
              {ready.map((ticketId) => (
                <li key={ticketId}>
                  <button
                    type="button"
                    onClick={() => onSelectTicket(ticketId)}
                    className="text-left w-full text-[11px] px-2 py-1 rounded hover:bg-emerald-100/80 dark:hover:bg-emerald-900/40"
                  >
                    <span className="font-mono font-semibold">{ticketId}</span>
                    <span className="text-muted-foreground ml-1.5">{nodeTitle(ticketId, tickets)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-lg border border-amber-200 dark:border-amber-900 bg-amber-50/40 dark:bg-amber-950/20 p-2.5">
          <h3 className="text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-300 mb-1.5">Blocked</h3>
          {blocked.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">No blocked ready tickets.</p>
          ) : (
            <ul className="space-y-1">
              {blocked.map((ticketId) => {
                const node = nodesById[ticketId];
                const deps = node?.depends_on ?? [];
                const reason = node?.ready_state === 'conflict'
                  ? 'file overlap with a running ticket'
                  : deps.length > 0
                    ? `waiting on ${deps.join(', ')}`
                    : 'not ready';
                return (
                  <li key={ticketId}>
                    <button
                      type="button"
                      onClick={() => onSelectTicket(ticketId)}
                      className="text-left w-full text-[11px] px-2 py-1 rounded hover:bg-amber-100/80 dark:hover:bg-amber-900/40"
                    >
                      <span className="font-mono font-semibold">{ticketId}</span>
                      <span className="text-muted-foreground ml-1.5">{nodeTitle(ticketId, tickets)}</span>
                      <span className="block text-[10px] text-amber-700 dark:text-amber-300 mt-0.5">{reason}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      {edges.length > 0 && (
        <div className="rounded-lg border border-border bg-card p-2.5">
          <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1.5">Dependency edges</h3>
          <ul className="space-y-0.5 max-h-28 overflow-y-auto">
            {edges.map((edge) => (
              <li key={`${edge.source}-${edge.target}`} className="text-[11px] font-mono text-muted-foreground">
                {edge.source} → {edge.target}
              </li>
            ))}
          </ul>
        </div>
      )}

      {blocked.some((id) => nodesById[id]?.ready_state === 'conflict') && (
        <p className="flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-300">
          <AlertTriangle size={12} />
          Some tickets are blocked by file overlap — see ticket cards for details.
        </p>
      )}
    </div>
  );
}
