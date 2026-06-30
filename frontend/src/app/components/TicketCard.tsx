import { useRef } from 'react';
import { useDrag } from 'react-dnd';
import {
  Loader2, CheckCircle2, XCircle, AlertTriangle, RefreshCw,
  ArrowRight, Zap, CheckCheck,
} from 'lucide-react';
import { getModelMeta, MANUAL_STATUS_TRANSITIONS, TYPE_CLASSES } from '../constants';
import { STATE_COPY } from '../copy';
import { workerDisplayNumber } from '../throughputUtils';
import { InterventionNotice, interventionNoticeForTicket } from './InterventionNotice';
import { PrLinkBadge } from './PrLinkBadge';
import { DiffScopeBadge } from './DiffScopeBadge';
import { SplitLineageMarker } from './SplitLineageMarker';
import type { Ticket } from '../types';

const PRIORITY_DOT: Record<string, string> = {
  high: 'bg-red-500',
  medium: 'bg-amber-400',
  low: 'bg-zinc-300 dark:bg-zinc-600',
};

interface Props {
  ticket: Ticket;
  isSelected: boolean;
  onClick: () => void;
  onApprove: (id: string) => void;
  onAccept: (id: string) => void;
  onOpenTicket?: (id: string) => void;
}

export function TicketCard({ ticket, isSelected, onClick, onApprove, onAccept, onOpenTicket }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  // Only cards whose status has an allowed manual transition can be dragged.
  // Showing a grab cursor on cards that can't move is a false affordance.
  const canDrag = (MANUAL_STATUS_TRANSITIONS[ticket.status]?.length ?? 0) > 0;

  const [{ isDragging }, drag] = useDrag({
    type: 'TICKET',
    item: { id: ticket.id, status: ticket.status },
    canDrag,
    collect: (monitor) => ({ isDragging: monitor.isDragging() }),
  });

  drag(ref);

  const model = getModelMeta(ticket.assignedModel);
  const isDone = ticket.status === 'Done';
  const isAbandoned = ticket.status === 'Abandoned';
  const isSplit = ticket.status === 'Split';
  const isTerminal = isDone || isAbandoned || isSplit;
  const isBlocked = ticket.status === 'Blocked';
  const isInProgress = ticket.status === 'In Progress';
  const isReady = ticket.status === 'Ready';
  const isAwaitingAcceptance = ticket.status === 'Awaiting acceptance';
  const isDiffReview = ticket.status === 'Diff review';
  const needsApproval = ticket.needsApproval && ticket.status === 'Backlog';
  const intervention = interventionNoticeForTicket(ticket);

  let leftBorderColor = 'transparent';
  if (isInProgress) leftBorderColor = model.accent;
  if (isBlocked) leftBorderColor = '#EF4444';
  if (isReady) leftBorderColor = '#16a34a';
  if (isAwaitingAcceptance) leftBorderColor = '#ea580c';
  if (isDiffReview) leftBorderColor = '#0891b2';
  if (needsApproval) leftBorderColor = '#d97706';

  return (
    <div
      ref={ref}
      data-testid={`ticket-card-${ticket.id}`}
      onClick={onClick}
      style={{
        opacity: isDragging ? 0.4 : 1,
        borderLeft: `2px solid ${leftBorderColor}`,
        cursor: canDrag ? (isDragging ? 'grabbing' : 'grab') : 'pointer',
      }}
      className={`
        group relative bg-card border border-border rounded-lg px-3 py-2.5 select-none
        transition-shadow hover:shadow-sm
        ${isTerminal ? 'opacity-60' : ''}
        ${isBlocked ? 'border-red-200 dark:border-red-900' : ''}
        ${isSelected ? 'ring-1 ring-primary' : ''}
      `}
    >
      {/* Top row: id + type + priority */}
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full shrink-0 ${PRIORITY_DOT[ticket.priority]}`}
            title={`${ticket.priority} priority`}
          />
          <span data-testid={`ticket-open-${ticket.id}`} className="text-xs text-muted-foreground font-mono">{ticket.id}</span>
          {ticket.projectName && (
            <span className="text-[10px] px-1 py-0 rounded border border-border bg-muted text-muted-foreground">
              {ticket.projectName}
            </span>
          )}
          <span className={`text-[10px] px-1 py-0 rounded uppercase tracking-wide font-medium ${TYPE_CLASSES[ticket.type]}`}>
            {ticket.type}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {isBlocked && <AlertTriangle size={12} className="text-red-500" />}
          {ticket.autoDispatched && !isBlocked && !isDone && (
            <span className="text-[10px] text-muted-foreground/60 font-mono" title="Started automatically by HAAO">auto</span>
          )}
        </div>
      </div>

      {/* Title */}
      <p className={`text-sm leading-snug mb-2 ${isTerminal ? 'line-through text-muted-foreground' : 'text-foreground'}`}>
        {ticket.title}
        {ticket.isNew && (
          <span className="ml-1.5 text-[10px] px-1 py-0 rounded bg-emerald-50 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800 font-medium normal-case no-underline">
            needs review
          </span>
        )}
      </p>

      {ticket.splitFrom && (
        <div className="mb-2" onClick={(e) => e.stopPropagation()}>
          <SplitLineageMarker
            parentId={ticket.splitFrom}
            onClick={onOpenTicket ? () => onOpenTicket(ticket.splitFrom!) : undefined}
          />
        </div>
      )}

      {isAbandoned && ticket.abandonReason && (
        <div className="mb-2 text-[11px] text-muted-foreground">
          Abandoned{ticket.abandonedAt ? ` · ${new Date(ticket.abandonedAt).toLocaleString()}` : ''}: {ticket.abandonReason}
        </div>
      )}

      {isSplit && ticket.childTicketIds && ticket.childTicketIds.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1" onClick={(e) => e.stopPropagation()}>
          {ticket.childTicketIds.map((childId) => (
            <button
              key={childId}
              type="button"
              onClick={() => onOpenTicket?.(childId)}
              className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-violet-200 dark:border-violet-800 text-violet-700 dark:text-violet-300 hover:bg-violet-50 dark:hover:bg-violet-950"
            >
              → {childId}
            </button>
          ))}
        </div>
      )}

      {ticket.diffStats && (
        <div className="mb-2">
          <DiffScopeBadge stats={ticket.diffStats} compact />
        </div>
      )}

      {ticket.lease && (isInProgress || ticket.testStatus === 'testing') && (
        <div className="mb-2 text-[11px] text-blue-700 dark:text-blue-300 font-mono">
          Running on worker {workerDisplayNumber(ticket.lease.workerId)}
        </div>
      )}

      {ticket.conflictNote && (
        <div className="mb-2 flex items-start gap-1 text-[11px] text-amber-700 dark:text-amber-300">
          <AlertTriangle size={11} className="shrink-0 mt-0.5" />
          <span>{ticket.conflictNote}</span>
        </div>
      )}

      {/* Rejection feedback lives in the detail panel — keep the card compact
          with just a one-line hint that feedback exists. */}
      {ticket.rejectionFeedback && (
        <div className="mb-2 flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-400">
          <span className="shrink-0">↩</span>
          <span>Feedback to address · open to view</span>
        </div>
      )}

      {intervention && (
        <div className="mb-2" onClick={(e) => e.stopPropagation()}>
          <InterventionNotice notification={intervention} compact />
        </div>
      )}

      {isDiffReview && (
        <div className="mb-2 flex items-center justify-between gap-2 bg-cyan-50 dark:bg-cyan-950/30 border border-cyan-200 dark:border-cyan-800 rounded px-2 py-1.5">
          <span className="text-[11px] font-medium text-cyan-700 dark:text-cyan-300">
            {STATE_COPY.diff.heading}
          </span>
          <span className="text-[11px] text-cyan-600 dark:text-cyan-400 shrink-0">
            Open to review →
          </span>
        </div>
      )}

      {/* Gate 1: needs approval */}
      {needsApproval && (
        <div
          className="mb-2 flex items-center justify-between gap-2 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded px-2 py-1.5"
          onClick={(e) => e.stopPropagation()}
        >
          <span className="text-[11px] font-medium text-amber-700 dark:text-amber-400 flex items-center gap-1">
            <AlertTriangle size={10} /> {STATE_COPY.gate1.badge}
          </span>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onApprove(ticket.id); }}
            className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Approve <ArrowRight size={10} />
          </button>
        </div>
      )}

      {/* Gate 2: awaiting acceptance */}
      {isAwaitingAcceptance && (
        <div
          className="mb-2 space-y-1.5"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
            <CheckCheck size={11} />
            <span>Passed automated review</span>
          </div>
          <div className="flex items-center gap-1 flex-wrap">
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onAccept(ticket.id); }}
              className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded bg-emerald-600 text-white hover:bg-emerald-700 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <CheckCircle2 size={10} /> Accept
            </button>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onClick(); }}
              className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded border border-border text-muted-foreground hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Open to review →
            </button>
          </div>
        </div>
      )}

      {/* Ready state auto-dispatch note */}
      {isReady && (
        <div className="mb-2 flex items-center gap-1 text-[11px] text-green-600 dark:text-green-400">
          <Zap size={10} />
          <span>Approved · starting soon…</span>
        </div>
      )}

      {/* Bottom row: model pill + status indicators */}
      <div className="flex items-center justify-between gap-x-2 gap-y-1 flex-wrap">
        <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[11px] font-medium max-w-[150px] truncate ${model.pillClass}`}>
          {model.label}
        </span>

        <div className="flex items-center gap-2 ml-auto shrink-0">
          {ticket.prUrl && (
            <PrLinkBadge
              prUrl={ticket.prUrl}
              prStatus={ticket.prStatus}
              compact
              onClick={(event) => event.stopPropagation()}
            />
          )}
          {ticket.autoEscalated && (
            <span className="text-[10px] text-amber-600 dark:text-amber-400 font-mono" title="Auto-escalated by HAAO">
              ↑ escalated
            </span>
          )}
          {isDiffReview && (
            <span className="text-[10px] px-1 py-0 rounded font-medium bg-cyan-50 text-cyan-700 dark:bg-cyan-950 dark:text-cyan-300">
              review changes
            </span>
          )}
          {ticket.retryCount > 0 && ticket.status !== 'Done' && (
            <span className="flex items-center gap-0.5 text-[11px] text-muted-foreground">
              <RefreshCw size={10} />
              {ticket.retryCount}/{ticket.retryBudget}
            </span>
          )}
          {ticket.testStatus === 'testing' && (
            <span className="flex items-center gap-0.5 text-[11px] text-amber-600 dark:text-amber-400">
              <Loader2 size={11} className="animate-spin" /> running tests
            </span>
          )}
          {ticket.testStatus === 'pass' && !isAwaitingAcceptance && (
            <span className="flex items-center gap-0.5 text-[11px] text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 size={11} />
              {isDone ? '✓ accepted' : 'passed'}
            </span>
          )}
          {ticket.testStatus === 'fail' && (
            <span className="flex items-center gap-0.5 text-[11px] text-red-600 dark:text-red-400">
              <XCircle size={11} /> failed
            </span>
          )}
          {isAbandoned && (
            <span className="text-[10px] px-1 py-0 rounded font-medium bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
              abandoned
            </span>
          )}
          {isSplit && (
            <span className="text-[10px] px-1 py-0 rounded font-medium bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300">
              superseded
            </span>
          )}
          {isBlocked && (
            <span className={`text-[10px] px-1 py-0 rounded font-medium bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300`}>
              blocked
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
