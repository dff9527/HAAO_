export type BackendTicketStatus =
  | 'backlog'
  | 'ready'
  | 'in_progress'
  | 'testing'
  | 'diff_pending'
  | 'review'
  | 'awaiting_acceptance'
  | 'done'
  | 'blocked';

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
}

export interface BackendProject {
  id: string;
  name: string;
  path: string;
  default_branch: string;
  env?: Record<string, string>;
  env_allowlist?: string[];
  test_allow_network?: boolean;
  sandbox_mode?: 'auto' | 'docker' | 'unshare' | 'none';
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
  | 'report'
  | 'run_finished'
  | 'error';

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
