export type BackendTicketStatus =
  | 'backlog'
  | 'ready'
  | 'in_progress'
  | 'testing'
  | 'diff_pending'
  | 'review'
  | 'awaiting_acceptance'
  | 'done'
  | 'blocked'
  | 'abandoned'
  | 'split';

export interface DiffStats {
  files_touched: number;
  lines_added: number;
  lines_removed: number;
  out_of_scope_files: string[];
}

export interface BackendTicket {
  id: string;
  title: string;
  type: 'feature' | 'bugfix' | 'refactor' | 'test' | 'chore';
  status: BackendTicketStatus;
  priority?: 'low' | 'medium' | 'high';
  task: {
    description?: string;
    target_files: string[];
  };
  context: {
    files: Array<{
      path: string;
      reason?: string;
      truncated?: boolean;
      content: string;
    }>;
  };
  definition_of_done: {
    tests: Array<{
      command: string;
      expect: 'pass' | 'fail';
      timeout_sec: number;
    }>;
    acceptance_criteria: string[];
  };
  execution: {
    assigned_model: string;
    retry_budget: number;
    attempts: number;
    escalate_to: 'tech_lead' | 'human' | 'blocked';
  };
  result?: {
    outcome?: 'success' | 'test_failed' | 'error' | 'pending';
    diff?: string;
    diff_stats?: DiffStats;
    test_output?: string;
    logs?: Array<{
      ts: string;
      level: 'info' | 'warn' | 'error';
      message: string;
    }>;
  };
  audit?: {
    verdict?: 'pending' | 'approved' | 'rejected';
    reviewed_by?: string;
    feedback?: string;
  };
  metadata?: Record<string, unknown>;
  depends_on?: string[];
}

export interface TicketGraphEdge {
  source: string;
  target: string;
  kind: string;
}

export interface TicketGraphNode {
  id: string;
  status: string;
  depends_on: string[];
  target_files: string[];
  ready_state: string;
  leased: boolean;
  lease: {
    worker_id: string;
    expires_at?: string;
    heartbeat_at?: string;
    ttl_sec?: number;
  } | null;
}

export interface TicketGraphPayload {
  project_id: string;
  nodes: TicketGraphNode[];
  edges: TicketGraphEdge[];
  ready?: string[];
  blocked?: string[];
}

export interface WorkerSlotStatus {
  worker_id: string;
  running: boolean;
  last_run_at?: string | null;
  last_error?: string;
  last_skipped_reason?: string;
  ticket_id?: string | null;
}

export interface BackendProject {
  id: string;
  name: string;
  path: string;
  default_branch: string;
  env?: Record<string, string>;
  env_allowlist?: string[];
  test_allow_network?: boolean;
  sandbox_mode?: 'auto' | 'strict' | 'docker' | 'unshare' | 'none';
  setup_cmd?: string;
  cleanup_cmd?: string;
  created_at?: string;
}

export interface BackendProjectConventions {
  project_id: string;
  test_command: string;
  conventions: string;
}

export interface BackendLocalModelEndpoint {
  id: string;
  label: string;
  base_url: string;
  api_key?: string;
  ok?: boolean;
  models?: string[];
  error?: string;
}

export interface BackendRequirement {
  id: string;
  project_id?: string;
  prompt: string;
  repo: string;
  branch: string;
  scope_paths: string[];
  constraints: string[];
  priority: 'low' | 'medium' | 'high';
  intent: 'feature' | 'bugfix' | 'refactor' | 'chore' | 'spike';
  scale?: 'small' | 'medium' | 'large' | null;
  granularity: 'coarse' | 'balanced' | 'fine';
  allow_new_files: boolean;
  test_command: string;
  acceptance_notes: string;
  created_at?: string;
  cloud_input_tokens?: number;
  cloud_output_tokens?: number;
  cloud_cost_usd?: number;
}

export interface BackendRequirementDecomposeRequest {
  project_id?: string;
  prompt: string;
  repo: string;
  branch: string;
  scope_paths: string[];
  constraints: string[];
  priority: 'low' | 'medium' | 'high';
  intent: 'feature' | 'bugfix' | 'refactor' | 'chore' | 'spike';
  scale?: 'small' | 'medium' | 'large' | null;
  granularity: 'coarse' | 'balanced' | 'fine';
  allow_new_files: boolean;
  test_command: string;
  attachments: Array<Record<string, unknown>>;
  acceptance_notes: string;
}

export interface BackendManualTicketCreateRequest {
  project_id?: string;
  title: string;
  type: 'feature' | 'bugfix' | 'refactor' | 'test' | 'chore';
  target_files: string[];
  task_description: string;
  constraints?: string[];
  dod_tests?: string[];
  acceptance_criteria?: string[];
  assigned_model?: string;
}

export interface BackendRoleRouting {
  tech_lead: string;
  dev_team: string | string[];
  gatekeeper: string;
  escalation_target: string;
}

export type RunEventType =
  | 'run_started'
  | 'model_call'
  | 'diff_produced'
  | 'dod_check'
  | 'retry'
  | 'escalation'
  | 'egress_attempt'
  | 'attachment_egress'
  | 'diff_scope_reject'
  | 'rollback'
  | 'conflict'
  | 'report'
  | 'run_finished'
  | 'error';

export interface SplitTicketResponse {
  parent_id: string;
  child_ticket_ids: string[];
  ticket: BackendTicket;
  children: BackendTicket[];
}

export type RunEventCostStatus = 'actual' | 'estimated' | 'unknown';

export type InsightsRange = '7d' | '30d' | 'all';

export type InsightsCostStatus = 'actual' | 'estimated' | 'unknown';

export interface InsightsThroughputSeriesPoint {
  date: string;
  count: number;
}

export interface InsightsCostSeriesPoint {
  date: string;
  actual: number;
  estimated: number;
  unknown: number;
  total_usd: number;
}

export interface InsightsModelScorecardRow {
  model_id: string;
  sample_size: number;
  runs: number;
  model_calls: number;
  successful_runs: number;
  success_rate: number;
  retries: number;
  escalations: number;
  cost_usd: number;
  cost_by_status: Record<InsightsCostStatus, number>;
  task_type_mix: Record<string, number>;
  human_override_count: number;
}

export interface InsightsPayload {
  project_id: string;
  range: InsightsRange;
  generated_at: string;
  throughput: {
    total_done: number;
    series: InsightsThroughputSeriesPoint[];
  };
  cycle_time: {
    sample_size: number;
    avg_hours: number;
    median_hours: number;
  };
  escalation_rate: {
    runs: number;
    escalations: number;
    rate: number;
  };
  local_vs_cloud: {
    total_model_calls: number;
    local: { count: number; share: number; cost_usd: number };
    cloud: { count: number; share: number; cost_usd: number };
  };
  cost: {
    total_usd: number;
    by_status: Record<InsightsCostStatus, number>;
    series: InsightsCostSeriesPoint[];
  };
  model_scorecard: InsightsModelScorecardRow[];
  time_to_first_pr?: {
    sample_size: number;
    avg_hours: number;
    median_hours: number;
    samples: Array<{ ticket_id: string; hours: number; pr_url?: string | null; opened_at: string }>;
  };
  roi?: {
    done_tickets: number;
    accepted_tickets: number;
    estimated_hours_saved: number;
    assumed_hours_saved_per_done_ticket: number;
    assumed_hourly_rate_usd: number;
    estimated_value_usd: number;
    cloud_cost_usd: number;
    estimated_net_value_usd: number;
    intervention_count?: number;
    local_share?: number;
    cloud_share?: number;
    method: string;
  };
}

export interface TicketSignals {
  ticket_id?: string;
  affected_files: string[];
  risk: { level: string; reasons?: string[] };
  dod_strength: {
    level: string;
    score?: number;
    tests_count?: number;
    static_checks_count?: number;
    acceptance_criteria_count?: number;
    machine_verifiable?: boolean;
    reasons?: string[];
  };
  cloud_privacy_flags: Array<{ id: string; severity: string; message: string }>;
  derived_only?: boolean;
}

export interface DecisionCenterItem {
  type: 'ticket' | 'requirement';
  id: string;
  project_id: string;
  title: string;
  status: string;
  priority?: string;
  requirement_id?: string;
  signals?: TicketSignals;
  actions?: string[];
  acceptance_summary?: AcceptanceSummary;
  split_from?: string;
  child_ticket_ids?: string[];
}

export interface DecisionCenterGroup {
  id: string;
  title: string;
  items: DecisionCenterItem[];
}

export interface DecisionCenterPayload {
  project_id: string;
  generated_at: string;
  groups: DecisionCenterGroup[];
  counts: Record<string, number>;
  derived_only?: boolean;
}

export interface AcceptanceCheck {
  id: string;
  label: string;
  passed: boolean;
  severity: string;
  detail: string;
}

export interface AcceptanceSummary {
  ticket_id: string;
  status: string;
  recommendation: string;
  checks: AcceptanceCheck[];
  signals?: TicketSignals;
  supply_chain?: {
    changed_manifests: string[];
    added_deps: Array<{
      manifest?: string;
      name?: string;
      version?: string;
    }>;
    findings: Array<{
      severity?: string;
      source?: string;
      package?: string;
      detail?: string;
    }>;
  };
  pr?: {
    url?: string | null;
    status?: string | null;
    branch?: string | null;
    provider?: string | null;
    ready?: boolean;
  };
  derived_only?: boolean;
}

export type InboxNotificationKind = 'needs_you' | 'done' | 'blocked';

export interface InboxNotification {
  id: number;
  project_id: string;
  ticket_id?: string | null;
  requirement_id?: string | null;
  kind: InboxNotificationKind;
  title: string;
  created_at: string;
  read_at?: string | null;
  dedupe_key?: string;
  unread?: boolean;
}

export interface InboxUnreadCount {
  total: number;
  by_project: Record<string, number>;
}

export interface InboxNotificationsResponse {
  notifications: InboxNotification[];
  unread_count: InboxUnreadCount;
}

export type EvalRunStatus = 'running' | 'completed' | 'failed';

export interface EvalTaskSet {
  id: string;
  label: string;
  description: string;
  task_ids: string[];
  task_count: number;
  source: string;
}

export interface EvalRun {
  id: string;
  model_id: string;
  task_set_id: string;
  status: EvalRunStatus;
  trials: number;
  started_at: string;
  finished_at?: string | null;
  summary: Record<string, unknown>;
  baseline_run_id?: string | null;
  regressed: boolean;
  error: string;
}

export interface RunEvent {
  id: number;
  project_id: string;
  requirement_id?: string | null;
  ticket_id?: string | null;
  run_id?: string | null;
  event_type: RunEventType;
  ts: string;
  model_id?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cost_usd?: number | null;
  cost_status?: RunEventCostStatus | null;
  payload?: Record<string, unknown> | null;
}

export interface RequirementTemplate {
  id: string;
  title: string;
  prompt: string;
  scope_paths: string[];
  constraints: string[];
  built_in?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface DemoSeedResult {
  project: BackendProject;
  requirement: BackendRequirement;
  proposed_tickets: BackendTicket[];
}

export interface RequirementShareSummaryTicket {
  id: string;
  title: string;
  status: string;
  outcome?: string;
  diff?: string;
}

export interface RequirementShareSummary {
  requirement: {
    id: string;
    project_id: string;
    status: string;
    prompt: string;
    scope_paths: string[];
    constraints: string[];
    acceptance_notes?: string;
    created_at?: string | null;
    updated_at?: string | null;
  };
  tickets: RequirementShareSummaryTicket[];
  run_events: Record<string, unknown>[];
  cost: {
    total_usd?: number;
    requirement_usd?: number;
    run_events_usd?: number;
  };
}

export type MembershipRole = 'owner' | 'admin' | 'member' | 'viewer';

export type TeamPlaneAction = 'read' | 'mutate' | 'admin';

export interface IdentityContext {
  identity_configured: boolean;
  actor_id: string;
  workspace_id: string;
  role: MembershipRole;
  implicit_owner: boolean;
  permissions: TeamPlaneAction[];
}

export interface WorkspaceMembership {
  user_id: string;
  workspace_id: string;
  role: MembershipRole;
  created_at: string;
  email?: string;
  display_name?: string;
}

export interface AuditEvent {
  id: number;
  actor_id: string;
  workspace_id: string;
  action: string;
  target: string;
  ts: string;
  ip?: string | null;
}

export interface RunnerActiveLease {
  ticket_id?: string;
  job_id?: string;
}

export interface RunnerRecord {
  id: string;
  workspace_id: string;
  label: string;
  created_at: string;
  revoked_at?: string | null;
  last_heartbeat_at?: string | null;
  status: 'online' | 'offline' | 'revoked';
  active_lease?: RunnerActiveLease | null;
}

export interface GitAppInstallInfo {
  provider: 'github' | 'gitlab';
  install_url: string;
  installed: boolean;
  credential_id?: string;
  label?: string;
  account?: string;
  installation_id?: string;
}

export type GitCredentialKind = 'pat' | 'app';

export interface GitCredentialPreference {
  workspace_id: string;
  provider: 'github' | 'gitlab';
  credential_kind: GitCredentialKind;
}

export interface OIDCProvider {
  issuer: string;
  client_id: string;
  redirect_uri: string;
  authorization_endpoint: string;
  token_endpoint: string;
  workspace_id: string;
  group_claim?: string | null;
  role_mapping: Record<string, MembershipRole>;
  default_role: MembershipRole;
  configured: boolean;
  client_secret_configured: boolean;
}

export interface RetentionPolicy {
  run_events_days: number | null;
  ticket_logs_days: number | null;
  diffs_days: number | null;
  prompts_days: number | null;
  attachments_days: number | null;
}

export interface RetentionPurgeCounts {
  run_events_deleted: number;
  ticket_logs_deleted: number;
  ticket_diffs_redacted: number;
  requirement_prompts_redacted: number;
  chat_messages_redacted: number;
  attachments_deleted: number;
}

export interface WorkspaceUsage {
  seats_used: number;
  seat_limit: number | null;
  plan: string;
}
