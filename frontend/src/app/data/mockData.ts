import type { Ticket, LogLevel, RequirementSource, ChatMessage, ChatAttachment, CloudModel } from '../types';

export const INITIAL_CHAT_ATTACHMENTS: ChatAttachment[] = [
  {
    id: 'ATT-mock-notes',
    filename: 'api-notes.txt',
    mime: 'text/plain',
    size: 1280,
    kind: 'file',
    stored_path: 'mock/api-notes.txt',
  },
];

export const INITIAL_CHAT_MESSAGES: ChatMessage[] = [
  {
    id: 'msg-001',
    project_id: 'default',
    role: 'agent',
    text: 'Welcome to HAAO. Tell me what you want built and I will file proposals into your backlog — nothing moves to Ready until you approve.',
    segment_id: 'seg-default',
    created_at: '2026-06-25T09:00:00.000Z',
  },
  {
    id: 'msg-002',
    project_id: 'default',
    role: 'user',
    text: 'Harden password storage — switch to bcrypt and reject weak passwords at the API.',
    segment_id: 'seg-default',
    created_at: '2026-06-25T09:05:00.000Z',
    attachment_ids: ['ATT-mock-notes'],
    attachments: [INITIAL_CHAT_ATTACHMENTS[0]],
  },
  {
    id: 'msg-003',
    project_id: 'default',
    role: 'agent',
    text: 'I heard 1 piece of work and filed it as a proposal in Backlog.',
    requirement_id: 'R-006',
    segment_id: 'seg-default',
    created_at: '2026-06-25T09:05:04.000Z',
  },
  {
    id: 'msg-004',
    project_id: 'default',
    role: 'system_report',
    text: 'T-012 done — moved to Review',
    ticket_id: 'T-012',
    report_kind: 'done',
    segment_id: 'seg-default',
    created_at: '2026-06-25T09:31:30.000Z',
  },
  {
    id: 'msg-005',
    project_id: 'default',
    role: 'system_report',
    text: 'T-011 needs you — awaiting acceptance (Gate 2)',
    ticket_id: 'T-011',
    report_kind: 'needs_you',
    segment_id: 'seg-default',
    created_at: '2026-06-25T08:55:38.000Z',
  },
];

export const INITIAL_REQUIREMENTS: RequirementSource[] = [
  {
    id: 'R-006',
    prompt:
      'Harden password storage and make login reject weak passwords. Switch to bcrypt with a proper work factor and add input validation so short or common passwords are rejected at the API level.',
    repo: 'auth-service',
    branch: 'main',
    scopePaths: ['auth_service/', 'tests/auth/'],
    constraints: [
      "Don't change public API signatures",
      'No new dependencies except bcrypt',
    ],
    priority: 'high',
    acceptanceNotes:
      'Security team sign-off required. All existing tests must still pass after the migration.',
    createdAt: '09:25:00',
    cloudCostUsd: 0.0142,
  },
];

export const INITIAL_CLOUD_MODELS: CloudModel[] = [
  {
    id: 'anthropic:claude-sonnet-4-6',
    label: 'Claude (Anthropic) · default',
    provider: 'anthropic',
    model_id: 'claude-sonnet-4-6',
    key_configured: false,
    deletable: false,
  },
  {
    id: 'openai:gpt-4o-mini',
    label: 'OpenAI · gpt-4o-mini',
    provider: 'openai',
    model_id: 'gpt-4o-mini',
    key_configured: true,
    deletable: true,
  },
];

// Indices into LIVE_LOG_STEPS where major state changes happen
export const SIM_REVIEW_STEP = 5;       // "Moving T-012 → Review"
export const SIM_AUDIT_DONE_STEP = 9;   // "Technical audit passed"

export const LIVE_LOG_STEPS: Array<{ level: LogLevel; message: string }> = [
  // Tests passing (steps 0-4)
  { level: 'info', message: 'tests/test_crypto.py::test_hash_returns_bytes PASSED' },
  { level: 'info', message: 'tests/test_crypto.py::test_verify_correct_password PASSED' },
  { level: 'info', message: 'tests/test_crypto.py::test_verify_wrong_password PASSED' },
  { level: 'info', message: 'tests/test_crypto.py::test_hash_strength PASSED' },
  { level: 'success', message: '4 passed in 1.24s — all tests green ✓' },
  // Move to Review (step 5)
  { level: 'success', message: 'Orchestrator: T-012 → Review (auto-transition)' },
  // Claude TL technical audit (steps 6-9)
  { level: 'info', message: 'Claude · Tech Lead: initiating technical audit…' },
  { level: 'info', message: 'Checking pytest tests/test_crypto.py -v → 4/4 passed ✓' },
  { level: 'info', message: 'Checking acceptance criteria: all 4 items met ✓' },
  { level: 'success', message: 'Technical audit passed → Awaiting acceptance (Gate 2)' },
];

export const INITIAL_TICKETS: Ticket[] = [
  {
    id: 'T-014',
    title: 'Add email format validation',
    type: 'feature',
    status: 'Backlog',
    priority: 'medium',
    assignedModel: 'qwen3-coder-next',
    retryCount: 0,
    retryBudget: 3,
    testStatus: 'none',
    needsApproval: true,
    contextFiles: [
      { path: 'auth_service/validators.py', reason: 'Will hold new validation logic' },
      { path: 'auth_service/routes/register.py', reason: 'Entry point calling the validator' },
    ],
    definitionOfDone: {
      tests: ['pytest tests/test_validators.py::test_email_format -v'],
      acceptanceCriteria: [
        'Rejects emails without @ symbol with 422',
        'Rejects emails with invalid TLD',
        'Accepts valid RFC-5322 email addresses',
        'Returns structured error JSON on failure',
      ],
    },
    agentLog: [
      { time: '09:12:00', level: 'info', message: 'Requirement submitted by Product Owner' },
      { time: '09:12:04', level: 'info', message: 'Claude · Tech Lead: decomposing requirement…' },
      { time: '09:12:07', level: 'info', message: 'Decomposed into 3 sub-tickets (T-014, T-016, T-017)' },
      { time: '09:12:08', level: 'warn', message: 'Awaiting Product Owner approval before dispatch (Gate 1)' },
    ],
  },
  {
    id: 'T-015',
    title: 'Password reset token expiry',
    type: 'feature',
    status: 'Ready',
    priority: 'high',
    assignedModel: 'qwen3-coder-next',
    retryCount: 0,
    retryBudget: 3,
    testStatus: 'none',
    readyState: 'conflict',
    conflictNote: 'Waiting — overlaps files with T-012',
    contextFiles: [
      { path: 'auth_service/utils/crypto.py', reason: 'Shares crypto module with in-flight work' },
      { path: 'auth_service/models/reset_token.py', reason: 'Token model with expires_at field' },
    ],
    definitionOfDone: {
      tests: [
        'pytest tests/test_password_reset.py',
        'pytest tests/test_password_reset.py::test_expired_token_rejected',
      ],
      acceptanceCriteria: [
        'Token expires exactly 15 minutes after creation',
        'Expired token returns 410 Gone',
        'Token is invalidated after single use',
        'Expiry enforced at DB and application layer',
      ],
    },
    agentLog: [
      { time: '09:15:22', level: 'info', message: 'Ticket created by Claude · Tech Lead from sprint planning' },
    ],
  },
  {
    id: 'T-012',
    title: 'Switch hash_password to bcrypt',
    type: 'refactor',
    status: 'In Progress',
    priority: 'high',
    assignedModel: 'qwen3-coder-next',
    retryCount: 1,
    retryBudget: 3,
    testStatus: 'testing',
    autoDispatched: true,
    lease: { workerId: 'worker-1', expiresAt: new Date(Date.now() + 120_000).toISOString() },
    requirementId: 'R-006',
    contextFiles: [
      { path: 'auth_service/utils/crypto.py', reason: 'Contains hash_password (SHA-256 → bcrypt)' },
      { path: 'auth_service/models/user.py', reason: 'Calls hash_password on User.save()' },
      { path: 'requirements.txt', reason: 'bcrypt==4.1.2 must be added' },
      { path: 'tests/test_crypto.py', reason: 'Existing hash tests to update' },
    ],
    definitionOfDone: {
      tests: [
        'pytest tests/test_crypto.py -v',
        'pytest tests/test_auth_flow.py::test_login_with_bcrypt_hash',
      ],
      acceptanceCriteria: [
        'hash_password uses bcrypt with work factor ≥ 12',
        'verify_password returns True for correct password',
        'Legacy SHA-256 users prompted to reset on next login',
        'No plain-text passwords appear in any log',
      ],
    },
    agentLog: [
      { time: '09:28:00', level: 'info', message: 'Orchestrator: auto-dispatched to qwen3-coder-next' },
      { time: '09:31:00', level: 'info', message: 'Task assigned — reading auth_service/utils/crypto.py' },
      { time: '09:31:02', level: 'info', message: 'Read 84 lines — found hash_password (SHA-256, no salt)' },
      { time: '09:31:05', level: 'info', message: 'Reading requirements.txt — bcrypt not listed' },
      { time: '09:31:07', level: 'info', message: 'Appending bcrypt==4.1.2 to requirements.txt' },
      { time: '09:31:09', level: 'info', message: 'Rewriting hash_password → bcrypt.hashpw(pwd.encode(), bcrypt.gensalt())' },
      { time: '09:31:11', level: 'info', message: 'Updating verify_password → bcrypt.checkpw(pwd.encode(), stored_hash)' },
      { time: '09:31:14', level: 'info', message: 'Running: pytest tests/test_crypto.py' },
      { time: '09:31:18', level: 'error', message: "FAILED test_crypto.py::test_hash_password — TypeError: bcrypt.hashpw() missing 'salt' argument" },
      { time: '09:31:19', level: 'warn', message: 'Orchestrator: 1 test failed — auto-retry 1/3' },
      { time: '09:31:21', level: 'info', message: 'Root cause: gensalt() not called before hashpw; salt kwarg required' },
      { time: '09:31:23', level: 'info', message: 'Fix: salt = bcrypt.gensalt(rounds=12); bcrypt.hashpw(pwd.encode(), salt)' },
      { time: '09:31:25', level: 'info', message: 'Re-running: pytest tests/test_crypto.py -v' },
    ],
  },
  {
    id: 'T-011',
    title: 'JWT expiry config',
    type: 'feature',
    status: 'Awaiting acceptance',
    priority: 'medium',
    assignedModel: 'qwen3-coder-next',
    retryCount: 0,
    retryBudget: 3,
    testStatus: 'pass',
    awaitingAcceptance: true,
    autoDispatched: true,
    diffStats: {
      files_touched: 2,
      lines_added: 18,
      lines_removed: 4,
      out_of_scope_files: [],
    },
    reasonerPromptVersion: 'coder@v1.8.0',
    lastRunModelId: 'qwen3-coder-next',
    technicalAudit: {
      verdict:
        'All acceptance criteria met. Code is clean, env-var-driven config is idiomatic. No security concerns. Ready for Product Owner acceptance.',
      checkedCriteria: [
        'pytest tests/test_token_service.py -v → 5 passed ✓',
        'JWT_EXPIRY_SECONDS configurable via os.getenv ✓',
        'Default expiry is 3600s (1 hour) ✓',
        'Expired JWTs return 401 Unauthorized ✓',
        'No hardcoded secrets or credentials in diff ✓',
      ],
      auditor: 'Claude · Tech Lead',
    },
    contextFiles: [
      { path: 'auth_service/config.py', reason: 'JWT_EXPIRY_SECONDS setting' },
      { path: 'auth_service/services/token_service.py', reason: 'Creates and decodes JWTs' },
    ],
    definitionOfDone: {
      tests: [
        'pytest tests/test_token_service.py -v',
        'pytest tests/test_token_service.py::test_expired_jwt_rejected',
      ],
      acceptanceCriteria: [
        'JWT_EXPIRY_SECONDS configurable via env var',
        'Default expiry is 3600s (1 hour)',
        'Expired JWTs return 401 Unauthorized',
      ],
    },
    agentLog: [
      { time: '08:54:00', level: 'info', message: 'Orchestrator: auto-dispatched to qwen3-coder-next' },
      { time: '08:55:10', level: 'info', message: 'Task assigned to qwen3-coder-next' },
      { time: '08:55:12', level: 'info', message: 'Reading config.py — JWT_EXPIRY hardcoded to 86400' },
      { time: '08:55:15', level: 'info', message: 'Refactoring to os.getenv("JWT_EXPIRY_SECONDS", "3600")' },
      { time: '08:55:18', level: 'info', message: 'Updating token_service.py to use config.JWT_EXPIRY_SECONDS' },
      { time: '08:55:22', level: 'info', message: 'Running: pytest tests/test_token_service.py -v' },
      { time: '08:55:26', level: 'success', message: '5 passed in 0.87s' },
      { time: '08:55:27', level: 'success', message: 'Orchestrator: T-011 → Review (auto-transition)' },
      { time: '08:55:30', level: 'info', message: 'Claude · Tech Lead: initiating technical audit…' },
      { time: '08:55:34', level: 'info', message: 'Checking pytest tests/test_token_service.py -v → 5/5 passed ✓' },
      { time: '08:55:36', level: 'info', message: 'Checking acceptance criteria: all 3 items met ✓' },
      { time: '08:55:38', level: 'success', message: 'Technical audit passed → Awaiting acceptance (Gate 2)' },
    ],
  },
  {
    id: 'T-009',
    title: 'Login endpoint skeleton',
    type: 'feature',
    status: 'Done',
    priority: 'high',
    assignedModel: 'qwen3.6-35b-a3b',
    retryCount: 0,
    retryBudget: 3,
    testStatus: 'pass',
    autoDispatched: true,
    contextFiles: [
      { path: 'auth_service/routes/login.py', reason: 'POST /auth/login endpoint' },
      { path: 'auth_service/schemas/login_schema.py', reason: 'Request/response schema' },
    ],
    definitionOfDone: {
      tests: ['pytest tests/test_login.py -v'],
      acceptanceCriteria: [
        'POST /auth/login accepts email + password',
        'Returns JWT on success',
        'Returns 401 on bad credentials',
      ],
    },
    agentLog: [
      { time: '08:10:00', level: 'info', message: 'Orchestrator: auto-dispatched to qwen3.6-35b-a3b' },
      { time: '08:10:05', level: 'info', message: 'Scaffolded login.py with FastAPI router' },
      { time: '08:10:22', level: 'success', message: '3 passed in 0.44s' },
      { time: '08:10:24', level: 'success', message: 'Technical audit passed — Claude · Tech Lead' },
      { time: '08:10:30', level: 'success', message: 'Accepted by Product Owner — merged to main' },
    ],
  },
  {
    id: 'T-010',
    title: 'User model fields',
    type: 'feature',
    status: 'Done',
    priority: 'medium',
    assignedModel: 'gemma-4-26b-a4b',
    retryCount: 0,
    retryBudget: 3,
    testStatus: 'pass',
    autoDispatched: true,
    contextFiles: [
      { path: 'auth_service/models/user.py', reason: 'SQLAlchemy User model' },
      { path: 'alembic/versions/0001_user_model.py', reason: 'Initial migration' },
    ],
    definitionOfDone: {
      tests: ['pytest tests/test_user_model.py -v'],
      acceptanceCriteria: [
        'User has: id, email, hashed_password, created_at, is_active',
        'Email field is unique and indexed',
        'Migration runs cleanly on fresh DB',
      ],
    },
    agentLog: [
      { time: '07:45:00', level: 'info', message: 'Orchestrator: auto-dispatched to gemma-4-26b-a4b' },
      { time: '07:45:20', level: 'info', message: 'Created User SQLAlchemy model with required fields' },
      { time: '07:45:35', level: 'success', message: '4 passed in 0.31s' },
      { time: '07:45:40', level: 'success', message: 'Technical audit passed — Claude · Tech Lead' },
      { time: '07:45:45', level: 'success', message: 'Accepted by Product Owner — merged to main' },
    ],
  },
  {
    id: 'T-018',
    title: 'OAuth provider integration (too large)',
    type: 'feature',
    status: 'Split',
    priority: 'high',
    assignedModel: 'qwen3-coder-next',
    retryCount: 2,
    retryBudget: 3,
    testStatus: 'fail',
    childTicketIds: ['T-019', 'T-020'],
    splitFeedback: 'Split into token exchange vs callback route — original scope was too broad.',
    splitAt: '2026-06-28T14:22:00.000Z',
    contextFiles: [
      { path: 'auth_service/oauth/', reason: 'Full OAuth module' },
    ],
    definitionOfDone: {
      tests: ['pytest tests/test_oauth.py -v'],
      acceptanceCriteria: ['Google OAuth login works end-to-end'],
    },
    agentLog: [
      { time: '14:20:00', level: 'warn', message: 'Product Owner split ticket into T-019, T-020' },
    ],
  },
  {
    id: 'T-019',
    title: 'OAuth token exchange handler',
    type: 'feature',
    status: 'Ready',
    priority: 'high',
    assignedModel: 'qwen3-coder-next',
    retryCount: 0,
    retryBudget: 3,
    testStatus: 'none',
    splitFrom: 'T-018',
    readyState: 'ready',
    graphReady: true,
    contextFiles: [
      { path: 'auth_service/oauth/token.py', reason: 'Exchange code for tokens' },
    ],
    definitionOfDone: {
      tests: ['pytest tests/test_oauth_token.py -v'],
      acceptanceCriteria: ['Exchanges auth code for access token'],
    },
    agentLog: [
      { time: '14:22:01', level: 'info', message: 'Created by split from T-018' },
    ],
  },
  {
    id: 'T-020',
    title: 'OAuth callback route',
    type: 'feature',
    status: 'Backlog',
    priority: 'high',
    assignedModel: 'qwen3-coder-next',
    retryCount: 0,
    retryBudget: 3,
    testStatus: 'none',
    splitFrom: 'T-018',
    dependsOn: ['T-019'],
    readyState: 'waiting_dependencies',
    graphBlocked: true,
    contextFiles: [
      { path: 'auth_service/routes/oauth_callback.py', reason: 'Browser redirect handler' },
    ],
    definitionOfDone: {
      tests: ['pytest tests/test_oauth_callback.py -v'],
      acceptanceCriteria: ['GET /auth/callback validates state param'],
    },
    agentLog: [
      { time: '14:22:02', level: 'info', message: 'Created by split from T-018' },
    ],
  },
  {
    id: 'T-021',
    title: 'Legacy SAML bridge spike',
    type: 'chore',
    status: 'Abandoned',
    priority: 'low',
    assignedModel: 'Claude · Tech Lead',
    retryCount: 1,
    retryBudget: 3,
    testStatus: 'none',
    abandonReason: 'Out of scope for this sprint — SAML deferred to Q3.',
    abandonedAt: '2026-06-27T11:05:00.000Z',
    contextFiles: [
      { path: 'auth_service/saml/', reason: 'Experimental SAML stub' },
    ],
    definitionOfDone: {
      tests: ['pytest tests/test_saml.py -v'],
      acceptanceCriteria: ['Spike documents SAML feasibility'],
    },
    agentLog: [
      { time: '11:05:00', level: 'warn', message: 'Product Owner abandoned ticket: Out of scope for this sprint' },
    ],
  },
  {
    id: 'T-013',
    title: 'Rate limit login attempts',
    type: 'feature',
    status: 'Blocked',
    priority: 'medium',
    assignedModel: 'gemma-4-26b-a4b',
    retryCount: 3,
    retryBudget: 3,
    testStatus: 'fail',
    diffStats: {
      files_touched: 3,
      lines_added: 42,
      lines_removed: 6,
      out_of_scope_files: ['README.md'],
    },
    reasonerPromptVersion: 'coder@v1.7.2',
    lastRunModelId: 'gemma-4-26b-a4b',
    contextFiles: [
      { path: 'auth_service/routes/login.py', reason: 'Apply rate limiting middleware' },
    ],
    definitionOfDone: {
      tests: ['pytest tests/test_rate_limit.py -v'],
      acceptanceCriteria: ['429 after 5 failed attempts per IP'],
    },
    agentLog: [
      { time: '10:12:00', level: 'error', message: 'Patch touched README.md — out of scope, run rolled back' },
    ],
  },
];
