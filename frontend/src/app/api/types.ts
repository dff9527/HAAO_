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
