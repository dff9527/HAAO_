import type { InboxNotification, InboxNotificationKind } from './api/types';

export type InboxKindFilter = 'all' | InboxNotificationKind;

export const INBOX_KIND_LABELS: Record<InboxNotificationKind, string> = {
  needs_you: 'Needs you',
  done: 'Done',
  blocked: 'Blocked',
};

export const INBOX_KIND_STYLES: Record<InboxNotificationKind, string> = {
  needs_you: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300 border-amber-200 dark:border-amber-800',
  done: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800',
  blocked: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300 border-red-200 dark:border-red-800',
};

export function isNotificationUnread(notification: InboxNotification): boolean {
  return notification.unread ?? !notification.read_at;
}

export function formatInboxTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const now = Date.now();
  const diffMs = now - date.getTime();
  if (diffMs < 60_000) return 'just now';
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}m ago`;
  if (diffMs < 86_400_000) return `${Math.floor(diffMs / 3_600_000)}h ago`;
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export function filterNotificationsByKind(
  notifications: InboxNotification[],
  kind: InboxKindFilter,
): InboxNotification[] {
  if (kind === 'all') return notifications;
  return notifications.filter((item) => item.kind === kind);
}

export const MOCK_INBOX_NOTIFICATIONS: InboxNotification[] = [
  {
    id: 1,
    project_id: 'default',
    ticket_id: 'T-001',
    kind: 'needs_you',
    title: 'T-001 needs you — diff review required',
    created_at: new Date(Date.now() - 12 * 60_000).toISOString(),
    unread: true,
  },
  {
    id: 2,
    project_id: 'default',
    ticket_id: 'T-002',
    kind: 'blocked',
    title: 'T-002 blocked — retry budget exhausted',
    created_at: new Date(Date.now() - 2 * 3_600_000).toISOString(),
    unread: true,
  },
  {
    id: 3,
    project_id: 'default',
    ticket_id: 'T-003',
    kind: 'done',
    title: 'T-003 done — accepted by Product Owner',
    created_at: new Date(Date.now() - 26 * 3_600_000).toISOString(),
    read_at: new Date(Date.now() - 20 * 3_600_000).toISOString(),
    unread: false,
  },
];

export const MOCK_INBOX_UNREAD_COUNT = {
  total: 2,
  by_project: { default: 2 },
};
