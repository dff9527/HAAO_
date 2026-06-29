import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronDown, Loader2 } from 'lucide-react';
import { apiClient } from '../api/client';
import type { AuditEvent, IdentityContext } from '../api/types';
import {
  MOCK_AUDIT_EVENTS,
  formatAuditTimestamp,
  memberDisplayName,
  mockTeamPlaneEnabled,
} from '../teamPlaneUtils';

interface Props {
  identityContext: IdentityContext;
}

export function AuditLogPanel({ identityContext }: Props) {
  const workspaceId = identityContext.workspace_id;
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [actionFilter, setActionFilter] = useState('all');

  const loadPage = useCallback(async (cursor?: number, append = false) => {
    if (mockTeamPlaneEnabled()) {
      const filtered = actionFilter === 'all'
        ? MOCK_AUDIT_EVENTS
        : MOCK_AUDIT_EVENTS.filter((event) => event.action.includes(actionFilter));
      setEvents(filtered);
      setNextCursor(null);
      return;
    }
    const result = await apiClient.getAuditEvents({
      workspace: workspaceId,
      cursor,
      limit: 50,
    });
    let page = result.events;
    if (actionFilter !== 'all') {
      page = page.filter((event) => event.action.includes(actionFilter));
    }
    setEvents((prev) => (append ? [...prev, ...page] : page));
    setNextCursor(result.next_cursor);
  }, [actionFilter, workspaceId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    loadPage()
      .catch(() => {
        if (active && mockTeamPlaneEnabled()) {
          setEvents(MOCK_AUDIT_EVENTS);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadPage]);

  const actionOptions = useMemo(() => {
    const actions = new Set(events.map((event) => event.action));
    for (const event of MOCK_AUDIT_EVENTS) {
      actions.add(event.action);
    }
    return ['all', ...Array.from(actions).sort()];
  }, [events]);

  async function loadMore() {
    if (nextCursor == null || loadingMore) return;
    setLoadingMore(true);
    try {
      await loadPage(nextCursor, true);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-muted-foreground">
        Append-only audit trail for privileged actions in this workspace.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-[11px] text-muted-foreground" htmlFor="audit-action-filter">
          Filter by action
        </label>
        <select
          id="audit-action-filter"
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value)}
          className="text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
        >
          {actionOptions.map((action) => (
            <option key={action} value={action}>
              {action === 'all' ? 'All actions' : action}
            </option>
          ))}
        </select>
      </div>
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
          <Loader2 size={12} className="animate-spin" />
          Loading audit log…
        </div>
      ) : events.length === 0 ? (
        <p className="text-[11px] text-muted-foreground py-2">No audit events yet.</p>
      ) : (
        <div className="rounded border border-border overflow-x-auto">
          <table className="w-full text-sm min-w-[560px]">
            <thead>
              <tr className="bg-muted/60 border-b border-border">
                <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2">Actor</th>
                <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2">Action</th>
                <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2">Target</th>
                <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2">Time</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event, index) => (
                <tr
                  key={event.id}
                  className={`border-b border-border last:border-0 ${index % 2 === 0 ? '' : 'bg-muted/20'}`}
                >
                  <td className="px-3 py-2.5 align-top">
                    <div className="text-xs text-foreground">{memberDisplayName({ user_id: event.actor_id })}</div>
                    <div className="font-mono text-[10px] text-muted-foreground">{event.actor_id}</div>
                    {event.ip && (
                      <div className="text-[10px] text-muted-foreground">{event.ip}</div>
                    )}
                  </td>
                  <td className="px-3 py-2.5 align-top font-mono text-[11px] text-foreground">{event.action}</td>
                  <td className="px-3 py-2.5 align-top font-mono text-[11px] text-muted-foreground break-all max-w-[200px]">
                    {event.target}
                  </td>
                  <td className="px-3 py-2.5 align-top text-[11px] text-muted-foreground whitespace-nowrap">
                    {formatAuditTimestamp(event.ts)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {nextCursor != null && !mockTeamPlaneEnabled() && (
        <button
          type="button"
          onClick={() => void loadMore()}
          disabled={loadingMore}
          className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
        >
          {loadingMore ? <Loader2 size={11} className="animate-spin" /> : <ChevronDown size={11} />}
          Load more
        </button>
      )}
    </div>
  );
}
