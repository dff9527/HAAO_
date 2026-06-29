import { useCallback, useEffect, useState } from 'react';
import { ClipboardCheck, Loader2 } from 'lucide-react';
import { apiClient } from '../api/client';
import type { DecisionCenterGroup, DecisionCenterItem } from '../api/types';
import { MOCK_DECISIONS } from '../trustUtils';
import { TicketSignalsBadges } from './TicketSignalsBadges';
import { SplitLineageMarker } from './SplitLineageMarker';

interface Props {
  projectId: string;
  usingMockData: boolean;
  onOpenTicket: (ticketId: string) => void;
  onOpenRequirement: (requirementId: string) => void;
  onApproveTicket: (ticketId: string) => void;
  onAcceptTicket: (ticketId: string) => void;
}

const GROUP_ORDER = ['gate1_scope', 'gate2_acceptance', 'blocked', 'high_risk'];

const PRIMARY_ACTION: Record<string, { label: string; action: string }> = {
  approve: { label: 'Approve', action: 'approve' },
  accept: { label: 'Accept', action: 'accept' },
  retry: { label: 'Retry', action: 'open' },
  review_scope: { label: 'Review', action: 'open' },
  approve_scope: { label: 'Review scope', action: 'requirement' },
};

export function DecisionCenterPage({
  projectId,
  usingMockData,
  onOpenTicket,
  onOpenRequirement,
  onApproveTicket,
  onAcceptTicket,
}: Props) {
  const [groups, setGroups] = useState<DecisionCenterGroup[]>(usingMockData ? MOCK_DECISIONS.groups : []);
  const [loading, setLoading] = useState(!usingMockData);

  const load = useCallback(async () => {
    if (usingMockData) {
      setGroups(MOCK_DECISIONS.groups);
      setLoading(false);
      return;
    }
    try {
      const data = await apiClient.getDecisions(projectId);
      setGroups(data.groups);
    } catch {
      setGroups(MOCK_DECISIONS.groups);
    } finally {
      setLoading(false);
    }
  }, [projectId, usingMockData]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load]);

  useEffect(() => {
    if (usingMockData) return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load, usingMockData]);

  function handlePrimary(item: DecisionCenterItem) {
    const action = item.actions?.[0];
    if (!action) {
      if (item.type === 'requirement') onOpenRequirement(item.id);
      else onOpenTicket(item.id);
      return;
    }
    if (action === 'approve') onApproveTicket(item.id);
    else if (action === 'accept') onAcceptTicket(item.id);
    else if (item.type === 'requirement') onOpenRequirement(item.id);
    else onOpenTicket(item.id);
  }

  const orderedGroups = GROUP_ORDER
    .map((id) => groups.find((group) => group.id === id))
    .filter((group): group is DecisionCenterGroup => Boolean(group));

  return (
    <div className="flex-1 overflow-y-auto bg-background">
      <div className="mx-auto max-w-3xl px-4 sm:px-6 py-8 space-y-5">
        <div>
          <h1 className="text-base font-semibold flex items-center gap-2">
            <ClipboardCheck size={16} className="text-indigo-600 dark:text-indigo-400" />
            PO Decision Center
          </h1>
          <p className="text-xs text-muted-foreground mt-1">
            Everything that needs you — approval, acceptance, blocked work, and high-risk items.
          </p>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            Loading decisions…
          </div>
        ) : (
          orderedGroups.map((group) => (
            <section key={group.id} className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{group.title}</h2>
                <span className="text-[11px] text-muted-foreground tabular-nums">{group.items.length}</span>
              </div>
              {group.items.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">
                  Nothing here right now.
                </div>
              ) : (
                group.items.map((item) => {
                  const primary = item.actions?.map((action) => PRIMARY_ACTION[action]).find(Boolean);
                  return (
                    <div
                      key={`${group.id}-${item.type}-${item.id}`}
                      className="rounded-xl border border-border bg-card px-4 py-3"
                    >
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-xs font-semibold">{item.id}</span>
                            <span className="text-[10px] text-muted-foreground capitalize">{item.status.replace(/_/g, ' ')}</span>
                          </div>
                          <p className="text-sm text-foreground mt-1">{item.title}</p>
                          {item.split_from && (
                            <div className="mt-1.5">
                              <SplitLineageMarker
                                parentId={item.split_from}
                                onClick={() => onOpenTicket(item.split_from!)}
                              />
                            </div>
                          )}
                          {item.child_ticket_ids && item.child_ticket_ids.length > 0 && (
                            <div className="mt-1.5 flex flex-wrap gap-1">
                              {item.child_ticket_ids.map((childId) => (
                                <button
                                  key={childId}
                                  type="button"
                                  onClick={() => onOpenTicket(childId)}
                                  className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-violet-200 dark:border-violet-800 text-violet-700 dark:text-violet-300 hover:bg-violet-50 dark:hover:bg-violet-950"
                                >
                                  → {childId}
                                </button>
                              ))}
                            </div>
                          )}
                          <TicketSignalsBadges signals={item.signals} compact />
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <button
                            type="button"
                            onClick={() => handlePrimary(item)}
                            className="text-xs px-2.5 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90"
                          >
                            {primary?.label ?? 'Open'}
                          </button>
                          <button
                            type="button"
                            onClick={() => (item.type === 'requirement' ? onOpenRequirement(item.id) : onOpenTicket(item.id))}
                            className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted"
                          >
                            Jump
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </section>
          ))
        )}
      </div>
    </div>
  );
}
