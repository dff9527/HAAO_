import { useCallback, useEffect, useMemo, useState, type MouseEvent } from 'react';
import { CheckCheck, Inbox, Loader2 } from 'lucide-react';
import { apiClient } from '../api/client';
import type { InboxNotification, InboxUnreadCount } from '../api/types';
import {
  INBOX_KIND_LABELS,
  INBOX_KIND_STYLES,
  MOCK_INBOX_NOTIFICATIONS,
  MOCK_INBOX_UNREAD_COUNT,
  type InboxKindFilter,
  filterNotificationsByKind,
  formatInboxTime,
  isNotificationUnread,
} from '../inboxUtils';

type ProjectScope = 'project' | 'all';

const KIND_FILTERS: Array<{ id: InboxKindFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'needs_you', label: 'Needs you' },
  { id: 'done', label: 'Done' },
  { id: 'blocked', label: 'Blocked' },
];

interface Props {
  projectId: string;
  projectNameById: Record<string, string>;
  usingMockData: boolean;
  onUnreadCountChange?: (count: InboxUnreadCount) => void;
  onOpenTicket: (ticketId: string, notificationProjectId: string) => void;
  onOpenRequirement: (requirementId: string, notificationProjectId: string) => void;
}

export function InboxPage({
  projectId,
  projectNameById,
  usingMockData,
  onUnreadCountChange,
  onOpenTicket,
  onOpenRequirement,
}: Props) {
  const [scope, setScope] = useState<ProjectScope>('project');
  const [kindFilter, setKindFilter] = useState<InboxKindFilter>('all');
  const [notifications, setNotifications] = useState<InboxNotification[]>(
    usingMockData ? MOCK_INBOX_NOTIFICATIONS : [],
  );
  const [unreadCount, setUnreadCount] = useState<InboxUnreadCount>(
    usingMockData ? MOCK_INBOX_UNREAD_COUNT : { total: 0, by_project: {} },
  );
  const [loading, setLoading] = useState(!usingMockData);
  const [markingAll, setMarkingAll] = useState(false);
  const [markingId, setMarkingId] = useState<number | null>(null);

  const listProjectId = scope === 'project' ? projectId : undefined;

  const loadInbox = useCallback(async () => {
    if (usingMockData) {
      const items = scope === 'project'
        ? MOCK_INBOX_NOTIFICATIONS.filter((item) => item.project_id === projectId)
        : MOCK_INBOX_NOTIFICATIONS;
      setNotifications(items);
      setUnreadCount(MOCK_INBOX_UNREAD_COUNT);
      onUnreadCountChange?.(MOCK_INBOX_UNREAD_COUNT);
      setLoading(false);
      return;
    }
    try {
      const data = await apiClient.listNotifications({ projectId: listProjectId, limit: 200 });
      setNotifications(data.notifications);
      setUnreadCount(data.unread_count);
      onUnreadCountChange?.(data.unread_count);
    } catch {
      setNotifications(MOCK_INBOX_NOTIFICATIONS);
      setUnreadCount(MOCK_INBOX_UNREAD_COUNT);
      onUnreadCountChange?.(MOCK_INBOX_UNREAD_COUNT);
    } finally {
      setLoading(false);
    }
  }, [listProjectId, onUnreadCountChange, projectId, scope, usingMockData]);

  useEffect(() => {
    setLoading(true);
    void loadInbox();
  }, [loadInbox]);

  useEffect(() => {
    if (usingMockData) return;
    const intervalId = window.setInterval(() => {
      void loadInbox();
    }, 5000);
    return () => window.clearInterval(intervalId);
  }, [loadInbox, usingMockData]);

  const filtered = useMemo(
    () =>
      filterNotificationsByKind(notifications, kindFilter).sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [kindFilter, notifications],
  );

  const scopeUnread = scope === 'project'
    ? (unreadCount.by_project[projectId] ?? 0)
    : unreadCount.total;

  async function handleMarkRead(notification: InboxNotification, event?: MouseEvent) {
    event?.stopPropagation();
    if (!isNotificationUnread(notification)) return;
    if (usingMockData) {
      setNotifications((prev) =>
        prev.map((item) =>
          item.id === notification.id
            ? { ...item, read_at: new Date().toISOString(), unread: false }
            : item,
        ),
      );
      const next = {
        total: Math.max(0, unreadCount.total - 1),
        by_project: {
          ...unreadCount.by_project,
          [notification.project_id]: Math.max(0, (unreadCount.by_project[notification.project_id] ?? 0) - 1),
        },
      };
      setUnreadCount(next);
      onUnreadCountChange?.(next);
      return;
    }
    setMarkingId(notification.id);
    try {
      const result = await apiClient.markNotificationRead(notification.id);
      setNotifications((prev) =>
        prev.map((item) => (item.id === notification.id ? result.notification : item)),
      );
      setUnreadCount(result.unread_count);
      onUnreadCountChange?.(result.unread_count);
    } finally {
      setMarkingId(null);
    }
  }

  async function handleMarkAllRead() {
    setMarkingAll(true);
    try {
      if (usingMockData) {
        const clearedIds = new Set(
          notifications.filter((item) => isNotificationUnread(item)).map((item) => item.id),
        );
        setNotifications((prev) =>
          prev.map((item) =>
            clearedIds.has(item.id)
              ? { ...item, read_at: new Date().toISOString(), unread: false }
              : item,
          ),
        );
        const next = scope === 'project'
          ? {
              total: Math.max(0, unreadCount.total - (unreadCount.by_project[projectId] ?? 0)),
              by_project: { ...unreadCount.by_project, [projectId]: 0 },
            }
          : { total: 0, by_project: {} };
        setUnreadCount(next);
        onUnreadCountChange?.(next);
        return;
      }
      const result = await apiClient.markAllNotificationsRead(listProjectId);
      await loadInbox();
      setUnreadCount(result.unread_count);
      onUnreadCountChange?.(result.unread_count);
    } finally {
      setMarkingAll(false);
    }
  }

  async function handleOpen(notification: InboxNotification) {
    if (isNotificationUnread(notification)) {
      await handleMarkRead(notification);
    }
    if (notification.ticket_id) {
      onOpenTicket(notification.ticket_id, notification.project_id);
      return;
    }
    if (notification.requirement_id) {
      onOpenRequirement(notification.requirement_id, notification.project_id);
    }
  }

  return (
    <div className="flex-1 overflow-y-auto bg-background">
      <div className="mx-auto max-w-2xl px-4 sm:px-6 py-8 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-base font-semibold flex items-center gap-2">
              <Inbox size={16} className="text-sky-600 dark:text-sky-400" />
              Inbox
            </h1>
            <p className="text-xs text-muted-foreground mt-1">
              Needs-you, done, and blocked items across your projects.
            </p>
          </div>
          <div className="flex items-center gap-1 rounded-lg border border-border bg-muted/40 p-0.5">
            <button
              type="button"
              onClick={() => setScope('project')}
              className={`text-xs px-2.5 py-1 rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                scope === 'project'
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              This project
            </button>
            <button
              type="button"
              onClick={() => setScope('all')}
              className={`text-xs px-2.5 py-1 rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                scope === 'all'
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              All projects
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1">
            {KIND_FILTERS.map((filter) => (
              <button
                key={filter.id}
                type="button"
                onClick={() => setKindFilter(filter.id)}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  kindFilter === filter.id
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border text-muted-foreground hover:bg-muted'
                }`}
              >
                {filter.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => void handleMarkAllRead()}
            disabled={markingAll || scopeUnread === 0}
            className="flex items-center gap-1 text-xs px-2.5 py-1 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {markingAll ? <Loader2 size={11} className="animate-spin" /> : <CheckCheck size={11} />}
            Mark all read
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            Loading inbox…
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border px-4 py-12 text-center">
            <p className="text-sm font-medium text-foreground">Inbox clear</p>
            <p className="text-xs text-muted-foreground mt-1">
              {kindFilter === 'all'
                ? 'No notifications in this view.'
                : `No ${INBOX_KIND_LABELS[kindFilter].toLowerCase()} items.`}
            </p>
          </div>
        ) : (
          <ul className="space-y-2">
            {filtered.map((notification) => {
              const unread = isNotificationUnread(notification);
              return (
                <li key={notification.id}>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => void handleOpen(notification)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        void handleOpen(notification);
                      }
                    }}
                    className={`w-full text-left rounded-xl border px-4 py-3 transition-colors hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                      unread ? 'border-border bg-card' : 'border-border/70 bg-muted/20'
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      {unread && (
                        <span className="w-1.5 h-1.5 rounded-full bg-primary shrink-0 mt-1.5" aria-hidden="true" />
                      )}
                      <div className={`flex-1 min-w-0 ${unread ? '' : 'pl-3.5'}`}>
                        <div className="flex flex-wrap items-center gap-2 mb-1">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${INBOX_KIND_STYLES[notification.kind]}`}>
                            {INBOX_KIND_LABELS[notification.kind]}
                          </span>
                          <span className="text-[10px] text-muted-foreground font-mono">
                            {projectNameById[notification.project_id] ?? notification.project_id}
                          </span>
                          {notification.ticket_id && (
                            <span className="text-[10px] font-mono text-muted-foreground">{notification.ticket_id}</span>
                          )}
                          {notification.requirement_id && (
                            <span className="text-[10px] font-mono text-muted-foreground">{notification.requirement_id}</span>
                          )}
                          <span className="text-[10px] text-muted-foreground ml-auto shrink-0">
                            {formatInboxTime(notification.created_at)}
                          </span>
                        </div>
                        <p className={`text-sm leading-snug ${unread ? 'font-semibold text-foreground' : 'text-muted-foreground'}`}>
                          {notification.title}
                        </p>
                      </div>
                      {unread && (
                        <button
                          type="button"
                          onClick={(event) => void handleMarkRead(notification, event)}
                          disabled={markingId === notification.id}
                          className="text-[11px] px-2 py-1 rounded border border-border text-muted-foreground hover:bg-muted hover:text-foreground shrink-0 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {markingId === notification.id ? <Loader2 size={11} className="animate-spin" /> : 'Mark read'}
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
