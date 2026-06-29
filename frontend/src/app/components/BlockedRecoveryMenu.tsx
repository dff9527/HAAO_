import { useState, type ReactNode } from 'react';
import { Bot, GitBranch, Loader2, RefreshCw, Scissors, Trash2 } from 'lucide-react';

interface Props {
  localModelIds: string[];
  assignedModel: string;
  pending?: boolean;
  onSplit: (feedback: string) => void | Promise<void>;
  onClarify: () => void;
  onChangeModelRetry: (model: string) => void | Promise<void>;
  onEscalate: () => void | Promise<void>;
  onAbandon: (reason: string) => void | Promise<void>;
}

export function BlockedRecoveryMenu({
  localModelIds,
  assignedModel,
  pending = false,
  onSplit,
  onClarify,
  onChangeModelRetry,
  onEscalate,
  onAbandon,
}: Props) {
  const [splitFeedback, setSplitFeedback] = useState('');
  const [abandonReason, setAbandonReason] = useState('');
  const [model, setModel] = useState(assignedModel);

  return (
    <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50/60 dark:bg-amber-950/30 p-3 space-y-3">
      <p className="text-xs font-semibold text-amber-800 dark:text-amber-200">Recovery options</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <ActionButton icon={Scissors} label="Split into smaller tickets" onClick={() => splitFeedback.trim() && void onSplit(splitFeedback.trim())} disabled={pending || !splitFeedback.trim()}>
          <input
            value={splitFeedback}
            onChange={(event) => setSplitFeedback(event.target.value)}
            placeholder="What should be split?"
            className="w-full mt-1 text-[11px] bg-background border border-border rounded px-2 py-1"
          />
        </ActionButton>
        <ActionButton icon={RefreshCw} label="Clarify requirement" onClick={onClarify} disabled={pending} />
        <ActionButton
          icon={GitBranch}
          label="Change model & retry"
          onClick={() => void onChangeModelRetry(model)}
          disabled={pending || !model}
        >
          <select
            value={model}
            onChange={(event) => setModel(event.target.value)}
            className="w-full mt-1 text-[11px] bg-background border border-border rounded px-2 py-1"
          >
            {localModelIds.map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </ActionButton>
        <ActionButton icon={Bot} label="Escalate to Tech Lead" onClick={() => void onEscalate()} disabled={pending} />
        <ActionButton icon={Trash2} label="Abandon ticket" onClick={() => abandonReason.trim() && void onAbandon(abandonReason.trim())} disabled={pending || !abandonReason.trim()}>
          <input
            value={abandonReason}
            onChange={(event) => setAbandonReason(event.target.value)}
            placeholder="Why abandon?"
            className="w-full mt-1 text-[11px] bg-background border border-border rounded px-2 py-1"
          />
        </ActionButton>
      </div>
      {pending && (
        <p className="text-[11px] text-muted-foreground inline-flex items-center gap-1">
          <Loader2 size={11} className="animate-spin" /> Working…
        </p>
      )}
    </div>
  );
}

function ActionButton({
  icon: Icon,
  label,
  onClick,
  disabled,
  children,
}: {
  icon: typeof Scissors;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="rounded border border-border bg-card p-2">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className="w-full text-left text-xs font-medium text-foreground inline-flex items-center gap-1.5 disabled:opacity-50"
      >
        <Icon size={12} />
        {label}
      </button>
      {children}
    </div>
  );
}
