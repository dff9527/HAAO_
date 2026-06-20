import { Sparkles, Plus, Cloud, ChevronRight } from 'lucide-react';
import { getModelMeta } from '../constants';
import type { RequirementSource, Ticket } from '../types';

interface Props {
  requirements: RequirementSource[];
  tickets: Ticket[];
  onOpenRequirement: (id: string) => void;
  onNewRequirement: () => void;
}

function formatCloudCost(usd?: number) {
  if (usd === undefined || usd <= 0) return null;
  return `$${usd.toFixed(4)}`;
}

export function RequirementsPage({ requirements, tickets, onOpenRequirement, onNewRequirement }: Props) {
  const ordered = [...requirements].reverse();
  const totalCost = requirements.reduce((sum, req) => sum + (req.cloudCostUsd ?? 0), 0);

  function ticketsForRequirement(reqId: string): Ticket[] {
    return tickets.filter((ticket) => ticket.requirementId === reqId);
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-5 py-6 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-base font-semibold flex items-center gap-2">
              <Sparkles size={16} className="text-amber-500" />
              Requirements
            </h1>
            <p className="text-xs text-muted-foreground mt-1">
              Every requirement decomposed by the Tech Lead, with the tickets it produced and the cloud API spend it incurred.
            </p>
          </div>
          <button
            type="button"
            onClick={onNewRequirement}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-border font-medium text-foreground hover:bg-muted transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Plus size={12} /> New requirement
          </button>
        </div>

        {requirements.length > 0 && (
          <div className="flex flex-wrap items-center gap-4 rounded-xl border border-border bg-muted/30 px-4 py-3 text-xs">
            <span className="text-muted-foreground">
              <span className="font-semibold text-foreground tabular-nums">{requirements.length}</span> requirement
              {requirements.length !== 1 ? 's' : ''}
            </span>
            <span className="text-muted-foreground/40">·</span>
            <span className="inline-flex items-center gap-1.5 text-muted-foreground">
              <Cloud size={12} />
              <span className="font-semibold text-foreground tabular-nums">
                {formatCloudCost(totalCost) ?? '$0.0000'}
              </span>
              total cloud API spend
            </span>
          </div>
        )}

        {requirements.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border px-4 py-10 text-center">
            <p className="text-sm text-foreground font-medium">No requirements yet</p>
            <p className="text-xs text-muted-foreground mt-1">
              Use <span className="font-medium text-foreground">New requirement</span> to describe a feature — Tech Lead decomposes it into tickets.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {ordered.map((req) => {
              const reqTickets = ticketsForRequirement(req.id);
              const cost = formatCloudCost(req.cloudCostUsd);
              return (
                <button
                  type="button"
                  key={req.id}
                  onClick={() => onOpenRequirement(req.id)}
                  className="w-full text-left rounded-xl border border-border bg-card hover:bg-muted/40 transition-colors px-4 py-3 group"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold text-foreground">{req.id}</span>
                    <span className="font-mono text-[11px] text-muted-foreground">{req.repo}/{req.branch}</span>
                    <span className="text-muted-foreground/40">·</span>
                    <span className="text-[11px] text-muted-foreground">{req.createdAt}</span>
                    <ChevronRight size={14} className="ml-auto text-muted-foreground/50 group-hover:text-foreground transition-colors" />
                  </div>
                  <p className="text-sm text-foreground mt-1.5 line-clamp-2 leading-snug">{req.prompt}</p>
                  <div className="flex flex-wrap items-center gap-2 mt-2">
                    <span className="text-[11px] text-muted-foreground tabular-nums">
                      {reqTickets.length} ticket{reqTickets.length !== 1 ? 's' : ''}
                    </span>
                    {reqTickets.slice(0, 4).map((ticket) => (
                      <span
                        key={ticket.id}
                        className={`text-[10px] px-1.5 py-0 rounded-full font-medium ${getModelMeta(ticket.assignedModel).pillClass}`}
                      >
                        {ticket.id}
                      </span>
                    ))}
                    {reqTickets.length > 4 && (
                      <span className="text-[10px] text-muted-foreground">+{reqTickets.length - 4}</span>
                    )}
                    {cost && (
                      <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                        <Cloud size={11} /> {cost}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
