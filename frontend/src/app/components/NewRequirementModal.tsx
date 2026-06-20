import { useState } from 'react';
import { X, Sparkles, Loader2, Bot } from 'lucide-react';
import type { Ticket, TicketType, TicketStatus, AssignedModel, TestStatus, LogLevel, Priority } from '../types';

interface Props {
  onClose: () => void;
  onAddTickets: (tickets: Ticket[]) => void;
  currentTicketCount: number;
}

function generateTickets(requirement: string, baseId: number): Ticket[] {
  const now = new Date().toLocaleTimeString('en-US', { hour12: false });
  const lower = requirement.toLowerCase();

  const is2fa = lower.includes('2fa') || lower.includes('totp') || lower.includes('two-factor') || lower.includes('mfa');
  const isOauth = lower.includes('oauth') || lower.includes('sso') || lower.includes('google') || lower.includes('github');
  const isEmail = lower.includes('email') || lower.includes('verification');
  const isPassword = lower.includes('password') || lower.includes('reset');
  const isRateLimit = lower.includes('rate') || lower.includes('throttl') || lower.includes('limit');

  type Task = { title: string; type: TicketType; priority: Priority };
  let tasks: Task[];

  if (is2fa) {
    tasks = [
      { title: 'Add TOTP secret field to User model + migration', type: 'feature', priority: 'high' },
      { title: 'Implement POST /auth/2fa/setup and /auth/2fa/verify', type: 'feature', priority: 'high' },
      { title: 'Write tests for TOTP setup and verification flow', type: 'test', priority: 'medium' },
    ];
  } else if (isOauth) {
    tasks = [
      { title: 'Add OAuth provider config to auth_service/config.py', type: 'feature', priority: 'medium' },
      { title: 'Implement OAuth callback handler + token exchange', type: 'feature', priority: 'high' },
      { title: 'Write integration tests for OAuth flow', type: 'test', priority: 'medium' },
    ];
  } else if (isEmail) {
    tasks = [
      { title: 'Add email verification token to User model', type: 'feature', priority: 'high' },
      { title: 'Implement POST /auth/verify-email endpoint', type: 'feature', priority: 'high' },
      { title: 'Write tests for email verification expiry edge cases', type: 'test', priority: 'medium' },
    ];
  } else if (isPassword) {
    tasks = [
      { title: 'Add password history table to prevent reuse', type: 'feature', priority: 'medium' },
      { title: 'Enforce minimum complexity in password_reset flow', type: 'bugfix', priority: 'high' },
      { title: 'Write tests for password policy enforcement', type: 'test', priority: 'medium' },
    ];
  } else if (isRateLimit) {
    tasks = [
      { title: 'Add rate-limit middleware to auth_service/middleware.py', type: 'feature', priority: 'high' },
      { title: 'Store rate-limit counters in Redis with TTL', type: 'feature', priority: 'high' },
      { title: 'Write tests for rate-limit sliding window logic', type: 'test', priority: 'medium' },
    ];
  } else {
    const slug = requirement.length > 35 ? requirement.slice(0, 35) + '…' : requirement;
    tasks = [
      { title: `${slug}: data model + migration`, type: 'feature', priority: 'medium' },
      { title: `${slug}: API endpoint implementation`, type: 'feature', priority: 'high' },
      { title: `${slug}: unit + integration tests`, type: 'test', priority: 'medium' },
    ];
  }

  return tasks.map((task, i) => ({
    id: `T-0${baseId + i}`,
    title: task.title,
    type: task.type,
    status: 'Backlog' as TicketStatus,
    priority: task.priority,
    assignedModel: 'qwen3-coder-next' as AssignedModel,
    retryCount: 0,
    retryBudget: 3,
    testStatus: 'none' as TestStatus,
    needsApproval: true,
    isNew: true,
    contextFiles: [],
    definitionOfDone: {
      tests: [],
      acceptanceCriteria: ['Acceptance criteria pending — review with Claude · Tech Lead'],
    },
    agentLog: [
      {
        time: now,
        level: 'info' as LogLevel,
        message: `Decomposed by Claude · Tech Lead from: "${requirement.slice(0, 80)}"`,
      },
    ],
  }));
}

export function NewRequirementModal({ onClose, onAddTickets, currentTicketCount }: Props) {
  const [requirement, setRequirement] = useState('');
  const [loading, setLoading] = useState(false);
  const [decomposed, setDecomposed] = useState<Ticket[] | null>(null);

  async function handleDecompose() {
    if (!requirement.trim()) return;
    setLoading(true);
    await new Promise(r => setTimeout(r, 1800));
    const nextId = 16 + (currentTicketCount - 6);
    const tickets = generateTickets(requirement, nextId);
    setDecomposed(tickets);
    setLoading(false);
  }

  function handleAccept() {
    if (decomposed) {
      onAddTickets(decomposed);
      onClose();
    }
  }

  return (
    <>
      <div
        className="fixed inset-0 bg-black/25 dark:bg-black/50 z-50 backdrop-blur-[1px]"
        onClick={!loading ? onClose : undefined}
      />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-xl pointer-events-auto overflow-hidden">

          {/* Header */}
          <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
            <Sparkles size={14} className="text-amber-500" />
            <span className="text-sm font-medium">New requirement</span>
            <div className="flex-1" />
            <button
              onClick={onClose}
              className="h-6 w-6 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            >
              <X size={13} />
            </button>
          </div>

          {/* Body */}
          <div className="p-4 space-y-3">
            {!decomposed ? (
              <>
                <p className="text-xs text-muted-foreground">
                  Paste a natural-language requirement. Claude · Tech Lead will decompose it into actionable tickets.
                </p>
                <textarea
                  className="w-full h-28 text-sm bg-muted/50 border border-border rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground"
                  placeholder="e.g. Add two-factor authentication using TOTP so users can secure their accounts with an authenticator app"
                  value={requirement}
                  onChange={e => setRequirement(e.target.value)}
                  disabled={loading}
                />
                <div className="flex items-center justify-end gap-2">
                  <button
                    onClick={onClose}
                    className="text-xs px-3 py-1.5 rounded border border-border hover:bg-muted transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleDecompose}
                    disabled={!requirement.trim() || loading}
                    className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-40"
                  >
                    {loading ? (
                      <>
                        <Loader2 size={11} className="animate-spin" />
                        Claude · Tech Lead is decomposing…
                      </>
                    ) : (
                      <>
                        <Bot size={11} />
                        Decompose
                      </>
                    )}
                  </button>
                </div>

                {loading && (
                  <div className="rounded-lg bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 px-3 py-2">
                    <div className="flex items-center gap-2 text-xs text-amber-700 dark:text-amber-300">
                      <Loader2 size={11} className="animate-spin shrink-0" />
                      <span>Claude · Tech Lead is analysing the requirement and breaking it into sprint-ready tickets…</span>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400 font-medium">
                  <Bot size={12} />
                  Claude · Tech Lead decomposed {decomposed.length} tickets
                </div>
                <div className="space-y-2">
                  {decomposed.map((t) => (
                    <div
                      key={t.id}
                      className="flex items-start gap-2.5 rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50/50 dark:bg-emerald-950/30 px-3 py-2"
                    >
                      <span className="font-mono text-xs text-muted-foreground mt-0.5 shrink-0">{t.id}</span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-foreground">{t.title}</p>
                        <div className="flex items-center gap-1.5 mt-1">
                          <span className="text-[10px] px-1 rounded uppercase tracking-wide bg-blue-50 dark:bg-blue-950 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-800">
                            {t.type}
                          </span>
                          <span className="text-xs text-muted-foreground">→ Backlog</span>
                          <span className="text-[10px] px-1.5 py-0 rounded bg-emerald-50 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800">
                            needs review
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="flex items-center justify-end gap-2 pt-1">
                  <button
                    onClick={() => setDecomposed(null)}
                    className="text-xs px-3 py-1.5 rounded border border-border hover:bg-muted transition-colors"
                  >
                    Back
                  </button>
                  <button
                    onClick={handleAccept}
                    className="text-xs px-3 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
                  >
                    Add to Backlog
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
