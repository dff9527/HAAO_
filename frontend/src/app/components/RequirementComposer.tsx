import { useState, useRef, useEffect, KeyboardEvent } from 'react';
import {
  X, ChevronDown, ChevronRight, Loader2, Sparkles,
  Plus, Trash2, CheckCircle2, FileCode, Circle, CheckSquare, Square,
  LayoutTemplate, BookmarkPlus,
} from 'lucide-react';
import { DEFAULT_LOCAL_MODELS, getModelMeta, TYPE_CLASSES } from '../constants';
import type {
  Ticket, TicketType, TicketStatus, AssignedModel, TestStatus,
  LogLevel, Priority, ContextFile, DefinitionOfDone, RequirementSource,
  Project, RequirementGranularity, RequirementIntent, RequirementScale,
} from '../types';
import type { BackendProjectConventions, BackendRequirementDecomposeRequest, BackendTicket, RequirementTemplate } from '../api/types';
import { toUiTicket } from '../api/adapter';
import { apiClient } from '../api/client';
import { MOCK_REQUIREMENT_TEMPLATES } from '../dxUtils';

// ── Local types ────────────────────────────────────────────────────────────

interface ComposeForm {
  projectId: string;
  prompt: string;
  repo: string;
  branch: string;
  scopePaths: string[];
  constraints: string[];
  priority: Priority;
  intent: RequirementIntent;
  scale: RequirementScale;
  granularity: RequirementGranularity;
  allowNewFiles: boolean;
  testCommand: string;
  acceptanceNotes: string;
}

interface ProposedTicket {
  tempId: string;
  title: string;
  type: TicketType;
  priority: Priority;
  assignedModel: AssignedModel;
  contextFiles: ContextFile[];
  definitionOfDone: DefinitionOfDone;
  included: boolean;
  dodExpanded: boolean;
  backendTicket?: BackendTicket;
}

// ── Seed data generator ────────────────────────────────────────────────────

function buildProposedTickets(form: ComposeForm): ProposedTicket[] {
  const lower = form.prompt.toLowerCase();
  const isPassword = lower.includes('password') || lower.includes('bcrypt') || lower.includes('hash') || lower.includes('harden');
  const is2fa = lower.includes('2fa') || lower.includes('totp') || lower.includes('two-factor');
  const isOauth = lower.includes('oauth') || lower.includes('sso');
  const isRateLimit = lower.includes('rate') || lower.includes('throttl');
  const isEmail = lower.includes('email') || lower.includes('verification');

  type Draft = { title: string; type: TicketType; model: AssignedModel; files: ContextFile[]; tests: string[]; criteria: string[] };

  let drafts: Draft[];

  if (isPassword) {
    drafts = [
      {
        title: 'Switch hash_password to bcrypt',
        type: 'refactor', model: 'qwen3-coder-next',
        files: [
          { path: 'auth_service/utils/crypto.py', reason: 'Contains hash_password (SHA-256 → bcrypt)' },
          { path: 'auth_service/models/user.py', reason: 'Calls hash_password on User.save()' },
          { path: 'requirements.txt', reason: 'bcrypt==4.1.2 must be added' },
          { path: 'tests/test_crypto.py', reason: 'Hash tests to update' },
        ],
        tests: ['pytest tests/test_crypto.py -v', 'pytest tests/test_auth_flow.py::test_login_with_bcrypt_hash'],
        criteria: ['hash_password uses bcrypt with work factor ≥ 12', 'verify_password returns True for correct password', 'No plain-text passwords in logs'],
      },
      {
        title: 'Add weak-password rejection on login',
        type: 'feature', model: 'qwen3-coder-next',
        files: [
          { path: 'auth_service/routes/login.py', reason: 'Entry point for validation' },
          { path: 'auth_service/validators.py', reason: 'New password strength validator' },
        ],
        tests: ['pytest tests/test_login.py::test_weak_password_rejected -v'],
        criteria: ['Rejects passwords shorter than 8 characters', 'Rejects passwords in common top-10k list', 'Returns 400 with descriptive error message'],
      },
      {
        title: 'Add tests for password policy',
        type: 'test', model: 'gemma-4-26b-a4b',
        files: [
          { path: 'tests/test_password_policy.py', reason: 'New test file for policy rules' },
          { path: 'auth_service/validators.py', reason: 'Source under test' },
        ],
        tests: ['pytest tests/test_password_policy.py -v'],
        criteria: ['Covers all rejection rules', 'Parametrized for boundary values', '≥ 95% branch coverage on validators.py'],
      },
    ];
  } else if (is2fa) {
    drafts = [
      {
        title: 'Add TOTP secret field to User model + migration',
        type: 'feature', model: 'qwen3-coder-next',
        files: [{ path: 'auth_service/models/user.py', reason: 'User model extension' }, { path: 'alembic/versions/', reason: 'New migration' }],
        tests: ['pytest tests/test_user_model.py::test_totp_field -v'],
        criteria: ['User.totp_secret is nullable, encrypted at rest', 'Migration runs cleanly on fresh DB'],
      },
      {
        title: 'Implement POST /auth/2fa/setup and /auth/2fa/verify',
        type: 'feature', model: 'qwen3-coder-next',
        files: [{ path: 'auth_service/routes/two_factor.py', reason: 'New 2FA routes' }, { path: 'auth_service/services/totp_service.py', reason: 'TOTP logic' }],
        tests: ['pytest tests/test_two_factor.py -v'],
        criteria: ['Setup returns QR-code URI', 'Verify returns JWT on correct code', 'Wrong code returns 401'],
      },
      {
        title: 'Write integration tests for 2FA flow',
        type: 'test', model: 'gemma-4-26b-a4b',
        files: [{ path: 'tests/test_two_factor.py', reason: 'Integration test file' }],
        tests: ['pytest tests/test_two_factor.py -v --cov'],
        criteria: ['Happy path: setup → verify succeeds', 'Replay attack: same code rejected twice', '≥ 90% branch coverage'],
      },
    ];
  } else if (isRateLimit) {
    drafts = [
      {
        title: 'Add rate-limit middleware to auth routes',
        type: 'feature', model: 'qwen3-coder-next',
        files: [{ path: 'auth_service/middleware.py', reason: 'New rate-limit middleware' }, { path: 'auth_service/config.py', reason: 'RATE_LIMIT_* env vars' }],
        tests: ['pytest tests/test_rate_limit.py -v'],
        criteria: ['Configurable via env vars', 'Returns 429 with Retry-After header', 'Sliding-window algorithm'],
      },
      {
        title: 'Store rate-limit counters in Redis with TTL',
        type: 'feature', model: 'qwen3.6-35b-a3b',
        files: [{ path: 'auth_service/services/rate_limit_store.py', reason: 'Redis counter store' }, { path: 'requirements.txt', reason: 'redis-py dependency' }],
        tests: ['pytest tests/test_rate_limit_store.py -v'],
        criteria: ['Counter increments atomically', 'TTL matches window size', 'Falls back gracefully if Redis unavailable'],
      },
      {
        title: 'Write tests for sliding-window rate limiting',
        type: 'test', model: 'gemma-4-26b-a4b',
        files: [{ path: 'tests/test_rate_limit.py', reason: 'Rate-limit integration tests' }],
        tests: ['pytest tests/test_rate_limit.py -v'],
        criteria: ['Tests burst limit', 'Tests sustained limit', 'Tests concurrent requests'],
      },
    ];
  } else if (isEmail) {
    drafts = [
      {
        title: 'Add email verification token to User model',
        type: 'feature', model: 'qwen3-coder-next',
        files: [{ path: 'auth_service/models/user.py', reason: 'is_verified + token fields' }, { path: 'alembic/versions/', reason: 'Migration for new columns' }],
        tests: ['pytest tests/test_user_model.py -v'],
        criteria: ['is_verified defaults to False', 'verification_token is unique + indexed', 'Migration is reversible'],
      },
      {
        title: 'Implement POST /auth/verify-email endpoint',
        type: 'feature', model: 'qwen3-coder-next',
        files: [{ path: 'auth_service/routes/register.py', reason: 'Add verify endpoint' }, { path: 'auth_service/services/mailer.py', reason: 'Send verification email' }],
        tests: ['pytest tests/test_register.py::test_email_verification_flow -v'],
        criteria: ['Token expires after 24h', 'Returns 410 on expired token', 'Idempotent: second verify returns 200'],
      },
      {
        title: 'Write tests for email verification expiry edge cases',
        type: 'test', model: 'gemma-4-26b-a4b',
        files: [{ path: 'tests/test_register.py', reason: 'Verification edge cases' }],
        tests: ['pytest tests/test_register.py -k verification -v'],
        criteria: ['Expired token → 410', 'Invalid token → 404', 'Already verified → 200 (idempotent)'],
      },
    ];
  } else if (isOauth) {
    drafts = [
      {
        title: 'Add OAuth provider config to auth_service/config.py',
        type: 'feature', model: 'qwen3-coder-next',
        files: [{ path: 'auth_service/config.py', reason: 'OAUTH_* env vars' }],
        tests: ['pytest tests/test_oauth_config.py -v'],
        criteria: ['OAUTH_CLIENT_ID / SECRET / REDIRECT_URI configurable via env', 'Missing env vars raise clear startup error'],
      },
      {
        title: 'Implement OAuth callback handler + token exchange',
        type: 'feature', model: 'qwen3-coder-next',
        files: [{ path: 'auth_service/routes/oauth.py', reason: 'GET /auth/oauth/callback' }, { path: 'auth_service/services/oauth_service.py', reason: 'Token exchange logic' }],
        tests: ['pytest tests/test_oauth.py -v'],
        criteria: ['Exchanges auth code for access token', 'Creates or updates User from provider profile', 'Returns JWT on success'],
      },
      {
        title: 'Write integration tests for OAuth flow',
        type: 'test', model: 'gemma-4-26b-a4b',
        files: [{ path: 'tests/test_oauth.py', reason: 'OAuth integration tests (mocked provider)' }],
        tests: ['pytest tests/test_oauth.py -v'],
        criteria: ['Happy path: callback → JWT', 'Invalid state param → 400', 'Provider error → 502 with message'],
      },
    ];
  } else {
    const slug = form.prompt.length > 40 ? form.prompt.slice(0, 40) + '…' : form.prompt;
    drafts = [
      {
        title: `${slug} — data model`,
        type: 'feature', model: 'qwen3-coder-next',
        files: [{ path: 'auth_service/models/', reason: 'Model changes' }, { path: 'alembic/versions/', reason: 'Migration' }],
        tests: ['pytest tests/test_models.py -v'],
        criteria: ['Schema matches spec', 'Migration reversible', 'Indexed fields documented'],
      },
      {
        title: `${slug} — API endpoint`,
        type: 'feature', model: 'qwen3-coder-next',
        files: [{ path: 'auth_service/routes/', reason: 'New route' }, { path: 'auth_service/services/', reason: 'Business logic' }],
        tests: ['pytest tests/test_routes.py -v'],
        criteria: ['Returns correct status codes', 'Input validation enforced', 'Errors return structured JSON'],
      },
      {
        title: `${slug} — unit + integration tests`,
        type: 'test', model: 'gemma-4-26b-a4b',
        files: [{ path: 'tests/', reason: 'Test coverage' }],
        tests: ['pytest tests/ -v --cov'],
        criteria: ['Happy path covered', 'Edge cases and error paths covered', '≥ 90% branch coverage'],
      },
    ];
  }

  return drafts.map((d, i) => ({
    tempId: `draft-${i + 1}`,
    title: d.title,
    type: d.type,
    priority: form.priority,
    assignedModel: d.model,
    contextFiles: d.files,
    definitionOfDone: { tests: d.tests, acceptanceCriteria: d.criteria },
    included: true,
    dodExpanded: false,
  }));
}

// ── Sub-components ─────────────────────────────────────────────────────────

function StepDots({ current }: { current: 1 | 2 | 3 }) {
  return (
    <div className="flex items-center gap-1.5">
      {([1, 2, 3] as const).map((n) => (
        <div key={n} className={`rounded-full transition-all ${
          n === current
            ? 'w-4 h-1.5 bg-primary'
            : n < current
            ? 'w-1.5 h-1.5 bg-primary/40'
            : 'w-1.5 h-1.5 bg-muted-foreground/20'
        }`} />
      ))}
    </div>
  );
}

function ChipInput({
  chips, onAdd, onRemove, placeholder, monoFont = true,
}: {
  chips: string[];
  onAdd: (v: string) => void;
  onRemove: (v: string) => void;
  placeholder?: string;
  monoFont?: boolean;
}) {
  const [draft, setDraft] = useState('');

  function commit() {
    const v = draft.trim();
    if (v && !chips.includes(v)) { onAdd(v); setDraft(''); }
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); commit(); }
    if (e.key === 'Backspace' && draft === '' && chips.length > 0) onRemove(chips[chips.length - 1]);
  }

  return (
    <div className="flex flex-wrap gap-1.5 items-center p-1.5 border border-border rounded-lg bg-muted/40 min-h-[36px] cursor-text" onClick={(e) => (e.currentTarget.querySelector('input') as HTMLInputElement)?.focus()}>
      {chips.map((c) => (
        <span key={c} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded bg-card border border-border text-xs ${monoFont ? 'font-mono' : ''}`}>
          {c}
          <button onClick={() => onRemove(c)} className="text-muted-foreground hover:text-foreground transition-colors"><X size={10} /></button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKey}
        onBlur={commit}
        placeholder={chips.length === 0 ? placeholder : undefined}
        className={`flex-1 min-w-[80px] bg-transparent text-xs outline-none placeholder:text-muted-foreground ${monoFont ? 'font-mono' : ''}`}
      />
    </div>
  );
}

const ALL_TYPES: TicketType[] = ['feature', 'bugfix', 'refactor', 'test', 'chore'];

function ProposedTicketCard({
  ticket, index, onChange, localModelIds,
}: {
  ticket: ProposedTicket;
  index: number;
  onChange: (updates: Partial<ProposedTicket>) => void;
  localModelIds: string[];
}) {
  const [newFile, setNewFile] = useState('');
  const model = getModelMeta(ticket.assignedModel);

  function addFile() {
    const v = newFile.trim();
    if (v) {
      onChange({ contextFiles: [...ticket.contextFiles, { path: v, reason: '' }] });
      setNewFile('');
    }
  }

  return (
    <div className={`rounded-xl border transition-all ${
      ticket.included
        ? 'border-border bg-card'
        : 'border-border/40 bg-muted/30 opacity-50'
    }`}>
      {/* Card header row */}
      <div className="flex items-start gap-3 px-4 py-3">
        <button
          onClick={() => onChange({ included: !ticket.included })}
          className="mt-0.5 shrink-0 text-primary"
          title={ticket.included ? 'Exclude from creation' : 'Include in creation'}
        >
          {ticket.included
            ? <CheckSquare size={16} className="text-primary" />
            : <Square size={16} className="text-muted-foreground" />
          }
        </button>

        <div className="flex-1 min-w-0 space-y-2">
          {/* Title */}
          <input
            value={ticket.title}
            onChange={(e) => onChange({ title: e.target.value })}
            disabled={!ticket.included}
            className="w-full text-sm font-medium bg-transparent border-0 outline-none focus:bg-muted/50 rounded px-1 -ml-1 py-0.5 text-foreground disabled:text-muted-foreground placeholder:text-muted-foreground"
            placeholder="Ticket title…"
          />

          {/* Type + model row */}
          <div className="flex items-center gap-2 flex-wrap">
            <select
              value={ticket.type}
              onChange={(e) => onChange({ type: e.target.value as TicketType })}
              disabled={!ticket.included}
              className={`text-[11px] px-1.5 py-0.5 rounded border font-medium bg-background focus:outline-none ${TYPE_CLASSES[ticket.type]}`}
            >
              {ALL_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>

            <select
              value={ticket.assignedModel}
              onChange={(e) => onChange({ assignedModel: e.target.value as AssignedModel })}
              disabled={!ticket.included}
              className={`text-[11px] px-1.5 py-0.5 rounded border font-medium bg-background focus:outline-none ${model.pillClass}`}
            >
              {localModelIds.map((m) => (
                <option key={m} value={m}>{getModelMeta(m).label}</option>
              ))}
            </select>
          </div>

          {/* Context files */}
          <div>
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1 flex items-center gap-1">
              <FileCode size={9} /> Target files
            </p>
            <div className="flex flex-wrap gap-1 mb-1">
              {ticket.contextFiles.map((f) => (
                <span key={f.path} className="inline-flex items-center gap-1 font-mono text-[11px] px-1.5 py-0.5 rounded bg-muted border border-border text-foreground">
                  {f.path}
                  {ticket.included && (
                    <button
                      onClick={() => onChange({ contextFiles: ticket.contextFiles.filter((x) => x.path !== f.path) })}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X size={9} />
                    </button>
                  )}
                </span>
              ))}
            </div>
            {ticket.included && (
              <div className="flex items-center gap-1">
                <input
                  value={newFile}
                  onChange={(e) => setNewFile(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addFile(); } }}
                  placeholder="+ Add file path…"
                  className="font-mono text-[11px] bg-transparent text-muted-foreground placeholder:text-muted-foreground/60 outline-none focus:text-foreground"
                />
              </div>
            )}
          </div>

          {/* DoD collapsible */}
          <button
            onClick={() => onChange({ dodExpanded: !ticket.dodExpanded })}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
          >
            {ticket.dodExpanded
              ? <ChevronDown size={11} />
              : <ChevronRight size={11} />
            }
            Definition of done
            <span className="text-muted-foreground/60">
              ({ticket.definitionOfDone.tests.length} test cmd{ticket.definitionOfDone.tests.length !== 1 ? 's' : ''}
              {' · '}
              {ticket.definitionOfDone.acceptanceCriteria.length} criteri{ticket.definitionOfDone.acceptanceCriteria.length !== 1 ? 'a' : 'on'})
            </span>
          </button>

          {ticket.dodExpanded && (
            <div className="space-y-2 pl-3 border-l-2 border-border">
              <div>
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">Test commands</p>
                {ticket.definitionOfDone.tests.map((cmd, i) => (
                  <div key={i} className="font-mono text-[11px] bg-muted px-2 py-1 rounded text-foreground mb-0.5">{cmd}</div>
                ))}
              </div>
              <div>
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground mb-1">Acceptance criteria</p>
                {ticket.definitionOfDone.acceptanceCriteria.map((ac, i) => (
                  <div key={i} className="flex items-start gap-1 text-[11px] mb-0.5">
                    <Circle size={8} className="mt-1 shrink-0 text-muted-foreground" />
                    <span>{ac}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {!ticket.included && (
        <div className="px-4 pb-2 flex items-center gap-1 text-[11px] text-muted-foreground">
          <X size={10} /> Excluded — won't be created
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

interface Props {
  onClose: () => void;
  onAddTickets: (tickets: Ticket[]) => void;
  onAddRequirement: (req: RequirementSource) => void;
  requirementCount: number;
  currentTicketCount: number;
  onDecomposeRequirement?: (payload: BackendRequirementDecomposeRequest) => Promise<{
    requirement_id: string;
    requirement: {
      id: string;
      prompt: string;
      repo: string;
      branch: string;
      scope_paths: string[];
      constraints: string[];
      priority: Priority;
      acceptance_notes: string;
      created_at?: string;
    };
    proposed_tickets: BackendTicket[];
  }>;
  onConfirmRequirement?: (requirementId: string, tickets: BackendTicket[], projectId?: string) => Promise<{
    tickets: BackendTicket[];
  }>;
  onDiscardRequirement?: (requirementId: string) => Promise<void>;
  projects?: Project[];
  selectedProjectId?: string;
  onSelectProject?: (projectId: string) => void;
  onGetProjectConventions?: (projectId: string) => Promise<BackendProjectConventions>;
  localModelIds?: string[];
  projectPathReady?: boolean;
  onDecomposeError?: (message: string) => void;
  usingMockData?: boolean;
}

type Step = 1 | 'decomposing' | 2 | 3;

const DEFAULT_FORM: ComposeForm = {
  projectId: 'default',
  prompt: '',
  repo: 'auth-service',
  branch: 'main',
  scopePaths: [],
  constraints: [],
  priority: 'medium',
  intent: 'feature',
  scale: '',
  granularity: 'balanced',
  allowNewFiles: false,
  testCommand: '',
  acceptanceNotes: '',
};

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    try {
      const parsed = JSON.parse(error.message) as { detail?: unknown };
      if (typeof parsed.detail === 'string') return parsed.detail;
    } catch {
      return error.message;
    }
    return error.message;
  }
  return 'Could not create tickets. Please try again.';
}

export function RequirementComposer({
  onClose,
  onAddTickets,
  onAddRequirement,
  requirementCount,
  currentTicketCount,
  onDecomposeRequirement,
  onConfirmRequirement,
  onDiscardRequirement,
  projects = [],
  selectedProjectId = 'default',
  onSelectProject,
  onGetProjectConventions,
  localModelIds = DEFAULT_LOCAL_MODELS,
  projectPathReady = true,
  onDecomposeError,
  usingMockData = false,
}: Props) {
  const selectedProject = projects.find((project) => project.id === selectedProjectId);
  const [form, setForm] = useState<ComposeForm>({
    ...DEFAULT_FORM,
    projectId: selectedProjectId,
    repo: selectedProject?.name ?? DEFAULT_FORM.repo,
    branch: selectedProject?.defaultBranch ?? DEFAULT_FORM.branch,
  });
  const [step, setStep] = useState<Step>(1);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [proposedTickets, setProposedTickets] = useState<ProposedTicket[]>([]);
  const [createdTickets, setCreatedTickets] = useState<Ticket[]>([]);
  const [reqId, setReqId] = useState('');
  const [confirmError, setConfirmError] = useState('');
  const [decomposeError, setDecomposeError] = useState('');
  const [isConfirming, setIsConfirming] = useState(false);
  const [isLoadingConventions, setIsLoadingConventions] = useState(false);
  const [templates, setTemplates] = useState<RequirementTemplate[]>(usingMockData ? MOCK_REQUIREMENT_TEMPLATES : []);
  const [selectedTemplateId, setSelectedTemplateId] = useState('');
  const [saveTemplateTitle, setSaveTemplateTitle] = useState('');
  const [showSaveTemplate, setShowSaveTemplate] = useState(false);
  const [templateMessage, setTemplateMessage] = useState('');
  const promptRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { promptRef.current?.focus(); }, []);

  useEffect(() => {
    if (usingMockData) {
      setTemplates(MOCK_REQUIREMENT_TEMPLATES);
      return;
    }
    let active = true;
    apiClient
      .listRequirementTemplates()
      .then((items) => {
        if (active) setTemplates(items);
      })
      .catch(() => {
        if (active) setTemplates(MOCK_REQUIREMENT_TEMPLATES);
      });
    return () => {
      active = false;
    };
  }, [usingMockData]);

  function applyTemplate(templateId: string) {
    setSelectedTemplateId(templateId);
    const template = templates.find((item) => item.id === templateId);
    if (!template) return;
    setForm((current) => ({
      ...current,
      prompt: template.prompt,
      scopePaths: [...template.scope_paths],
      constraints: [...template.constraints],
    }));
    setShowAdvanced(template.scope_paths.length > 0 || template.constraints.length > 0);
  }

  async function handleSaveTemplate() {
    const title = saveTemplateTitle.trim() || form.prompt.trim().slice(0, 48);
    if (!title || !form.prompt.trim()) return;
    try {
      const saved = await apiClient.saveRequirementTemplate({
        title,
        prompt: form.prompt.trim(),
        scope_paths: form.scopePaths,
        constraints: form.constraints,
      });
      setTemplates((prev) => [saved, ...prev.filter((item) => item.id !== saved.id)]);
      setSelectedTemplateId(saved.id);
      setShowSaveTemplate(false);
      setSaveTemplateTitle('');
      setTemplateMessage('Template saved.');
    } catch (error) {
      setTemplateMessage(error instanceof Error ? error.message : 'Could not save template.');
    }
  }

  useEffect(() => {
    setForm((current) => ({
      ...current,
      projectId: selectedProjectId,
      repo: selectedProject?.name ?? current.repo,
      branch: selectedProject?.defaultBranch ?? current.branch,
    }));
  }, [selectedProject?.defaultBranch, selectedProject?.name, selectedProjectId]);

  useEffect(() => {
    // Follow the locally-chosen target project (hybrid), not the global board project,
    // so auto-detected test command matches what this requirement will be created in.
    const targetProjectId = form.projectId;
    if (!onGetProjectConventions || !targetProjectId) return;
    let cancelled = false;
    setIsLoadingConventions(true);
    onGetProjectConventions(targetProjectId)
      .then((conventions) => {
        if (cancelled || !conventions.test_command) return;
        setForm((current) => {
          if (current.projectId !== targetProjectId || current.testCommand.trim()) {
            return current;
          }
          return { ...current, testCommand: conventions.test_command };
        });
      })
      .catch(() => {
        return;
      })
      .finally(() => {
        if (!cancelled) setIsLoadingConventions(false);
      });
    return () => {
      cancelled = true;
    };
  }, [onGetProjectConventions, form.projectId]);

  function updateForm<K extends keyof ComposeForm>(key: K, value: ComposeForm[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleDecompose() {
    if (!form.prompt.trim()) return;
    if (!projectPathReady) {
      const message = 'Set a repository path in project settings before decomposing requirements.';
      setDecomposeError(message);
      onDecomposeError?.(message);
      return;
    }
    setConfirmError('');
    setDecomposeError('');
    setStep('decomposing');
    if (!onDecomposeRequirement) {
      const message = 'Backend decompose is not available. Start the API server and try again.';
      setDecomposeError(message);
      onDecomposeError?.(message);
      setStep(1);
      return;
    }

    try {
      const response = await onDecomposeRequirement({
        project_id: form.projectId,
        prompt: form.prompt,
        repo: form.repo,
        branch: form.branch,
        scope_paths: form.scopePaths,
        constraints: form.constraints,
        priority: form.priority,
        intent: form.intent,
        scale: form.scale || null,
        granularity: form.granularity,
        allow_new_files: form.allowNewFiles,
        test_command: form.testCommand,
        attachments: [],
        acceptance_notes: form.acceptanceNotes,
      });
      setReqId(response.requirement_id);
      setProposedTickets(
        response.proposed_tickets.map((ticket, index) => {
          const uiTicket = toUiTicket(ticket);
          return {
            tempId: `draft-${index + 1}`,
            title: uiTicket.title,
            type: uiTicket.type,
            priority: uiTicket.priority,
            assignedModel: uiTicket.assignedModel,
            contextFiles: uiTicket.contextFiles,
            definitionOfDone: uiTicket.definitionOfDone,
            included: true,
            dodExpanded: false,
            backendTicket: ticket,
          };
        }),
      );
      onAddRequirement({
        id: response.requirement.id,
        projectId: response.requirement.project_id,
        prompt: response.requirement.prompt,
        repo: response.requirement.repo,
        branch: response.requirement.branch,
        scopePaths: response.requirement.scope_paths ?? [],
        constraints: response.requirement.constraints ?? [],
        priority: response.requirement.priority,
        intent: response.requirement.intent,
        scale: response.requirement.scale ?? '',
        granularity: response.requirement.granularity,
        allowNewFiles: response.requirement.allow_new_files,
        testCommand: response.requirement.test_command,
        acceptanceNotes: response.requirement.acceptance_notes ?? '',
        createdAt: response.requirement.created_at ?? new Date().toISOString(),
        cloudInputTokens: response.requirement.cloud_input_tokens,
        cloudOutputTokens: response.requirement.cloud_output_tokens,
        cloudCostUsd: response.requirement.cloud_cost_usd,
      });
      setStep(2);
    } catch (error) {
      const message = errorMessage(error);
      setDecomposeError(message);
      onDecomposeError?.(message);
      setStep(1);
    }
  }

  function updateProposed(tempId: string, updates: Partial<ProposedTicket>) {
    setConfirmError('');
    setProposedTickets((prev) => prev.map((t) => t.tempId === tempId ? { ...t, ...updates } : t));
  }

  async function handleApproveCreate() {
    setConfirmError('');
    const now = new Date().toLocaleTimeString('en-US', { hour12: false });
    const id = reqId || `R-${String(requirementCount + 1).padStart(3, '0')}`;
    setReqId(id);

    const req: RequirementSource = {
      id,
      projectId: form.projectId,
      prompt: form.prompt,
      repo: form.repo,
      branch: form.branch,
      scopePaths: form.scopePaths,
      constraints: form.constraints,
      priority: form.priority,
      intent: form.intent,
      scale: form.scale,
      granularity: form.granularity,
      allowNewFiles: form.allowNewFiles,
      testCommand: form.testCommand,
      acceptanceNotes: form.acceptanceNotes,
      createdAt: now,
    };

    const included = proposedTickets.filter((t) => t.included);
    if (onConfirmRequirement) {
      const backendTickets = included
        .map((ticket) => {
          if (!ticket.backendTicket) return undefined;
          // The card's edits only update local state; carry the user's title,
          // type, and model choices into the confirm payload so they aren't
          // reset to the decompose-time defaults.
          return {
            ...ticket.backendTicket,
            title: ticket.title,
            type: ticket.type,
            execution: {
              ...ticket.backendTicket.execution,
              assigned_model: ticket.assignedModel,
            },
          };
        })
        .filter((ticket): ticket is BackendTicket => Boolean(ticket));
      if (backendTickets.length > 0) {
        setIsConfirming(true);
        try {
          const confirmed = await onConfirmRequirement(id, backendTickets, form.projectId);
          const uiTickets = confirmed.tickets.map(toUiTicket);
          onAddTickets(uiTickets);
          setCreatedTickets(uiTickets);
          // Switch the board to the project we just created in, so the new
          // tickets are visible once this composer closes.
          onSelectProject?.(form.projectId);
          setStep(3);
        } catch (error) {
          setConfirmError(errorMessage(error));
          setStep(2);
        } finally {
          setIsConfirming(false);
        }
        return;
      }
    }
    onAddRequirement(req);
    let baseId = 16 + currentTicketCount - 6;

    const tickets: Ticket[] = included.map((pt, i) => ({
      id: `T-${String(baseId + i).padStart(3, '0')}`,
      title: pt.title,
      type: pt.type,
      status: 'Backlog' as TicketStatus,
      priority: pt.priority,
      assignedModel: pt.assignedModel,
      retryCount: 0,
      retryBudget: 3,
      testStatus: 'none' as TestStatus,
      contextFiles: pt.contextFiles,
      definitionOfDone: pt.definitionOfDone,
      needsApproval: true,
      requirementId: id,
      agentLog: [
        {
          time: now,
          level: 'info' as LogLevel,
          message: `Created from ${id} — approved by Product Owner`,
        },
      ],
    }));

    onAddTickets(tickets);
    setCreatedTickets(tickets);
    onSelectProject?.(form.projectId);
    setStep(3);
  }

  const stepNum: 1 | 2 | 3 = step === 'decomposing' ? 1 : (step as 1 | 2 | 3);
  const includedCount = proposedTickets.filter((t) => t.included).length;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/30 dark:bg-black/50 z-50 backdrop-blur-[2px]"
        onClick={step !== 'decomposing' ? onClose : undefined}
      />

      {/* Composer panel */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-6 pointer-events-none">
        <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-3xl max-h-[92vh] flex flex-col pointer-events-auto overflow-hidden">

          {/* ── Header ─────────────────────────────────────── */}
          <div className="flex items-center gap-3 px-5 py-4 border-b border-border shrink-0">
            <Sparkles size={15} className="text-amber-500 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold">Requirement Composer</span>
                {step === 2 && reqId === '' && (
                  <span className="font-mono text-xs text-muted-foreground">
                    R-{String(requirementCount + 1).padStart(3, '0')} (draft)
                  </span>
                )}
                {step === 3 && (
                  <span className="font-mono text-xs text-emerald-600 dark:text-emerald-400">{reqId}</span>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                {step === 1 || step === 'decomposing'
                  ? 'Describe what you want built — the Tech Lead will decompose it into atomic tickets.'
                  : step === 2
                  ? 'Review the proposed tickets. Edit, exclude, then approve to create.'
                  : 'Tickets created and added to Backlog.'}
              </p>
            </div>
            <StepDots current={stepNum} />
            <button
              onClick={onClose}
              className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors shrink-0"
            >
              <X size={14} />
            </button>
          </div>

          {/* ── Scrollable body ─────────────────────────────── */}
          <div className="flex-1 overflow-y-auto">

            {/* ─ Step 1: Compose ─ */}
            {(step === 1 || step === 'decomposing') && (
              <div className="p-5 space-y-5">

                <div className="flex flex-wrap items-center gap-2">
                  <label className="text-xs font-medium text-muted-foreground flex items-center gap-1">
                    <LayoutTemplate size={12} />
                    Templates
                  </label>
                  <select
                    value={selectedTemplateId}
                    onChange={(event) => {
                      const value = event.target.value;
                      if (!value) {
                        setSelectedTemplateId('');
                        return;
                      }
                      applyTemplate(value);
                    }}
                    disabled={step === 'decomposing'}
                    className="text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 min-w-[180px]"
                  >
                    <option value="">Choose a template…</option>
                    {templates.map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.title}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => setShowSaveTemplate((value) => !value)}
                    disabled={!form.prompt.trim() || step === 'decomposing'}
                    className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
                  >
                    <BookmarkPlus size={11} />
                    Save as template
                  </button>
                </div>

                {showSaveTemplate && (
                  <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/20 px-3 py-2">
                    <input
                      type="text"
                      value={saveTemplateTitle}
                      onChange={(event) => setSaveTemplateTitle(event.target.value)}
                      placeholder="Template title"
                      className="flex-1 min-w-[160px] text-xs bg-background border border-border rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring"
                    />
                    <button
                      type="button"
                      onClick={() => void handleSaveTemplate()}
                      className="text-xs px-2.5 py-1.5 rounded bg-primary text-primary-foreground hover:opacity-90"
                    >
                      Save
                    </button>
                  </div>
                )}

                {templateMessage && (
                  <p className="text-[11px] text-muted-foreground">{templateMessage}</p>
                )}

                {/* Prompt (main field) */}
                <div>
                  <label className="text-xs font-semibold text-foreground mb-1.5 flex items-center gap-1">
                    Prompt
                    <span className="text-red-500">*</span>
                  </label>
                  <textarea
                    ref={promptRef}
                    value={form.prompt}
                    onChange={(e) => updateForm('prompt', e.target.value)}
                    disabled={step === 'decomposing'}
                    rows={5}
                    placeholder="Describe the feature or change in plain language… e.g. &quot;Harden password storage: switch to bcrypt and reject weak passwords on login.&quot;"
                    className="w-full text-sm bg-muted/40 border border-border rounded-xl px-3.5 py-3 resize-none focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50 placeholder:text-muted-foreground disabled:opacity-50 leading-relaxed"
                  />
                </div>

                {!projectPathReady && (
                  <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                    Set a repository path in project settings before decomposing requirements.
                  </div>
                )}

                {decomposeError && (
                  <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/40 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                    {decomposeError}
                  </div>
                )}

                {/* Advanced options toggle — keeps step 1 to plain-language essentials by default */}
                <button
                  type="button"
                  onClick={() => setShowAdvanced((v) => !v)}
                  disabled={step === 'decomposing'}
                  className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
                >
                  {showAdvanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  Advanced options
                  <span className="text-muted-foreground/60 font-normal">
                    (project, scope, constraints, repo &amp; branch, granularity)
                  </span>
                </button>

                {showAdvanced && (
                <div className="space-y-5">
                {/* Project + planner controls */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground mb-1 block">Project</label>
                    <select
                      value={form.projectId}
                      onChange={(e) => {
                        // Hybrid: the project picked here only targets this requirement.
                        // The global board project is switched on successful create
                        // (see handleApproveCreate), so we don't yank the board mid-edit.
                        const nextProject = projects.find((project) => project.id === e.target.value);
                        updateForm('projectId', e.target.value);
                        if (nextProject) {
                          updateForm('repo', nextProject.name);
                          updateForm('branch', nextProject.defaultBranch);
                        }
                      }}
                      disabled={step === 'decomposing'}
                      className="w-full text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                    >
                      {(projects.length
                        ? projects
                        : [{
                          id: form.projectId,
                          name: form.repo,
                          path: '',
                          defaultBranch: form.branch,
                          env: {},
                          setupCmd: '',
                          cleanupCmd: '',
                        }]).map((project) => (
                        <option key={project.id} value={project.id}>{project.name}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground mb-1 block">Intent</label>
                    <select
                      value={form.intent}
                      onChange={(e) => updateForm('intent', e.target.value as RequirementIntent)}
                      disabled={step === 'decomposing'}
                      className="w-full text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                    >
                      {(['feature', 'bugfix', 'refactor', 'chore', 'spike'] as RequirementIntent[]).map((intent) => (
                        <option key={intent} value={intent}>{intent}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground mb-1 block">Scale</label>
                    <select
                      value={form.scale}
                      onChange={(e) => updateForm('scale', e.target.value as RequirementScale)}
                      disabled={step === 'decomposing'}
                      className="w-full text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                    >
                      <option value="">infer</option>
                      {(['small', 'medium', 'large'] as const).map((scale) => (
                        <option key={scale} value={scale}>{scale}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground mb-1 block">Granularity</label>
                    <select
                      value={form.granularity}
                      onChange={(e) => updateForm('granularity', e.target.value as RequirementGranularity)}
                      disabled={step === 'decomposing'}
                      className="w-full text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                    >
                      {(['coarse', 'balanced', 'fine'] as RequirementGranularity[]).map((granularity) => (
                        <option key={granularity} value={granularity}>{granularity}</option>
                      ))}
                    </select>
                  </div>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground pt-6">
                    <input
                      type="checkbox"
                      checked={form.allowNewFiles}
                      onChange={(e) => updateForm('allowNewFiles', e.target.checked)}
                      disabled={step === 'decomposing'}
                      className="h-3.5 w-3.5"
                    />
                    Allow new files
                  </label>
                </div>

                {/* Target repo + branch */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-muted-foreground mb-1 block">Target repo</label>
                    <input
                      value={form.repo}
                      onChange={(e) => updateForm('repo', e.target.value)}
                      disabled={step === 'decomposing'}
                      className="w-full font-mono text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                      placeholder="auth-service"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-muted-foreground mb-1 block">Branch</label>
                    <input
                      value={form.branch}
                      onChange={(e) => updateForm('branch', e.target.value)}
                      disabled={step === 'decomposing'}
                      className="w-full font-mono text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                      placeholder="main"
                    />
                  </div>
                </div>

                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Test command convention</label>
                  <input
                    value={form.testCommand}
                    onChange={(e) => updateForm('testCommand', e.target.value)}
                    disabled={step === 'decomposing'}
                    className="w-full font-mono text-xs bg-muted/40 border border-border rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
                    placeholder={isLoadingConventions ? 'Detecting project test command...' : 'Auto-detected by backend if empty'}
                  />
                </div>

                {/* Scope paths */}
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Scope (paths)</label>
                  <ChipInput
                    chips={form.scopePaths}
                    onAdd={(v) => updateForm('scopePaths', [...form.scopePaths, v])}
                    onRemove={(v) => updateForm('scopePaths', form.scopePaths.filter((x) => x !== v))}
                    placeholder="auth_service/, tests/auth/** — press Enter to add"
                  />
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Narrows what the Tech Lead sees and decomposes. Leave empty for full-repo scope.
                  </p>
                </div>

                {/* Constraints */}
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Constraints / non-goals</label>
                  <div className="space-y-1.5 mb-1.5">
                    {form.constraints.map((c) => (
                      <div key={c} className="flex items-start gap-2 group">
                        <span className="text-muted-foreground mt-0.5 shrink-0">·</span>
                        <span className="text-xs flex-1">{c}</span>
                        <button
                          onClick={() => updateForm('constraints', form.constraints.filter((x) => x !== c))}
                          className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    ))}
                  </div>
                  <ChipInput
                    chips={[]}
                    onAdd={(v) => updateForm('constraints', [...form.constraints, v])}
                    onRemove={() => {}}
                    placeholder={`e.g. "Don't change public signatures" — press Enter to add`}
                    monoFont={false}
                  />
                </div>
                </div>
                )}

                {/* Priority */}
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1.5 block">Priority</label>
                  <div className="flex rounded-lg border border-border overflow-hidden w-fit">
                    {(['low', 'medium', 'high'] as Priority[]).map((p) => (
                      <button
                        key={p}
                        onClick={() => updateForm('priority', p)}
                        disabled={step === 'decomposing'}
                        className={`px-5 py-1.5 text-xs capitalize transition-colors ${
                          form.priority === p
                            ? 'bg-primary text-primary-foreground'
                            : 'hover:bg-muted text-muted-foreground'
                        }`}
                      >
                        {p}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Acceptance notes */}
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">
                    Acceptance notes
                    <span className="ml-1 text-muted-foreground/60">(optional)</span>
                  </label>
                  <textarea
                    value={form.acceptanceNotes}
                    onChange={(e) => updateForm('acceptanceNotes', e.target.value)}
                    disabled={step === 'decomposing'}
                    rows={2}
                    placeholder="What does 'good' look like at the product level?"
                    className="w-full text-xs bg-muted/40 border border-border rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground disabled:opacity-50"
                  />
                </div>

                {/* Loading overlay */}
                {step === 'decomposing' && (
                  <div className="flex items-center gap-3 p-3 rounded-xl bg-amber-50 dark:bg-amber-950/50 border border-amber-200 dark:border-amber-800">
                    <Loader2 size={14} className="animate-spin text-amber-600 shrink-0" />
                    <div>
                      <p className="text-xs font-medium text-amber-700 dark:text-amber-300">Tech Lead is decomposing…</p>
                      <p className="text-[11px] text-amber-600/80 dark:text-amber-400/80 mt-0.5">
                        Analysing scope, context files, and acceptance notes. This takes a few seconds.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ─ Step 2: Preview ─ */}
            {step === 2 && (
              <div className="p-5 space-y-4">
                {/* Summary header */}
                <div className="rounded-xl bg-muted/40 border border-border px-4 py-3">
                  <p className="text-xs font-medium text-foreground truncate">
                    "{form.prompt.slice(0, 80)}{form.prompt.length > 80 ? '…' : ''}"
                  </p>
                  <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                    <span className="font-mono text-[11px] text-muted-foreground">{form.repo}/{form.branch}</span>
                    <span className="text-muted-foreground/40">·</span>
                    <span className={`text-[11px] font-medium capitalize ${form.priority === 'high' ? 'text-red-600 dark:text-red-400' : form.priority === 'medium' ? 'text-amber-600 dark:text-amber-400' : 'text-zinc-500'}`}>
                      {form.priority} priority
                    </span>
                    <span className="text-muted-foreground/40">·</span>
                    <span className="text-[11px] text-muted-foreground">
                      {form.intent}{form.scale ? `/${form.scale}` : ''} · {form.granularity}
                    </span>
                    {form.scopePaths.length > 0 && (
                      <>
                        <span className="text-muted-foreground/40">·</span>
                        <span className="font-mono text-[11px] text-muted-foreground">{form.scopePaths.join(', ')}</span>
                      </>
                    )}
                  </div>
                </div>

                {/* Not-created-yet notice */}
                <div className="flex items-center gap-2 text-xs text-muted-foreground border border-border rounded-lg px-3 py-2 bg-muted/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
                  <span><strong>These tickets are not created yet.</strong> Review, edit, and exclude any you don't want, then click "Approve &amp; create".</span>
                </div>

                {confirmError && (
                  <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/40 px-3 py-2 text-xs text-red-700 dark:text-red-300">
                    {confirmError}
                  </div>
                )}

                {/* Proposed ticket cards */}
                <div className="space-y-3">
                  {proposedTickets.map((t, i) => (
                    <ProposedTicketCard
                      key={t.tempId}
                      ticket={t}
                      index={i}
                      onChange={(updates) => updateProposed(t.tempId, updates)}
                      localModelIds={localModelIds}
                    />
                  ))}
                </div>

                {includedCount === 0 && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 text-center py-2">
                    All tickets excluded — nothing will be created.
                  </p>
                )}
              </div>
            )}

            {/* ─ Step 3: Created ─ */}
            {step === 3 && (
              <div className="p-5 space-y-4">
                <div className="flex flex-col items-center text-center py-6 gap-3">
                  <div className="w-12 h-12 rounded-full bg-emerald-100 dark:bg-emerald-950 flex items-center justify-center">
                    <CheckCircle2 size={24} className="text-emerald-600 dark:text-emerald-400" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold">{reqId} created</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {createdTickets.length} ticket{createdTickets.length !== 1 ? 's' : ''} added to Backlog · needs approval
                    </p>
                  </div>
                </div>

                <div className="space-y-1.5">
                  {createdTickets.map((t) => (
                    <div key={t.id} className="flex items-center gap-2.5 rounded-lg border border-emerald-200 dark:border-emerald-800 bg-emerald-50/50 dark:bg-emerald-950/20 px-3 py-2">
                      <span className="font-mono text-xs text-muted-foreground shrink-0">{t.id}</span>
                      <span className="text-xs flex-1 truncate">{t.title}</span>
                      <span className={`text-[10px] px-1.5 py-0 rounded-full font-medium ${getModelMeta(t.assignedModel).pillClass}`}>
                        {getModelMeta(t.assignedModel).label}
                      </span>
                      <span className="text-[10px] px-1 py-0 rounded bg-amber-50 text-amber-600 dark:bg-amber-950 dark:text-amber-400 border border-amber-200 dark:border-amber-800 font-medium shrink-0">
                        needs approval
                      </span>
                    </div>
                  ))}
                </div>

              </div>
            )}
          </div>

          {/* ── Footer ─────────────────────────────────────── */}
          {step !== 3 && (
            <div className="shrink-0 border-t border-border px-5 py-4 flex items-center justify-between gap-3">
              {/* Left: secondary */}
              {step === 1 && (
                <button
                  onClick={onClose}
                  className="text-xs px-3 py-1.5 rounded border border-border hover:bg-muted transition-colors"
                >
                  Cancel
                </button>
              )}
              {step === 2 && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setStep(1)}
                    disabled={isConfirming}
                    className="text-xs px-3 py-1.5 rounded border border-border hover:bg-muted transition-colors"
                  >
                    ← Back to edit
                  </button>
                  <button
                    onClick={() => {
                      if (reqId && onDiscardRequirement) {
                        void onDiscardRequirement(reqId);
                      }
                      onClose();
                    }}
                    disabled={isConfirming}
                    className="text-xs px-3 py-1.5 rounded border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 transition-colors"
                  >
                    Discard
                  </button>
                </div>
              )}

              {/* Right: primary */}
              {(step === 1) && (
                <button
                  onClick={handleDecompose}
                  disabled={!form.prompt.trim() || step === 'decomposing' || !projectPathReady}
                  className="flex items-center gap-2 text-xs px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-40 ml-auto"
                >
                  <Sparkles size={12} />
                  Decompose
                </button>
              )}
              {step === 'decomposing' && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground ml-auto">
                  <Loader2 size={12} className="animate-spin" />
                  Tech Lead is decomposing…
                </div>
              )}
              {step === 2 && (
                <button
                  onClick={handleApproveCreate}
                  disabled={includedCount === 0 || isConfirming}
                  className="flex items-center gap-2 text-xs px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-40 ml-auto"
                >
                  {isConfirming ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
                  {isConfirming ? 'Creating…' : `Approve & create ${includedCount > 0 ? `(${includedCount})` : ''}`}
                </button>
              )}
            </div>
          )}

          {step === 3 && (
            <div className="shrink-0 border-t border-border px-5 py-4 flex items-center justify-end">
              <button
                onClick={onClose}
                className="flex items-center gap-2 text-xs px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
              >
                <CheckCircle2 size={12} />
                Done — view in Backlog
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
