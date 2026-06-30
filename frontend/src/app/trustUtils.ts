import type { AcceptanceSummary, DecisionCenterPayload, TicketSignals } from './api/types';
import type { InsightsPayload } from './api/types';

export type RiskLevel = 'low' | 'medium' | 'high';

export const RISK_STYLES: Record<RiskLevel, string> = {
  low: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-800',
  medium: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800',
  high: 'bg-red-50 text-red-700 border-red-200 dark:bg-red-950 dark:text-red-300 dark:border-red-800',
};

export const DOD_STRENGTH_STYLES: Record<string, string> = {
  strong: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300',
  medium: 'bg-sky-50 text-sky-700 border-sky-200 dark:bg-sky-950 dark:text-sky-300',
  weak: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950 dark:text-amber-300',
};

export const ACTION_DISCLOSURES = {
  cloud_reasoner:
    'Cloud reasoner calls may send prompt context to your configured provider. Usage is tracked in Insights.',
  image_local:
    'Images are analysed by the cloud Tech Lead even when chat is in local mode.',
  open_pr:
    'Opening a PR publishes your branch and diff to the configured git provider.',
  webhook:
    'Webhook notifications may egress ticket titles and status to your configured URL.',
} as const;

export function decisionTotalCount(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, value) => sum + value, 0);
}

export function deriveTicketSignals(input: {
  targetFiles: string[];
  assignedModel: string;
  testsCount: number;
  staticChecksCount?: number;
  acceptanceCount?: number;
}): TicketSignals {
  const affected_files = input.targetFiles.filter(Boolean);
  const sensitive = affected_files.some((path) =>
    ['.env', 'secret', 'credential', 'token', 'private_key'].some((hint) => path.toLowerCase().includes(hint)),
  );
  const cloud = CLOUD_HINTS.some((hint) => input.assignedModel.toLowerCase().includes(hint));
  const score =
    input.testsCount * 2 +
    (input.staticChecksCount ?? 0) +
    Math.min(input.acceptanceCount ?? 0, 2);
  const dodLevel = score >= 5 ? 'strong' : score >= 2 ? 'medium' : 'weak';
  let risk: RiskLevel = 'low';
  if (sensitive || affected_files.length >= 4) risk = 'high';
  else if (cloud || dodLevel === 'weak') risk = 'medium';
  const flags = [];
  if (cloud) flags.push({ id: 'cloud_execution_model', severity: 'warning', message: 'Assigned model runs on cloud.' });
  if (sensitive) flags.push({ id: 'sensitive_file_target', severity: 'critical', message: 'Touches sensitive-looking paths.' });
  return {
    ticket_id: undefined,
    affected_files,
    risk: { level: risk, reasons: sensitive ? ['Sensitive path target'] : [] },
    dod_strength: {
      level: dodLevel,
      score,
      tests_count: input.testsCount,
      static_checks_count: input.staticChecksCount ?? 0,
      acceptance_criteria_count: input.acceptanceCount ?? 0,
      machine_verifiable: input.testsCount > 0,
      reasons: input.testsCount ? [`${input.testsCount} executable DoD test(s)`] : ['No executable DoD tests'],
    },
    cloud_privacy_flags: flags,
    derived_only: true,
  };
}

const CLOUD_HINTS = ['anthropic', 'claude', 'openai', 'gpt', 'gemini', 'google'];

export const MOCK_DECISIONS: DecisionCenterPayload = {
  project_id: 'default',
  generated_at: new Date().toISOString(),
  derived_only: true,
  counts: { gate1_scope: 2, gate2_acceptance: 1, blocked: 1, high_risk: 1 },
  groups: [
    {
      id: 'gate1_scope',
      title: 'Gate 1 scope approval',
      items: [
        {
          type: 'ticket',
          id: 'T-019',
          project_id: 'default',
          title: 'OAuth token exchange handler',
          status: 'backlog',
          priority: 'high',
          split_from: 'T-018',
          signals: deriveTicketSignals({
            targetFiles: ['auth_service/oauth/token.py'],
            assignedModel: 'qwen3-coder-next',
            testsCount: 1,
          }),
          actions: ['approve', 'split', 'abandon'],
        },
      ],
    },
    {
      id: 'gate2_acceptance',
      title: 'Gate 2 acceptance',
      items: [
        {
          type: 'ticket',
          id: 'T-003',
          project_id: 'default',
          title: 'Add tests for password policy',
          status: 'awaiting_acceptance',
          priority: 'medium',
          signals: deriveTicketSignals({
            targetFiles: ['tests/test_password_policy.py'],
            assignedModel: 'gemma-4-26b-a4b',
            testsCount: 3,
          }),
          actions: ['accept', 'reject'],
        },
      ],
    },
    {
      id: 'blocked',
      title: 'Blocked tickets',
      items: [
        {
          type: 'ticket',
          id: 'T-002',
          project_id: 'default',
          title: 'Add weak-password rejection on login',
          status: 'blocked',
          priority: 'high',
          signals: deriveTicketSignals({
            targetFiles: ['auth_service/routes/login.py'],
            assignedModel: 'qwen3-coder-next',
            testsCount: 1,
          }),
          actions: ['retry', 'split', 'assign_model', 'escalate', 'abandon'],
        },
      ],
    },
    {
      id: 'high_risk',
      title: 'High-risk open work',
      items: [],
    },
  ],
};

export const MOCK_ACCEPTANCE_SUMMARY: AcceptanceSummary = {
  ticket_id: 'T-003',
  status: 'awaiting_acceptance',
  recommendation: 'ready',
  derived_only: true,
  checks: [
    { id: 'dod_passed', label: 'Definition of Done passed', passed: true, severity: 'critical', detail: 'success' },
    { id: 'gatekeeper_approved', label: 'Gatekeeper approved', passed: true, severity: 'critical', detail: 'approved' },
    { id: 'awaiting_acceptance', label: 'Ticket is ready for PO acceptance', passed: true, severity: 'critical', detail: 'awaiting_acceptance' },
    { id: 'diff_available', label: 'Diff is available for inspection', passed: true, severity: 'warning', detail: 'present' },
    { id: 'pr_ready', label: 'PR can be opened or updated', passed: true, severity: 'warning', detail: 'eligible' },
  ],
  pr: { ready: true, url: null, status: null, branch: null, provider: null },
  supply_chain: {
    changed_manifests: ['requirements.txt', 'package.json'],
    added_deps: [
      { manifest: 'requirements.txt', name: 'requests', version: '==2.32.0' },
      { manifest: 'package.json', name: 'left-pad', version: '1.3.0' },
    ],
    findings: [],
  },
  signals: deriveTicketSignals({
    targetFiles: ['tests/test_password_policy.py'],
    assignedModel: 'gemma-4-26b-a4b',
    testsCount: 3,
  }),
};

export function extendMockInsights(base: InsightsPayload): InsightsPayload {
  return {
    ...base,
    time_to_first_pr: {
      sample_size: 3,
      avg_hours: 6.5,
      median_hours: 5.0,
      samples: [
        { ticket_id: 'T-010', hours: 5, pr_url: 'https://github.com/acme/app/pull/12', opened_at: new Date().toISOString() },
      ],
    },
    roi: {
      done_tickets: base.throughput.total_done,
      accepted_tickets: base.throughput.total_done,
      estimated_hours_saved: base.throughput.total_done * 1.5,
      assumed_hours_saved_per_done_ticket: 1.5,
      assumed_hourly_rate_usd: 100,
      estimated_value_usd: base.throughput.total_done * 150,
      cloud_cost_usd: base.cost.total_usd,
      estimated_net_value_usd: base.throughput.total_done * 150 - base.cost.total_usd,
      intervention_count: 4,
      local_share: base.local_vs_cloud.local.share,
      cloud_share: base.local_vs_cloud.cloud.share,
      method: 'Heuristic: done tickets × 1.5 engineering hours at $100/hour minus tracked cloud cost.',
    },
  };
}

export function formatHoursSaved(hours: number): string {
  if (hours <= 0) return '—';
  if (hours < 1) return `${Math.round(hours * 60)}m saved`;
  return `${hours.toFixed(1)}h saved`;
}
