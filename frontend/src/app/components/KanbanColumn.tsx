import { useRef, useState } from 'react';
import { useDrop } from 'react-dnd';
import { TicketCard } from './TicketCard';
import { HelpTip } from './HelpTip';
import { canManuallyMoveTicket, MANUAL_DROP_HINT } from '../constants';
import { STATE_COPY } from '../copy';
import { HELP_TOOLTIPS } from '../dxUtils';
import type { Ticket, TicketStatus } from '../types';

interface Props {
  status: TicketStatus;
  tickets: Ticket[];
  loading?: boolean;
  selectedTicketId: string | null;
  onDropTicket: (ticketId: string, newStatus: TicketStatus) => void;
  onSelectTicket: (id: string) => void;
  onApproveTicket: (id: string) => void;
  onAcceptTicket: (id: string) => void;
}

const COLUMN_BORDER: Record<string, string> = {
  Backlog: 'border-zinc-200 dark:border-zinc-700',
  Ready: 'border-green-200 dark:border-green-900',
  'In Progress': 'border-blue-200 dark:border-blue-900',
  Review: 'border-violet-200 dark:border-violet-900',
  'Awaiting acceptance': 'border-orange-200 dark:border-orange-900',
  Done: 'border-emerald-200 dark:border-emerald-900',
};

const COLUMN_HEADER: Record<string, string> = {
  Backlog: 'text-zinc-600 dark:text-zinc-400',
  Ready: 'text-green-700 dark:text-green-300',
  'In Progress': 'text-blue-700 dark:text-blue-300',
  Review: 'text-violet-700 dark:text-violet-300',
  'Awaiting acceptance': 'text-orange-600 dark:text-orange-400',
  Done: 'text-emerald-700 dark:text-emerald-300',
};

const COUNT_STYLE: Record<string, string> = {
  Backlog: 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-500',
  Ready: 'bg-green-50 text-green-600 dark:bg-green-950 dark:text-green-400',
  'In Progress': 'bg-blue-50 text-blue-600 dark:bg-blue-950 dark:text-blue-400',
  Review: 'bg-violet-50 text-violet-600 dark:bg-violet-950 dark:text-violet-400',
  'Awaiting acceptance': 'bg-orange-50 text-orange-600 dark:bg-orange-950 dark:text-orange-400',
  Done: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400',
};

const COLUMN_LABEL: Record<string, string> = {
  Backlog: 'Backlog',
  Ready: 'Ready',
  'In Progress': 'In Progress',
  Review: 'Review',
  'Awaiting acceptance': 'Awaiting Acceptance',
  Done: 'Done',
};

function columnSublabel(columnStatus: TicketStatus, tickets: Ticket[]): string | undefined {
  if (columnStatus === 'Ready') return 'Approved · starts automatically';
  if (columnStatus === 'Awaiting acceptance') return STATE_COPY.gate2.columnSublabel;
  if (columnStatus === 'Backlog') {
    const needsApproval = tickets.some((t) => t.needsApproval && t.status === 'Backlog');
    if (needsApproval) return STATE_COPY.gate1.columnSublabel;
    return 'New and queued work';
  }
  if (columnStatus === 'In Progress') {
    const hasBlocked = tickets.some((t) => t.status === 'Blocked');
    const hasTesting = tickets.some((t) => t.testStatus === 'testing');
    if (hasBlocked && hasTesting) return 'Running, testing, or blocked';
    if (hasBlocked) return 'Includes blocked tickets';
    if (hasTesting) return 'Includes tickets running tests';
    return 'Agents working on tickets';
  }
  if (columnStatus === 'Review') {
    const hasDiff = tickets.some((t) => t.status === 'Diff review');
    if (hasDiff) return STATE_COPY.diff.columnSublabel;
    return 'Automated review in progress';
  }
  return undefined;
}

function emptyHint(columnStatus: TicketStatus): string {
  switch (columnStatus) {
    case 'Backlog':
      return 'Add a requirement or ticket to get started.';
    case 'Ready':
      return 'Approved tickets wait here before work starts.';
    case 'In Progress':
      return 'Nothing running yet.';
    case 'Review':
      return 'No tickets in review.';
    case 'Awaiting acceptance':
      return 'You decide when work is complete.';
    case 'Done':
      return 'Completed tickets appear here.';
    default:
      return 'Empty';
  }
}

function SkeletonCard() {
  return (
    <div className="rounded-lg border border-border bg-card p-3 space-y-2 animate-pulse">
      <div className="h-3 w-2/3 rounded bg-muted" />
      <div className="h-2 w-1/2 rounded bg-muted" />
      <div className="h-2 w-full rounded bg-muted" />
    </div>
  );
}

export function KanbanColumn({
  status, tickets, loading = false, selectedTicketId,
  onDropTicket, onSelectTicket,
  onApproveTicket, onAcceptTicket,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const sublabel = columnSublabel(status, tickets);

  // Done accumulates forever; show only the most recent and let the user expand.
  const DONE_CAP = 12;
  const [showAllDone, setShowAllDone] = useState(false);
  const isDoneColumn = status === 'Done';
  const hiddenDoneCount = isDoneColumn && !showAllDone ? Math.max(0, tickets.length - DONE_CAP) : 0;
  const visibleTickets = hiddenDoneCount > 0 ? tickets.slice(-DONE_CAP) : tickets;

  const [{ isOver, canDrop }, drop] = useDrop({
    accept: 'TICKET',
    canDrop: (item: { id: string; status: TicketStatus }) => canManuallyMoveTicket(item.status, status),
    drop: (item: { id: string; status: TicketStatus }) => {
      if (canManuallyMoveTicket(item.status, status)) onDropTicket(item.id, status);
    },
    collect: (monitor) => ({
      isOver: monitor.isOver(),
      canDrop: monitor.canDrop(),
    }),
  });

  drop(ref);

  return (
    <div
      ref={ref}
      className={`
        flex max-h-full min-h-[190px] flex-col min-w-[220px] w-[252px] shrink-0
        rounded-xl border ${COLUMN_BORDER[status]}
        transition-colors
        ${isOver && canDrop ? 'bg-muted/60' : 'bg-muted/20 dark:bg-zinc-900/40'}
        ${isOver && !canDrop ? 'opacity-60' : ''}
      `}
    >
      <div className="flex shrink-0 items-start justify-between px-3 py-2.5 border-b border-border">
        <div className="min-w-0 pr-2">
          <span className={`inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wider ${COLUMN_HEADER[status]}`}>
            {COLUMN_LABEL[status]}
            {status === 'Backlog' && (
              <HelpTip text={HELP_TOOLTIPS.backlog_proposal} label="Backlog proposals help" />
            )}
            {status === 'Ready' && (
              <HelpTip text={HELP_TOOLTIPS.ready_column} label="Ready column help" />
            )}
          </span>
          {sublabel && (
            <p className="text-[11px] text-muted-foreground/70 mt-0.5 font-normal normal-case tracking-normal leading-snug">
              {sublabel}
            </p>
          )}
        </div>
        <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium tabular-nums shrink-0 mt-0.5 ${COUNT_STYLE[status]}`}>
          {tickets.length}
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-2 space-y-2">
        {loading && (
          <>
            <SkeletonCard />
            <SkeletonCard />
          </>
        )}
        {!loading && hiddenDoneCount > 0 && (
          <button
            type="button"
            onClick={() => setShowAllDone(true)}
            className="w-full text-[11px] text-muted-foreground hover:text-foreground border border-dashed border-border rounded-lg py-1.5 transition-colors"
          >
            Show {hiddenDoneCount} older
          </button>
        )}
        {!loading && isDoneColumn && showAllDone && tickets.length > DONE_CAP && (
          <button
            type="button"
            onClick={() => setShowAllDone(false)}
            className="w-full text-[11px] text-muted-foreground hover:text-foreground border border-dashed border-border rounded-lg py-1.5 transition-colors"
          >
            Show fewer
          </button>
        )}
        {!loading && visibleTickets.map((ticket) => (
          <TicketCard
            key={ticket.id}
            ticket={ticket}
            isSelected={ticket.id === selectedTicketId}
            onClick={() => onSelectTicket(ticket.id)}
            onApprove={onApproveTicket}
            onAccept={onAcceptTicket}
          />
        ))}
        {!loading && tickets.length === 0 && (
          <div
            className={`
            min-h-16 flex items-center justify-center rounded-lg border border-dashed border-border
            text-xs text-muted-foreground text-center px-3 py-4 transition-colors
            ${isOver && canDrop ? 'border-primary/40 text-primary/60' : ''}
            ${isOver && !canDrop ? 'border-red-300 text-red-500 dark:border-red-800 dark:text-red-400' : ''}
          `}
            title={isOver && !canDrop ? MANUAL_DROP_HINT : undefined}
          >
            {isOver
              ? (canDrop ? 'Drop here' : `Can't drop here. ${MANUAL_DROP_HINT}`)
              : emptyHint(status)}
          </div>
        )}
      </div>
    </div>
  );
}
