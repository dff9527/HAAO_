import { CheckCircle2, FolderGit2, Loader2, Sparkles, Wand2, X } from 'lucide-react';
import type { ReactNode } from 'react';

interface Props {
  open: boolean;
  projectPathReady: boolean;
  modelsConfigured: boolean;
  seedingDemo: boolean;
  onClose: () => void;
  onDismiss: () => void;
  onOpenModels: () => void;
  onNewRequirement: () => void;
  onSeedDemo: () => void;
}

export function OnboardingWizard({
  open,
  projectPathReady,
  modelsConfigured,
  seedingDemo,
  onClose,
  onDismiss,
  onOpenModels,
  onNewRequirement,
  onSeedDemo,
}: Props) {
  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 bg-black/35 dark:bg-black/55 backdrop-blur-[1px] z-[70]" onClick={onClose} />
      <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 pointer-events-none">
        <div
          className="pointer-events-auto w-full max-w-lg rounded-2xl border border-border bg-card shadow-2xl overflow-hidden"
          role="dialog"
          aria-labelledby="onboarding-title"
        >
          <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border">
            <div>
              <h2 id="onboarding-title" className="text-sm font-semibold text-foreground flex items-center gap-2">
                <Wand2 size={15} className="text-violet-600 dark:text-violet-400" />
                Get started with HAAO
              </h2>
              <p className="text-xs text-muted-foreground mt-1">Three quick steps to your first shipped ticket.</p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Close setup guide"
            >
              <X size={14} />
            </button>
          </div>

          <ol className="p-5 space-y-4">
            <Step
              done={projectPathReady}
              number={1}
              title="Connect a repository"
              description="Set your git repo path in the project menu (top left). HAAO runs tests and applies diffs there."
              action={
                !projectPathReady ? (
                  <p className="text-[11px] text-muted-foreground">
                    Open the project switcher → edit settings → paste your local repo path.
                  </p>
                ) : (
                  <DoneNote />
                )
              }
            />
            <Step
              done={modelsConfigured}
              number={2}
              title="Configure a model"
              description="Add a local LM Studio endpoint or a cloud API key so agents can decompose and execute work."
              action={
                modelsConfigured ? (
                  <DoneNote />
                ) : (
                  <button
                    type="button"
                    onClick={onOpenModels}
                    className="text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors"
                  >
                    Open Settings
                  </button>
                )
              }
            />
            <Step
              done={false}
              number={3}
              title="Describe your first requirement"
              description="Plain language in — Tech Lead proposes tickets into Backlog. Or try the bundled demo project."
              action={
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={onNewRequirement}
                    disabled={!projectPathReady || !modelsConfigured}
                    className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
                  >
                    <Sparkles size={12} className="text-amber-500" />
                    New requirement
                  </button>
                  <button
                    type="button"
                    onClick={onSeedDemo}
                    disabled={seedingDemo}
                    className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded bg-foreground text-background hover:opacity-90 transition-opacity disabled:opacity-50"
                  >
                    {seedingDemo ? <Loader2 size={12} className="animate-spin" /> : <FolderGit2 size={12} />}
                    Try the demo project
                  </button>
                </div>
              }
            />
          </ol>

          <div className="px-5 py-3 border-t border-border flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={onDismiss}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Don&apos;t show again
            </button>
            <button
              type="button"
              onClick={onClose}
              className="text-xs px-3 py-1.5 rounded border border-border hover:bg-muted transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

function Step({
  done,
  number,
  title,
  description,
  action,
}: {
  done: boolean;
  number: number;
  title: string;
  description: string;
  action: ReactNode;
}) {
  return (
    <li className="flex gap-3">
      <span
        className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
          done
            ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
            : 'bg-muted text-muted-foreground'
        }`}
      >
        {done ? <CheckCircle2 size={13} /> : number}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{title}</p>
        <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{description}</p>
        <div className="mt-2">{action}</div>
      </div>
    </li>
  );
}

function DoneNote() {
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
      <CheckCircle2 size={12} /> Done
    </span>
  );
}
