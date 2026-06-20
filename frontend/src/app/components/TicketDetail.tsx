import { useState, useEffect, useRef } from 'react';
import {
  X, FileCode, ClipboardList, Bot, RefreshCw,
  ChevronRight, AlertTriangle, CheckCircle2, Loader2, XCircle,
  ShieldCheck, UserCheck, Zap, Trash2, Square, Pencil, GitBranch, GitCommit, GitMerge, RotateCcw,
} from 'lucide-react';
import { DEFAULT_LOCAL_MODELS, getModelMeta, MANUAL_STATUS_TRANSITIONS, STATUS_CLASSES, TYPE_CLASSES } from '../constants';
import {
  CLAUDE_TECH_LEAD,
  MANUAL_MOVE_LABELS,
  STATE_COPY,
  canShowMergeAction,
  formatAuditVerdict,
  statusDisplayLabel,
} from '../copy';
import { DiffViewer } from './DiffViewer';
import { InterventionNotice, interventionNoticeForTicket } from './InterventionNotice';
import type { Ticket, TicketStatus, AssignedModel, RequirementSource } from '../types';

const LOG_LEVEL_CLASSES = {
  info: 'text-zinc-400',
  warn: 'text-amber-400',
  error: 'text-red-400',
  success: 'text-emerald-400',
};
const LOG_LEVEL_PREFIX = {
  info: 'INFO ',
  warn: 'WARN ',
  error: 'ERR  ',
  success: 'OK   ',
};

interface Props {
  ticket: Ticket;
  onClose: () => void;
  onUpdate: (updates: Partial<Ticket>) => void;
  onMove: (status: TicketStatus) => void;
  onRetry: () => void;
  onApprove: () => void;
  onAccept: () => void;
  onReject: (feedback: string) => void;
  onRun: () => void;
  onCancel: () => void;
  onApproveDiff: () => void;
  onRejectDiff: (feedback: string) => void;
  onMerge: () => void;
  onRevert: () => void;
  onUpdateAndRerun: (payload: {
    task_description?: string;
    dod_tests?: string[];
    assigned_model?: string;
  }) => void;
  onDelete: (force?: boolean) => void;
  onEscalate: () => void;
  requirementSource?: RequirementSource;
  onViewRequirement?: () => void;
  localModelIds?: string[];
  projectPathReady?: boolean;
}

type ConfirmAction = 'escalate' | 'backlog' | 'delete' | 'merge' | 'revert' | null;

export function TicketDetail({ ticket, onClose, onUpdate, onMove, onRetry, onApprove, onAccept, onReject, onRun, onCancel, onApproveDiff, onRejectDiff, onMerge, onRevert, onUpdateAndRerun, onDelete, onEscalate, requirementSource, onViewRequirement, localModelIds = DEFAULT_LOCAL_MODELS, projectPathReady = true }: Props) {
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [rejectFeedback, setRejectFeedback] = useState('');
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [showDiffRejectInput, setShowDiffRejectInput] = useState(false);
  const [diffRejectFeedback, setDiffRejectFeedback] = useState('');
  const [isEditing, setIsEditing] = useState(false);
  const [editDescription, setEditDescription] = useState(ticket.taskDescription ?? '');
  const [editTests, setEditTests] = useState(ticket.definitionOfDone.tests.join('\n'));
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [ticket.agentLog.length]);

  // Reset transient action UI only when switching to a different ticket.
  // Keying this on taskDescription/tests too would wipe an in-progress
  // Edit/Action whenever polling or the live-log WS delivers a fresh ticket
  // object (toUiTicket builds new array refs each time).
  useEffect(() => {
    setConfirmAction(null);
    setShowRejectInput(false);
    setShowDiffRejectInput(false);
    setRejectFeedback('');
    setDiffRejectFeedback('');
    setIsEditing(false);
  }, [ticket.id]);

  // Keep the edit draft in sync with incoming ticket data, but never clobber
  // the user's edits while they are actively editing.
  useEffect(() => {
    if (isEditing) return;
    setEditDescription(ticket.taskDescription ?? '');
    setEditTests(ticket.definitionOfDone.tests.join('\n'));
  }, [ticket.id, ticket.taskDescription, ticket.definitionOfDone.tests, isEditing]);

  function handleEscalate() {
    if (confirmAction === 'escalate') {
      onEscalate();
      setConfirmAction(null);
    } else {
      setConfirmAction('escalate');
    }
  }

  function handleMoveToBacklog() {
    if (confirmAction === 'backlog') {
      onMove('Backlog');
      setConfirmAction(null);
    } else {
      setConfirmAction('backlog');
    }
  }

  function handleDelete(force = false) {
    if (confirmAction === 'delete') {
      onDelete(force);
      setConfirmAction(null);
    } else {
      setConfirmAction('delete');
    }
  }

  function handleRejectSubmit() {
    if (rejectFeedback.trim()) {
      onReject(rejectFeedback.trim());
      setShowRejectInput(false);
      setRejectFeedback('');
    }
  }

  const isBlocked = ticket.status === 'Blocked';
  const isDiffReview = ticket.status === 'Diff review';
  const intervention = interventionNoticeForTicket(ticket);
  const model = getModelMeta(ticket.assignedModel);
  const isAwaitingAcceptance = ticket.status === 'Awaiting acceptance';
  const needsApproval = ticket.needsApproval && ticket.status === 'Backlog';
  const manualNextStatuses = needsApproval ? [] : MANUAL_STATUS_TRANSITIONS[ticket.status] ?? [];
  const canRun = projectPathReady && !needsApproval && ['Ready', 'In Progress'].includes(ticket.status);
  const canRetry = ticket.status === 'Blocked' || ticket.status === 'Backlog';
  const isRunning = ticket.status === 'In Progress' || ticket.testStatus === 'testing';
  const assignableModels = [...new Set([...localModelIds, ticket.assignedModel, CLAUDE_TECH_LEAD])];
  const showLiveLog = isRunning;

  useEffect(() => {
    function isTypingTarget(target: EventTarget | null): boolean {
      if (!(target instanceof HTMLElement)) return false;
      const tag = target.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Enter' || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      if (showRejectInput || showDiffRejectInput || isEditing || confirmAction) return;

      // Gate 1 approval and diff approval are reversible/low-risk, so Enter is a
      // convenience here. Gate 2 acceptance closes the ticket, so it is
      // intentionally excluded — it must be a deliberate click.
      if (needsApproval) {
        event.preventDefault();
        onApprove();
        return;
      }
      if (isDiffReview && ticket.pendingDiff) {
        event.preventDefault();
        onApproveDiff();
      }
    }

    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [
    confirmAction,
    isAwaitingAcceptance,
    isDiffReview,
    isEditing,
    needsApproval,
    onApprove,
    onApproveDiff,
    showDiffRejectInput,
    showRejectInput,
    ticket.pendingDiff,
  ]);

  function renderActivityLog(sticky = false) {
    return (
      <div
        className={`rounded-lg border border-border overflow-hidden ${
          sticky ? 'sticky top-0 z-10 shadow-md ring-1 ring-border/60' : ''
        }`}
      >
        <div className="flex items-center justify-between px-3 py-2 bg-zinc-900 border-b border-zinc-800">
          <span className="text-xs font-mono text-zinc-400 tracking-wide">
            Activity log{sticky ? ' · live' : ''}
          </span>
          <span className="text-[10px] text-zinc-600 font-mono">{ticket.agentLog.length} lines</span>
        </div>
        <div className={`bg-zinc-950 p-3 overflow-y-auto font-mono text-[11px] leading-relaxed space-y-0.5 ${
          sticky ? 'max-h-48' : 'max-h-56'
        }`}
        >
          {ticket.agentLog.length === 0 && (
            <p className="text-zinc-600">Waiting for agent output…</p>
          )}
          {ticket.agentLog.map((entry, i) => (
            <div key={i} className="flex gap-2">
              <span className="text-zinc-600 shrink-0">{entry.time}</span>
              <span className={`shrink-0 ${LOG_LEVEL_CLASSES[entry.level]}`}>{LOG_LEVEL_PREFIX[entry.level]}</span>
              <span className={LOG_LEVEL_CLASSES[entry.level]}>{entry.message}</span>
            </div>
          ))}
          <div ref={logEndRef} />
        </div>
      </div>
    );
  }

  function requestMerge() {
    setConfirmAction((current) => (current === 'merge' ? null : 'merge'));
  }

  function requestRevert() {
    setConfirmAction((current) => (current === 'revert' ? null : 'revert'));
  }

  function confirmMerge() {
    onMerge();
    setConfirmAction(null);
  }

  function confirmRevert() {
    onRevert();
    setConfirmAction(null);
  }

  return (
    <>
      <div className="fixed inset-0 bg-black/20 dark:bg-black/40 z-40 backdrop-blur-[1px]" onClick={onClose} aria-hidden="true" />

      <div
        className="fixed inset-y-0 right-0 w-full max-w-[580px] bg-card border-l border-border z-50 flex flex-col shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ticket-detail-title"
      >

        {/* Header */}
        <div className="flex items-center gap-2.5 px-4 py-3 border-b border-border shrink-0">
          <span className="font-mono text-xs text-muted-foreground">{ticket.id}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wide font-medium ${TYPE_CLASSES[ticket.type]}`}>
            {ticket.type}
          </span>
          <h2 id="ticket-detail-title" className="text-sm font-medium flex-1 truncate">{ticket.title}</h2>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_CLASSES[ticket.status]}`}>
            {statusDisplayLabel(ticket.status)}
          </span>
          {ticket.autoDispatched && (
            <span className="text-[10px] text-muted-foreground/60 font-mono flex items-center gap-0.5">
              <Zap size={9} /> auto
            </span>
          )}
          {requirementSource && (
            <button
              onClick={onViewRequirement}
              className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-950 text-indigo-600 dark:text-indigo-400 hover:bg-indigo-100 dark:hover:bg-indigo-900 transition-colors"
              title={`View requirement ${requirementSource.id}`}
            >
              {requirementSource.id}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="ml-1 h-6 w-6 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Close ticket details"
          >
            <X size={14} />
          </button>
        </div>

        {/* Scrollable content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">

          {intervention && <InterventionNotice notification={intervention} />}

          {!projectPathReady && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800">
              <AlertTriangle size={14} className="text-amber-500 mt-0.5 shrink-0" />
              <p className="text-xs text-amber-700 dark:text-amber-300">
                Set a repository path in project settings before starting or rerunning work on this ticket.
              </p>
            </div>
          )}

          {/* Blocked banner */}
          {isBlocked && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800">
              <AlertTriangle size={14} className="text-red-500 mt-0.5 shrink-0" />
              <p className="text-xs text-red-700 dark:text-red-300">
                <span className="font-semibold">Blocked — retry budget exhausted.</span>
                {' '}HAAO will auto-escalate to the Tech Lead, or you can move back to Backlog for re-scoping.
              </p>
            </div>
          )}

          {/* Gate 1 banner */}
          {needsApproval && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800">
              <AlertTriangle size={14} className="text-amber-500 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-amber-700 dark:text-amber-300">{STATE_COPY.gate1.heading}</p>
                <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">
                  {STATE_COPY.gate1.body}
                  <span className="hidden sm:inline text-amber-500/80"> Press Enter to approve.</span>
                </p>
                <button
                  type="button"
                  onClick={onApprove}
                  className="mt-2 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Approve for development <Zap size={11} />
                </button>
              </div>
            </div>
          )}

          {showLiveLog && renderActivityLog(true)}

          {/* Info tiles */}
          <div className="space-y-3">
            {/* Context files */}
            <div className="rounded-lg border border-border p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <FileCode size={12} className="text-muted-foreground" />
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Context files</span>
              </div>
              <div className="space-y-1.5">
                {ticket.contextFiles.length === 0 && <p className="text-xs text-muted-foreground">No files attached</p>}
                {ticket.contextFiles.map((f, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <span className="font-mono text-xs text-foreground bg-muted px-1.5 py-0.5 rounded shrink-0">{f.path}</span>
                    <span className="text-xs text-muted-foreground leading-5">{f.reason}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Definition of Done */}
            <div className="rounded-lg border border-border p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <ClipboardList size={12} className="text-muted-foreground" />
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Acceptance criteria</span>
              </div>
              {ticket.definitionOfDone.tests.length > 0 && (
                <div className="mb-2">
                  <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">Test commands</p>
                  {ticket.definitionOfDone.tests.map((cmd, i) => (
                    <div key={i} className="font-mono text-xs bg-muted px-2 py-1 rounded text-foreground mb-1">{cmd}</div>
                  ))}
                </div>
              )}
              {ticket.definitionOfDone.acceptanceCriteria.length > 0 && (
                <div>
                  <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">Acceptance criteria</p>
                  <ul className="space-y-0.5">
                    {ticket.definitionOfDone.acceptanceCriteria.map((ac, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-xs">
                        <ChevronRight size={10} className="text-muted-foreground mt-0.5 shrink-0" />
                        <span>{ac}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            {/* Assigned model */}
            <div className="rounded-lg border border-border p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <Bot size={12} className="text-muted-foreground" />
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Assigned model</span>
              </div>
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${model.pillClass}`}>
                  {model.label}
                </span>
                {ticket.autoEscalated && (
                  <span className="text-[10px] text-amber-600 dark:text-amber-400 font-mono">↑ auto-escalated by HAAO</span>
                )}
                <select
                  value={ticket.assignedModel}
                  onChange={(e) => onUpdate({ assignedModel: e.target.value as AssignedModel })}
                  className="ml-auto text-xs border border-border rounded px-2 py-1 bg-background text-foreground focus:outline-none focus:ring-1 focus:ring-ring max-w-[200px]"
                  aria-label="Assigned model"
                >
                  {assignableModels.map((m) => (
                    <option key={m} value={m}>{getModelMeta(m).label}</option>
                  ))}
                </select>
              </div>
              {ticket.retryCount > 0 && (
                <p className="text-xs text-muted-foreground mt-1.5 flex items-center gap-1">
                  Retry {ticket.retryCount}/{ticket.retryBudget}
                  {ticket.testStatus === 'testing' && <span className="inline-flex items-center gap-0.5 text-amber-600 dark:text-amber-400"><Loader2 size={10} className="animate-spin" /> running tests</span>}
                  {ticket.testStatus === 'pass' && <span className="inline-flex items-center gap-0.5 text-emerald-600 dark:text-emerald-400"><CheckCircle2 size={10} /> passed</span>}
                  {ticket.testStatus === 'fail' && <span className="inline-flex items-center gap-0.5 text-red-600 dark:text-red-400"><XCircle size={10} /> failed</span>}
                </p>
              )}
            </div>
          </div>

          {/* Diff review gate */}
          {isDiffReview && ticket.pendingDiff && (
            <div className="rounded-lg border border-cyan-200 dark:border-cyan-800 bg-cyan-50/60 dark:bg-cyan-950/30 p-3 space-y-3">
              <div className="flex items-center gap-1.5">
                <FileCode size={13} className="text-cyan-600 dark:text-cyan-400" />
                <span className="text-xs font-semibold text-cyan-700 dark:text-cyan-300 uppercase tracking-wide">
                  {STATE_COPY.diff.heading}
                </span>
              </div>
              <DiffViewer diff={ticket.pendingDiff} />
              {!showDiffRejectInput ? (
                <div className="flex gap-2">
                  <button
                    onClick={onApproveDiff}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
                  >
                    <CheckCircle2 size={12} /> Approve changes
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowDiffRejectInput(true)}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 transition-colors"
                  >
                    <XCircle size={12} /> Send back for changes
                  </button>
                </div>
              ) : (
                <div className="space-y-2">
                  <textarea
                    autoFocus
                    value={diffRejectFeedback}
                    onChange={(e) => setDiffRejectFeedback(e.target.value)}
                    placeholder="Why should the agent redo this change?"
                    rows={3}
                    className="w-full text-xs font-mono bg-background border border-border rounded px-2 py-1.5 resize-none focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => {
                        if (diffRejectFeedback.trim()) {
                          onRejectDiff(diffRejectFeedback.trim());
                          setShowDiffRejectInput(false);
                          setDiffRejectFeedback('');
                        }
                      }}
                      disabled={!diffRejectFeedback.trim()}
                      className="text-xs px-3 py-1.5 rounded bg-red-600 text-white hover:bg-red-700 transition-colors disabled:opacity-40"
                    >
                      Send back and rerun
                    </button>
                    <button
                      onClick={() => { setShowDiffRejectInput(false); setDiffRejectFeedback(''); }}
                      className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {ticket.diffRejectionFeedback && (
            <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
              Last diff rejection: {ticket.diffRejectionFeedback}
            </div>
          )}

          {ticket.rejectionFeedback && (
            <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
              <p className="font-semibold mb-0.5">Reviewer feedback</p>
              <p className="whitespace-pre-wrap">{ticket.rejectionFeedback}</p>
            </div>
          )}

          {(ticket.gitBranch || ticket.gitCommit) && (
            <div className="rounded-lg border border-border p-3 space-y-2">
              <div className="flex items-center gap-1.5">
                <GitBranch size={12} className="text-muted-foreground" />
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Git</span>
              </div>
              {ticket.gitBranch && (
                <div className="flex items-center gap-2 text-xs">
                  <GitBranch size={11} className="text-muted-foreground" />
                  <span className="font-mono bg-muted px-1.5 py-0.5 rounded">{ticket.gitBranch}</span>
                  {ticket.gitBaseBranch && <span className="text-muted-foreground">base {ticket.gitBaseBranch}</span>}
                </div>
              )}
              {ticket.gitCommit && (
                <div className="flex items-center gap-2 text-xs">
                  <GitCommit size={11} className="text-muted-foreground" />
                  <span className="font-mono bg-muted px-1.5 py-0.5 rounded">{ticket.gitCommit.slice(0, 12)}</span>
                </div>
              )}
              {ticket.gitMergeCommit ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400">
                    <GitMerge size={11} />
                    <span>Merged into {ticket.gitMergedTo ?? ticket.gitBaseBranch} at {ticket.gitMergeCommit.slice(0, 12)}</span>
                  </div>
                  {ticket.gitRevertCommit ? (
                    <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
                      <RotateCcw size={11} />
                      <span>Reverted at {ticket.gitRevertCommit.slice(0, 12)}</span>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={requestRevert}
                      className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border transition-colors ${
                        confirmAction === 'revert'
                          ? 'border-amber-400 bg-amber-100 text-amber-800 dark:border-amber-700 dark:bg-amber-900 dark:text-amber-200'
                          : 'border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300 hover:bg-amber-50 dark:hover:bg-amber-950'
                      }`}
                    >
                      <RotateCcw size={12} /> Undo merge
                    </button>
                  )}
                </div>
              ) : (
                ticket.gitBranch && canShowMergeAction(ticket.status) && (
                  <button
                    type="button"
                    onClick={requestMerge}
                    className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border transition-colors ${
                      confirmAction === 'merge'
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-border hover:bg-muted'
                    }`}
                  >
                    <GitMerge size={12} /> Merge branch
                  </button>
                )
              )}
              {confirmAction === 'merge' && ticket.gitBranch && !ticket.gitMergeCommit && (
                <div className="rounded border border-border bg-muted/40 p-2 text-xs space-y-2">
                  <p>Merge <span className="font-mono">{ticket.gitBranch}</span> into {ticket.gitBaseBranch ?? 'main'}?</p>
                  <div className="flex gap-2">
                    <button type="button" onClick={confirmMerge} className="px-2.5 py-1 rounded bg-primary text-primary-foreground text-xs">Confirm merge</button>
                    <button type="button" onClick={() => setConfirmAction(null)} className="px-2.5 py-1 rounded border border-border text-xs">Cancel</button>
                  </div>
                </div>
              )}
              {confirmAction === 'revert' && ticket.gitMergeCommit && !ticket.gitRevertCommit && (
                <div className="rounded border border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-950/30 p-2 text-xs space-y-2">
                  <p>Undo the merge on {ticket.gitMergedTo ?? ticket.gitBaseBranch}? This creates a revert commit.</p>
                  <div className="flex gap-2">
                    <button type="button" onClick={confirmRevert} className="px-2.5 py-1 rounded bg-amber-600 text-white text-xs">Confirm undo</button>
                    <button type="button" onClick={() => setConfirmAction(null)} className="px-2.5 py-1 rounded border border-border text-xs">Cancel</button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Edit task / DoD for rerun */}
          {isEditing ? (
            <div className="rounded-lg border border-border p-3 space-y-3">
              <div className="flex items-center gap-1.5">
                <Pencil size={12} className="text-muted-foreground" />
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Edit for rerun</span>
              </div>
              <div>
                <label className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1 block">Task description</label>
                <textarea
                  value={editDescription}
                  onChange={(e) => setEditDescription(e.target.value)}
                  rows={4}
                  className="w-full text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
              <div>
                <label className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1 block">Acceptance test commands (one per line)</label>
                <textarea
                  value={editTests}
                  onChange={(e) => setEditTests(e.target.value)}
                  rows={3}
                  className="w-full font-mono text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-ring"
                />
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    onUpdateAndRerun({
                      task_description: editDescription.trim(),
                      dod_tests: editTests.split('\n').map((line) => line.trim()).filter(Boolean),
                      assigned_model: ticket.assignedModel,
                    });
                    setIsEditing(false);
                  }}
                  disabled={!editDescription.trim() || !editTests.trim()}
                  className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-40"
                >
                  Save &amp; rerun
                </button>
                <button
                  onClick={() => setIsEditing(false)}
                  className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : null}

          {/* Technical Audit (automated system check — Claude TL) */}
          {ticket.technicalAudit && (
            <div className="rounded-lg border border-violet-200 dark:border-violet-800 bg-violet-50/60 dark:bg-violet-950/30 p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <ShieldCheck size={13} className="text-violet-600 dark:text-violet-400" />
                <span className="text-xs font-semibold text-violet-700 dark:text-violet-300 uppercase tracking-wide">
                  Technical audit — {ticket.technicalAudit.auditor}
                </span>
                <span className="ml-auto text-[10px] px-1.5 py-0 rounded bg-violet-100 dark:bg-violet-900 text-violet-600 dark:text-violet-300 border border-violet-200 dark:border-violet-700 font-medium">
                  automated
                </span>
              </div>
              <p className="text-xs text-violet-800 dark:text-violet-200 mb-2">{formatAuditVerdict(ticket.technicalAudit.verdict)}</p>
              {ticket.technicalAudit.feedback && (
                <p className="text-xs text-violet-800 dark:text-violet-200 mb-2 whitespace-pre-wrap">
                  {ticket.technicalAudit.feedback}
                </p>
              )}
              <div className="space-y-0.5">
                {ticket.technicalAudit.checkedCriteria.map((c, i) => (
                  <div key={i} className="flex items-start gap-1.5 text-xs text-violet-700 dark:text-violet-300">
                    <CheckCircle2 size={10} className="mt-0.5 shrink-0 text-violet-500" />
                    <span className="font-mono">{c}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Gate 2: Product Owner acceptance (human decision) ── */}
          {isAwaitingAcceptance && (
            <div className="rounded-lg border border-indigo-200 dark:border-indigo-800 bg-indigo-50/60 dark:bg-indigo-950/30 p-3">
              <div className="flex items-center gap-1.5 mb-2">
                <UserCheck size={13} className="text-indigo-600 dark:text-indigo-400" />
                <span className="text-xs font-semibold text-indigo-700 dark:text-indigo-300 uppercase tracking-wide">
                  {STATE_COPY.gate2.heading}
                </span>
              </div>
              <p className="text-xs text-indigo-700 dark:text-indigo-300 mb-3">
                {STATE_COPY.gate2.body}
              </p>
              {!showRejectInput ? (
                <div className="flex gap-2">
                  <button
                    onClick={onAccept}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
                  >
                    <CheckCircle2 size={12} /> Accept and close
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowRejectInput(true)}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 transition-colors"
                  >
                    <XCircle size={12} /> Send back for changes
                  </button>
                </div>
              ) : (
                <div className="space-y-2">
                  <textarea
                    autoFocus
                    value={rejectFeedback}
                    onChange={(e) => setRejectFeedback(e.target.value)}
                    placeholder="Describe what needs to change…"
                    rows={3}
                    className="w-full text-xs font-mono bg-background border border-border rounded px-2 py-1.5 resize-none focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={handleRejectSubmit}
                      disabled={!rejectFeedback.trim()}
                      className="text-xs px-3 py-1.5 rounded bg-red-600 text-white hover:bg-red-700 transition-colors disabled:opacity-40"
                    >
                      Send back to Backlog
                    </button>
                    <button
                      onClick={() => { setShowRejectInput(false); setRejectFeedback(''); }}
                      className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {!showLiveLog && renderActivityLog()}

          {/* Confirm overlay — sticky to the bottom of the scroll area so it never
              scrolls out of view (e.g. while a running ticket's log auto-scrolls). */}
          {confirmAction && ['escalate', 'backlog', 'delete'].includes(confirmAction) && (
            <div className={`sticky bottom-0 z-10 rounded-lg border p-3 shadow-lg ${
              confirmAction === 'escalate'
                ? 'bg-amber-50 dark:bg-amber-950 border-amber-200 dark:border-amber-800'
                : 'bg-zinc-50 dark:bg-zinc-900 border-border'
            }`}>
              <p className="text-xs font-medium mb-2">
                {confirmAction === 'escalate'
                  ? 'Reassign to the Tech Lead?'
                  : confirmAction === 'delete'
                  ? 'Delete this ticket permanently?'
                  : 'Move this ticket back to Backlog?'}
              </p>
              <div className="flex gap-2">
                {confirmAction === 'delete' ? (
                  <>
                    <button
                      type="button"
                      onClick={() => handleDelete(false)}
                      className="text-xs px-2.5 py-1 rounded bg-red-600 text-white hover:bg-red-700 transition-colors"
                    >
                      Delete
                    </button>
                    {ticket.status === 'In Progress' && (
                      <button
                        type="button"
                        onClick={() => handleDelete(true)}
                        className="text-xs px-2.5 py-1 rounded border border-red-300 dark:border-red-700 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-950 transition-colors"
                        title="Stop the running agent, then delete"
                      >
                        Stop run and delete
                      </button>
                    )}
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={confirmAction === 'escalate' ? handleEscalate : handleMoveToBacklog}
                    className="text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
                  >
                    Confirm
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setConfirmAction(null)}
                  className="text-xs px-2.5 py-1 rounded border border-border hover:bg-muted transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Actions footer */}
        <div className="shrink-0 border-t border-border px-4 py-3 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground">Actions</span>
            {canRun && (
              <button
                type="button"
                onClick={onRun}
                title={ticket.status === 'Ready' ? 'Start the assigned agent on this ticket' : 'Continue running this ticket'}
                className="flex items-center gap-1 text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Zap size={11} /> {ticket.status === 'Ready' ? 'Start work' : 'Continue'}
              </button>
            )}
            {isRunning && (
              <button
                type="button"
                onClick={onCancel}
                className="flex items-center gap-1 text-xs px-2.5 py-1 rounded border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Square size={11} /> Stop
              </button>
            )}
            {!isEditing && ['Backlog', 'Ready', 'Blocked', 'Diff review'].includes(ticket.status) && (
              <button
                onClick={() => setIsEditing(true)}
                className="flex items-center gap-1 text-xs px-2.5 py-1 rounded border border-border hover:bg-muted transition-colors text-muted-foreground"
              >
                <Pencil size={11} /> Edit
              </button>
            )}
            {manualNextStatuses.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  if (s === 'Backlog' && ticket.status !== 'Backlog') handleMoveToBacklog();
                  else onMove(s);
                }}
                className="text-xs px-2 py-1 rounded border border-border hover:bg-muted transition-colors text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {MANUAL_MOVE_LABELS[s] ?? `Move to ${statusDisplayLabel(s)}`}
              </button>
            ))}

            {canRetry && (
              <button
                onClick={onRetry}
                className="flex items-center gap-1 text-xs px-2.5 py-1 rounded border border-border hover:bg-muted transition-colors text-muted-foreground"
              >
                <RefreshCw size={11} /> Retry
              </button>
            )}

            {ticket.assignedModel !== CLAUDE_TECH_LEAD && (
              <button
                onClick={handleEscalate}
                className={`flex items-center gap-1 text-xs px-2.5 py-1 rounded transition-colors ${
                  confirmAction === 'escalate'
                    ? 'bg-amber-100 dark:bg-amber-900 text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-700'
                    : 'bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-400 border border-amber-200 dark:border-amber-800 hover:bg-amber-100 dark:hover:bg-amber-900'
                }`}
              >
                <Bot size={11} /> Escalate to Tech Lead
              </button>
            )}

            <button
              onClick={() => handleDelete(false)}
              className={`ml-auto flex items-center gap-1 text-xs px-2.5 py-1 rounded transition-colors ${
                confirmAction === 'delete'
                  ? 'bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300 border border-red-300 dark:border-red-700'
                  : 'bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800 hover:bg-red-100 dark:hover:bg-red-900'
              }`}
            >
              <Trash2 size={11} /> Delete
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
