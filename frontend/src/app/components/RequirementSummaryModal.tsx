import { useState } from 'react';
import { X, FileCode, ShieldOff, Sparkles, Cloud, Share2, Copy, Loader2 } from 'lucide-react';
import type { RequirementSource } from '../types';
import { apiClient } from '../api/client';
import { summaryApiUrl, summaryToMarkdown } from '../dxUtils';

function formatCloudCost(usd?: number) {
  if (usd === undefined || usd <= 0) return null;
  return `$${usd.toFixed(4)}`;
}

function formatTokenCount(value?: number) {
  if (value === undefined || value <= 0) return null;
  return value.toLocaleString();
}

interface Props {
  requirement: RequirementSource;
  onClose: () => void;
  usingMockData?: boolean;
}

export function RequirementSummaryModal({ requirement, onClose, usingMockData = false }: Props) {
  const [shareOpen, setShareOpen] = useState(false);
  const [shareLoading, setShareLoading] = useState(false);
  const [shareMarkdown, setShareMarkdown] = useState('');
  const [shareMessage, setShareMessage] = useState('');

  const priorityClass =
    requirement.priority === 'high'
      ? 'bg-red-50 text-red-700 border-red-200 dark:bg-red-950 dark:text-red-300 dark:border-red-800'
      : requirement.priority === 'medium'
      ? 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800'
      : 'bg-zinc-100 text-zinc-600 border-zinc-200 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700';

  async function openShare() {
    setShareOpen(true);
    setShareLoading(true);
    setShareMessage('');
    try {
      if (usingMockData) {
        setShareMarkdown(summaryToMarkdown({
          requirement: {
            id: requirement.id,
            project_id: requirement.repo,
            status: 'confirmed',
            prompt: requirement.prompt,
            scope_paths: requirement.scopePaths,
            constraints: requirement.constraints,
            acceptance_notes: requirement.acceptanceNotes,
          },
          tickets: [],
          run_events: [],
          cost: { total_usd: requirement.cloudCostUsd ?? 0 },
        }));
        return;
      }
      const summary = await apiClient.getRequirementSummary(requirement.id);
      setShareMarkdown(summaryToMarkdown(summary));
    } catch (error) {
      setShareMessage(error instanceof Error ? error.message : 'Could not load share summary.');
    } finally {
      setShareLoading(false);
    }
  }

  async function copyMarkdown() {
    if (!shareMarkdown) return;
    try {
      await navigator.clipboard.writeText(shareMarkdown);
      setShareMessage('Markdown copied.');
    } catch {
      setShareMessage('Could not copy markdown.');
    }
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(summaryApiUrl(requirement.id));
      setShareMessage('Summary API link copied.');
    } catch {
      setShareMessage('Could not copy link.');
    }
  }

  return (
    <>
      <div
        className="fixed inset-0 bg-black/30 dark:bg-black/50 backdrop-blur-[1px]"
        style={{ zIndex: 60 }}
        onClick={onClose}
      />
      <div className="fixed inset-0 flex items-center justify-center p-6 pointer-events-none" style={{ zIndex: 60 }}>
        <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-xl max-h-[80vh] flex flex-col pointer-events-auto overflow-hidden">

          {/* Header */}
          <div className="flex items-center gap-2.5 px-5 py-4 border-b border-border shrink-0">
            <Sparkles size={13} className="text-amber-500 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm font-semibold text-foreground">{requirement.id}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium capitalize ${priorityClass}`}>
                  {requirement.priority}
                </span>
                <span className="text-[10px] px-1.5 py-0.5 rounded border font-medium bg-violet-50 text-violet-600 border-violet-200 dark:bg-violet-950 dark:text-violet-400 dark:border-violet-800">
                  read-only
                </span>
              </div>
              <p className="text-[11px] text-muted-foreground font-mono mt-0.5">
                {requirement.repo}/{requirement.branch} · {requirement.createdAt}
              </p>
            </div>
            <button
              onClick={onClose}
              className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors shrink-0"
            >
              <X size={14} />
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-y-auto p-5 space-y-4">

            {/* Prompt */}
            <div>
              <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1.5">Prompt</p>
              <p className="text-sm leading-relaxed text-foreground">{requirement.prompt}</p>
            </div>

            {/* Scope paths */}
            {requirement.scopePaths.length > 0 && (
              <div>
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1.5 flex items-center gap-1">
                  <FileCode size={9} /> Scope
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {requirement.scopePaths.map((p) => (
                    <span key={p} className="font-mono text-[11px] px-2 py-0.5 rounded bg-muted border border-border">
                      {p}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Constraints */}
            {requirement.constraints.length > 0 && (
              <div>
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1.5 flex items-center gap-1">
                  <ShieldOff size={9} /> Constraints / non-goals
                </p>
                <ul className="space-y-1">
                  {requirement.constraints.map((c, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs">
                      <span className="text-muted-foreground shrink-0 mt-0.5">·</span>
                      <span>{c}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Cloud usage */}
            {(requirement.cloudCostUsd || requirement.cloudInputTokens || requirement.cloudOutputTokens) ? (
              <div className="rounded-lg border border-border bg-muted/30 px-3 py-2.5">
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1.5 flex items-center gap-1">
                  <Cloud size={9} /> Cloud usage (estimate)
                </p>
                <p className="text-sm font-medium text-foreground">
                  {formatCloudCost(requirement.cloudCostUsd)
                    ? `About ${formatCloudCost(requirement.cloudCostUsd)} spent on cloud API`
                    : 'No cloud cost recorded yet'}
                </p>
                {(requirement.cloudInputTokens || requirement.cloudOutputTokens) ? (
                  <p className="text-[11px] text-muted-foreground mt-1 font-mono">
                    {formatTokenCount(requirement.cloudInputTokens)} in · {formatTokenCount(requirement.cloudOutputTokens)} out tokens
                  </p>
                ) : null}
              </div>
            ) : null}

            {/* Acceptance notes */}
            {requirement.acceptanceNotes && (
              <div>
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1.5">Acceptance notes</p>
                <p className="text-xs text-foreground leading-relaxed">{requirement.acceptanceNotes}</p>
              </div>
            )}

            {shareOpen && (
              <div className="rounded-lg border border-border bg-muted/20 px-3 py-3 space-y-2">
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground">Share summary</p>
                {shareLoading ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground py-4 justify-center">
                    <Loader2 size={14} className="animate-spin" />
                    Loading redacted summary…
                  </div>
                ) : (
                  <>
                    <textarea
                      readOnly
                      value={shareMarkdown}
                      rows={8}
                      className="w-full text-[11px] font-mono bg-background border border-border rounded-lg px-2.5 py-2 resize-y"
                    />
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => void copyMarkdown()}
                        className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted"
                      >
                        <Copy size={11} /> Copy markdown
                      </button>
                      <button
                        type="button"
                        onClick={() => void copyLink()}
                        className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted"
                      >
                        <Copy size={11} /> Copy API link
                      </button>
                    </div>
                  </>
                )}
                {shareMessage && <p className="text-[11px] text-muted-foreground">{shareMessage}</p>}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="shrink-0 border-t border-border px-5 py-3 flex justify-between gap-2">
            <button
              type="button"
              onClick={() => void openShare()}
              className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border border-border hover:bg-muted transition-colors"
            >
              <Share2 size={12} />
              Share summary
            </button>
            <button
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
