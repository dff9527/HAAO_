import { Bell } from 'lucide-react';
import type { InterventionNotification } from '../types';

interface Props {
  notification?: InterventionNotification;
  compact?: boolean;
}

export function InterventionNotice({ notification, compact = false }: Props) {
  if (!notification?.reason) return null;

  return (
    <div
      className={`flex items-start gap-1.5 rounded border border-violet-200 dark:border-violet-800 bg-violet-50/80 dark:bg-violet-950/40 text-violet-800 dark:text-violet-200 ${
        compact ? 'px-2 py-1 text-[11px]' : 'px-3 py-2 text-xs'
      }`}
    >
      <Bell size={compact ? 10 : 12} className="shrink-0 mt-0.5" />
      <div className="min-w-0">
        <p className="font-medium leading-snug">Needs your attention</p>
        <p className="text-violet-700/90 dark:text-violet-300/90 leading-snug">{notification.reason}</p>
        {!compact && notification.sentAt && (
          <p className="text-[10px] text-violet-600/70 dark:text-violet-400/70 mt-1 font-mono">
            {new Date(notification.sentAt).toLocaleString()}
          </p>
        )}
      </div>
    </div>
  );
}

function shouldShowIntervention(status: string) {
  return status === 'Diff review' || status === 'Blocked' || status === 'Awaiting acceptance';
}

export function interventionNoticeForTicket(ticket: {
  status: string;
  lastInterventionNotification?: InterventionNotification;
}) {
  if (!shouldShowIntervention(ticket.status)) return undefined;
  return ticket.lastInterventionNotification;
}
