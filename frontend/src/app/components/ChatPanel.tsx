import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Cloud,
  FileText,
  HardDrive,
  ImagePlus,
  Loader2,
  Paperclip,
  Send,
  X,
} from 'lucide-react';
import { apiClient } from '../api/client';
import type { ChatAttachment, ChatMessage, RequirementSource, Ticket } from '../types';
import { HelpTip } from './HelpTip';
import { HELP_TOOLTIPS } from '../dxUtils';
import { ACTION_DISCLOSURES } from '../trustUtils';
import { ActionDisclosure } from './ActionDisclosure';

export type ChatLayoutMode = 'balanced' | 'rail' | 'hidden';

interface PendingAttachment extends ChatAttachment {
  previewUrl?: string;
}

interface Props {
  messages: ChatMessage[];
  requirements: RequirementSource[];
  tickets: Ticket[];
  layoutMode: ChatLayoutMode;
  sending: boolean;
  chatReasonerMode: 'cloud' | 'local';
  projectId: string;
  usingMockData?: boolean;
  onSend: (text: string, attachmentIds: string[]) => void;
  onToggleReasoner: () => void;
  onCollapse: () => void;
  onExpand: () => void;
  onSelectTicket: (ticketId: string) => void;
  onOpenRequirement: (requirementId: string) => void;
  onUploadError?: (message: string) => void;
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '';
  }
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function reportTicketsForMessage(message: ChatMessage, tickets: Ticket[]): Ticket[] {
  if (!message.requirement_id) return [];
  return tickets.filter(
    (ticket) => ticket.requirementId === message.requirement_id && ticket.status === 'Backlog',
  );
}

function latestReports(messages: ChatMessage[], limit = 3): ChatMessage[] {
  return messages
    .filter((message) => message.role === 'system_report')
    .slice(-limit);
}

function AttachmentChip({
  attachment,
  previewUrl,
  projectId,
  onRemove,
}: {
  attachment: ChatAttachment;
  previewUrl?: string;
  projectId: string;
  onRemove?: () => void;
}) {
  const imageSrc = previewUrl
    ?? (attachment.kind === 'image'
      ? apiClient.chatAttachmentContentUrl(attachment.id, projectId)
      : undefined);

  return (
    <div className="inline-flex items-center gap-1.5 max-w-full rounded-md border border-border bg-background/80 px-1.5 py-1 text-[11px]">
      {attachment.kind === 'image' && imageSrc ? (
        <img
          src={imageSrc}
          alt={attachment.filename}
          className="h-8 w-8 rounded object-cover shrink-0"
        />
      ) : (
        <FileText size={12} className="shrink-0 text-muted-foreground" />
      )}
      <span className="truncate max-w-[140px] text-foreground">{attachment.filename}</span>
      <span className="text-muted-foreground shrink-0">{formatFileSize(attachment.size)}</span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="h-5 w-5 flex items-center justify-center rounded hover:bg-muted text-muted-foreground shrink-0"
          aria-label={`Remove ${attachment.filename}`}
        >
          <X size={11} />
        </button>
      )}
    </div>
  );
}

function MessageAttachments({
  attachments,
  projectId,
}: {
  attachments: ChatAttachment[];
  projectId: string;
}) {
  if (!attachments.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {attachments.map((attachment) => (
        <AttachmentChip
          key={attachment.id}
          attachment={attachment}
          projectId={projectId}
        />
      ))}
    </div>
  );
}

function ReportRow({
  message,
  compact,
  onSelectTicket,
}: {
  message: ChatMessage;
  compact?: boolean;
  onSelectTicket: (ticketId: string) => void;
}) {
  const isDone = message.report_kind === 'done';
  const tone = isDone
    ? 'border-emerald-200 bg-emerald-50/80 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200'
    : 'border-amber-200 bg-amber-50/80 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200';

  return (
    <button
      type="button"
      onClick={() => message.ticket_id && onSelectTicket(message.ticket_id)}
      className={`w-full text-left rounded-lg border px-2.5 py-2 transition-colors hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${tone} ${
        compact ? 'text-[11px]' : 'text-xs'
      }`}
    >
      <div className="flex items-start gap-1.5">
        {isDone ? (
          <CheckCircle2 size={compact ? 11 : 13} className="shrink-0 mt-0.5" />
        ) : (
          <AlertTriangle size={compact ? 11 : 13} className="shrink-0 mt-0.5" />
        )}
        <div className="min-w-0">
          <p className="font-medium leading-snug">{message.text}</p>
          {!compact && (
            <p className="text-[10px] opacity-70 mt-0.5 font-mono">{formatTime(message.created_at)}</p>
          )}
        </div>
      </div>
    </button>
  );
}

function MessageBubble({
  message,
  requirements,
  tickets,
  projectId,
  onSelectTicket,
  onOpenRequirement,
}: {
  message: ChatMessage;
  requirements: RequirementSource[];
  tickets: Ticket[];
  projectId: string;
  onSelectTicket: (ticketId: string) => void;
  onOpenRequirement: (requirementId: string) => void;
}) {
  if (message.role === 'system_report') {
    return (
      <div className="px-3">
        <ReportRow message={message} onSelectTicket={onSelectTicket} />
      </div>
    );
  }

  const isUser = message.role === 'user';
  const proposalTickets = reportTicketsForMessage(message, tickets);
  const requirement = message.requirement_id
    ? requirements.find((item) => item.id === message.requirement_id)
    : undefined;
  const attachments = message.attachments ?? [];

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} px-3`}>
      <div className={`max-w-[92%] space-y-2 ${isUser ? 'items-end' : 'items-start'} flex flex-col`}>
        {attachments.length > 0 && (
          <MessageAttachments attachments={attachments} projectId={projectId} />
        )}
        <div
          className={`rounded-xl px-3 py-2 text-sm leading-relaxed ${
            isUser
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted text-foreground border border-border'
          }`}
        >
          <p className="whitespace-pre-wrap">{message.text}</p>
          <p className={`text-[10px] mt-1 font-mono ${isUser ? 'text-primary-foreground/70' : 'text-muted-foreground'}`}>
            {formatTime(message.created_at)}
          </p>
        </div>

        {message.requirement_id && (
          <div className="w-full space-y-1.5">
            {requirement && (
              <button
                type="button"
                data-testid={`chat-proposal-${message.requirement_id}`}
                onClick={() => onOpenRequirement(message.requirement_id!)}
                className="w-full text-left rounded-lg border border-border bg-card px-2.5 py-2 text-xs hover:bg-muted/60 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <p className="font-medium text-foreground inline-flex items-center gap-1">
                  Proposal · {message.requirement_id}
                  <HelpTip text={HELP_TOOLTIPS.chat_proposal} label="Proposal help" />
                </p>
                <p className="text-muted-foreground mt-0.5 line-clamp-2">{requirement.prompt}</p>
              </button>
            )}
            {proposalTickets.map((ticket) => (
              <button
                key={ticket.id}
                type="button"
                onClick={() => onSelectTicket(ticket.id)}
                className="w-full text-left rounded-lg border border-dashed border-border bg-card/80 px-2.5 py-1.5 text-[11px] hover:bg-muted/50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="font-mono text-muted-foreground">{ticket.id}</span>
                <span className="mx-1.5 text-muted-foreground">·</span>
                <span className="text-foreground">{ticket.title}</span>
                <span className="ml-1.5 text-[10px] uppercase tracking-wide text-amber-700 dark:text-amber-300">
                  Backlog
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function ChatPanel({
  messages,
  requirements,
  tickets,
  layoutMode,
  sending,
  chatReasonerMode,
  projectId,
  usingMockData = false,
  onSend,
  onToggleReasoner,
  onCollapse,
  onExpand,
  onSelectTicket,
  onOpenRequirement,
  onUploadError,
}: Props) {
  const [draft, setDraft] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const isRail = layoutMode === 'rail';
  const reports = useMemo(() => latestReports(messages), [messages]);
  const hasImageAttachment = pendingAttachments.some((attachment) => attachment.kind === 'image');
  const showImageNotice = chatReasonerMode === 'local' && hasImageAttachment;
  const canSend = draft.trim().length > 0 && !sending && !uploading;

  useEffect(() => {
    const node = listRef.current;
    if (!node || isRail) return;
    node.scrollTop = node.scrollHeight;
  }, [messages, isRail, pendingAttachments]);

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setUploading(true);
    try {
      for (const file of list) {
        if (usingMockData) {
          const kind: ChatAttachment['kind'] = file.type.startsWith('image/') ? 'image' : 'file';
          const mockAttachment: PendingAttachment = {
            id: `ATT-mock-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
            filename: file.name,
            mime: file.type || 'application/octet-stream',
            size: file.size,
            kind,
            stored_path: `mock/${file.name}`,
            previewUrl: kind === 'image' ? URL.createObjectURL(file) : undefined,
          };
          setPendingAttachments((prev) => [...prev, mockAttachment]);
          continue;
        }
        const attachment = await apiClient.uploadChatAttachment(projectId, file);
        const previewUrl = attachment.kind === 'image' ? URL.createObjectURL(file) : undefined;
        setPendingAttachments((prev) => [...prev, { ...attachment, previewUrl }]);
      }
    } catch (error) {
      onUploadError?.(error instanceof Error ? error.message : 'Could not upload attachment.');
    } finally {
      setUploading(false);
    }
  }

  function removePendingAttachment(attachmentId: string) {
    setPendingAttachments((prev) => {
      const target = prev.find((item) => item.id === attachmentId);
      if (target?.previewUrl) {
        URL.revokeObjectURL(target.previewUrl);
      }
      return prev.filter((item) => item.id !== attachmentId);
    });
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending || uploading) return;
    onSend(
      text,
      pendingAttachments.map((attachment) => attachment.id),
    );
    setDraft('');
    pendingAttachments.forEach((attachment) => {
      if (attachment.previewUrl) {
        URL.revokeObjectURL(attachment.previewUrl);
      }
    });
    setPendingAttachments([]);
  }

  function handleDragOver(event: React.DragEvent) {
    event.preventDefault();
    if (!isRail) setDragActive(true);
  }

  function handleDragLeave(event: React.DragEvent) {
    event.preventDefault();
    setDragActive(false);
  }

  function handleDrop(event: React.DragEvent) {
    event.preventDefault();
    setDragActive(false);
    if (isRail || sending || uploading) return;
    void uploadFiles(event.dataTransfer.files);
  }

  if (layoutMode === 'hidden') {
    return (
      <div className="w-9 shrink-0 border-r border-border bg-card flex flex-col items-center pt-2">
        <button
          type="button"
          onClick={onExpand}
          title="Show chat"
          aria-label="Show chat panel"
          className="w-7 h-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronRight size={14} />
        </button>
      </div>
    );
  }

  return (
    <aside
      data-testid="chat-panel"
      className={`shrink-0 border-r border-border bg-card flex flex-col min-h-0 transition-[width] duration-200 ${
        isRail ? 'w-[220px]' : 'w-[min(420px,38vw)]'
      } ${dragActive ? 'ring-2 ring-inset ring-primary/40' : ''}`}
      aria-label="Orchestrator chat"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <header className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-foreground truncate">Orchestrator</p>
          {!isRail && (
            <p className="text-[10px] text-muted-foreground truncate">Chat with your agent</p>
          )}
        </div>
        <button
          type="button"
          onClick={onToggleReasoner}
          title={
            chatReasonerMode === 'cloud'
              ? 'Chat model: cloud — click to switch to local'
              : 'Chat model: local — click to switch to cloud'
          }
          aria-label={`Chat model: ${chatReasonerMode}. Click to switch.`}
          className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
            chatReasonerMode === 'cloud'
              ? 'border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-200'
              : 'border-border bg-muted text-muted-foreground hover:text-foreground'
          }`}
        >
          {chatReasonerMode === 'cloud' ? <Cloud size={10} /> : <HardDrive size={10} />}
          {chatReasonerMode === 'cloud' ? 'Cloud' : 'Local'}
        </button>
        {chatReasonerMode === 'cloud' && !isRail && (
          <ActionDisclosure text={ACTION_DISCLOSURES.cloud_reasoner} />
        )}
        <button
          type="button"
          onClick={isRail ? onCollapse : onCollapse}
          title={isRail ? 'Hide chat' : 'Collapse to rail'}
          aria-label={isRail ? 'Hide chat' : 'Collapse chat to rail'}
          className="w-7 h-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronLeft size={14} />
        </button>
      </header>

      {isRail ? (
        <div className="flex-1 overflow-y-auto px-2 py-2 space-y-1.5 min-h-0">
          {reports.length === 0 ? (
            <p className="text-[11px] text-muted-foreground px-1">No recent reports</p>
          ) : (
            reports.map((message) => (
              <ReportRow
                key={message.id}
                message={message}
                compact
                onSelectTicket={onSelectTicket}
              />
            ))
          )}
        </div>
      ) : (
        <div ref={listRef} className="flex-1 overflow-y-auto py-3 space-y-3 min-h-0">
          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              requirements={requirements}
              tickets={tickets}
              projectId={projectId}
              onSelectTicket={onSelectTicket}
              onOpenRequirement={onOpenRequirement}
            />
          ))}
        </div>
      )}

      <form onSubmit={handleSubmit} className="shrink-0 border-t border-border p-2 space-y-2">
        {!isRail && pendingAttachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {pendingAttachments.map((attachment) => (
              <AttachmentChip
                key={attachment.id}
                attachment={attachment}
                previewUrl={attachment.previewUrl}
                projectId={projectId}
                onRemove={() => removePendingAttachment(attachment.id)}
              />
            ))}
          </div>
        )}
        {!isRail && showImageNotice && (
          <div className="px-1">
            <ActionDisclosure text={ACTION_DISCLOSURES.image_local} />
          </div>
        )}
        <div className="flex items-end gap-1.5">
          {!isRail && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={(event) => {
                  if (event.target.files) {
                    void uploadFiles(event.target.files);
                    event.target.value = '';
                  }
                }}
              />
              <input
                ref={imageInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(event) => {
                  if (event.target.files) {
                    void uploadFiles(event.target.files);
                    event.target.value = '';
                  }
                }}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={sending || uploading}
                title="Attach file"
                aria-label="Attach file"
                className="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg border border-border text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {uploading ? <Loader2 size={14} className="animate-spin" /> : <Paperclip size={14} />}
              </button>
              <button
                type="button"
                onClick={() => imageInputRef.current?.click()}
                disabled={sending || uploading}
                title="Attach image"
                aria-label="Attach image"
                className="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg border border-border text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <ImagePlus size={14} />
              </button>
            </>
          )}
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                handleSubmit(event);
              }
            }}
            rows={isRail ? 2 : 3}
            placeholder={isRail ? 'Reply…' : 'Describe what you want built…'}
            aria-label="Chat message"
            data-testid="chat-message-input"
            className="flex-1 resize-none rounded-lg border border-border bg-background px-2.5 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
          />
          <button
            type="submit"
            disabled={!canSend}
            aria-label="Send message"
            data-testid="chat-send"
            className="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg bg-primary text-primary-foreground disabled:opacity-50 hover:opacity-90 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          </button>
        </div>
      </form>
    </aside>
  );
}
