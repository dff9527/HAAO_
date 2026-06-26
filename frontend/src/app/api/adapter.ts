import type { AssignedModel, LogLevel, Project, RequirementSource, RoleRoute, Ticket, TicketStatus } from '../types';
import type { BackendProject, BackendRequirement, BackendRoleRouting, BackendTicket, BackendTicketStatus } from './types';

const STATUS_TO_UI: Record<BackendTicketStatus, TicketStatus> = {
  backlog: 'Backlog',
  ready: 'Ready',
  in_progress: 'In Progress',
  testing: 'In Progress',
  diff_pending: 'Diff review',
  review: 'Review',
  awaiting_acceptance: 'Awaiting acceptance',
  done: 'Done',
  blocked: 'Blocked',
};

const STATUS_TO_BACKEND: Record<TicketStatus, BackendTicketStatus> = {
  Backlog: 'backlog',
  Ready: 'ready',
  'In Progress': 'in_progress',
  'Diff review': 'diff_pending',
  Review: 'review',
  'Awaiting acceptance': 'awaiting_acceptance',
  Done: 'done',
  Blocked: 'blocked',
};

function toAssignedModel(model: string | string[]): AssignedModel {
  if (Array.isArray(model)) {
    return toAssignedModel(model[0] ?? '');
  }
  if (model === 'claude-tech-lead' || model === 'Claude PO' || model === 'Claude · Tech Lead') {
    return 'Claude · Tech Lead';
  }
  if (model === 'qwen3-coder-next' || model === 'gemma-4-26b-a4b' || model === 'qwen3.6-35b-a3b') {
    return model;
  }
  return model;
}

function toTestStatus(ticket: BackendTicket): Ticket['testStatus'] {
  if (ticket.status === 'testing') return 'testing';
  if (ticket.status === 'ready' || ticket.status === 'in_progress') return 'none';
  if (ticket.status === 'backlog' && ticket.audit?.verdict === 'rejected') return 'none';
  if (ticket.result?.outcome === 'success') return 'pass';
  if (ticket.result?.outcome === 'test_failed' || ticket.result?.outcome === 'error') return 'fail';
  return 'none';
}

function toLogLevel(level?: string): LogLevel {
  if (level === 'warn' || level === 'error' || level === 'success') return level;
  return 'info';
}

export function toUiTicket(ticket: BackendTicket, projectName?: string): Ticket {
  const projectId = typeof ticket.metadata?.project_id === 'string' ? ticket.metadata.project_id : undefined;
  const diffRejectionFeedback =
    typeof ticket.metadata?.diff_rejection_feedback === 'string'
      ? ticket.metadata.diff_rejection_feedback
      : undefined;
  const previousReviewFeedback =
    typeof ticket.metadata?.previous_review_feedback === 'string'
      ? ticket.metadata.previous_review_feedback
      : undefined;
  const showReworkFeedback = ['backlog', 'ready', 'in_progress', 'testing', 'blocked'].includes(ticket.status);
  const auditVerdict = ticket.audit?.verdict ?? 'pending';
  return {
    id: ticket.id,
    title: ticket.title,
    type: ticket.type,
    status: STATUS_TO_UI[ticket.status],
    priority: ticket.priority ?? 'medium',
    assignedModel: toAssignedModel(ticket.execution.assigned_model),
    retryCount: ticket.execution.attempts,
    retryBudget: ticket.execution.retry_budget,
    testStatus: toTestStatus(ticket),
    contextFiles: (ticket.context.files ?? []).map((file) => ({
      path: file.path,
      reason: file.reason ?? '',
    })),
    definitionOfDone: {
      tests: (ticket.definition_of_done.tests ?? []).map((t) => t.command),
      acceptanceCriteria: ticket.definition_of_done.acceptance_criteria ?? [],
    },
    agentLog: (ticket.result?.logs ?? []).map((log) => ({
      time: log.ts,
      level: toLogLevel(log.level),
      message: log.message,
    })),
    needsApproval: Boolean(ticket.metadata?.needs_approval),
    awaitingAcceptance: ticket.status === 'awaiting_acceptance',
    technicalAudit: ticket.audit && (auditVerdict !== 'pending' || Boolean(ticket.audit.feedback))
      ? {
          verdict: auditVerdict,
          feedback: ticket.audit.feedback || undefined,
          auditor: ticket.audit.reviewed_by ?? '',
          checkedCriteria: ticket.definition_of_done.acceptance_criteria ?? [],
        }
      : undefined,
    rejectionFeedback: showReworkFeedback
      ? (typeof ticket.metadata?.product_rejection_feedback === 'string'
          ? ticket.metadata?.product_rejection_feedback
          : undefined) ?? previousReviewFeedback ?? ticket.audit?.feedback
      : undefined,
    autoDispatched: Boolean(ticket.metadata?.auto_dispatched),
    autoEscalated: Boolean(ticket.metadata?.auto_escalated),
    requirementId:
      typeof ticket.metadata?.requirement_id === 'string'
        ? ticket.metadata.requirement_id
        : undefined,
    projectId,
    projectName,
    taskDescription: ticket.task.description ?? '',
    pendingDiff: ticket.status === 'diff_pending' ? ticket.result?.diff : undefined,
    diffRejectionFeedback,
    gitBranch: typeof ticket.metadata?.git_branch === 'string' ? ticket.metadata.git_branch : undefined,
    gitCommit: typeof ticket.metadata?.git_commit === 'string' ? ticket.metadata.git_commit : undefined,
    gitBaseBranch: typeof ticket.metadata?.git_base_branch === 'string' ? ticket.metadata.git_base_branch : undefined,
    gitMergedTo: typeof ticket.metadata?.git_merged_to === 'string' ? ticket.metadata.git_merged_to : undefined,
    gitMergeCommit: typeof ticket.metadata?.git_merge_commit === 'string' ? ticket.metadata.git_merge_commit : undefined,
    gitRevertCommit: typeof ticket.metadata?.git_revert_commit === 'string' ? ticket.metadata.git_revert_commit : undefined,
    prUrl: typeof ticket.metadata?.pr_url === 'string' ? ticket.metadata.pr_url : undefined,
    prStatus: typeof ticket.metadata?.pr_status === 'string' ? ticket.metadata.pr_status : undefined,
    lastInterventionNotification: toInterventionNotification(ticket.metadata),
  };
}

function toInterventionNotification(metadata: BackendTicket['metadata']): Ticket['lastInterventionNotification'] {
  const raw = metadata?.last_intervention_notification;
  if (!raw || typeof raw !== 'object') return undefined;
  const record = raw as Record<string, unknown>;
  if (typeof record.reason !== 'string' || !record.reason.trim()) return undefined;
  return {
    ticketId: typeof record.ticket_id === 'string' ? record.ticket_id : '',
    status: typeof record.status === 'string' ? record.status : '',
    reason: record.reason,
    ticketUrl: typeof record.ticket_url === 'string' ? record.ticket_url : '',
    sentAt: typeof record.sent_at === 'string' ? record.sent_at : undefined,
  };
}

export function toBackendStatus(status: TicketStatus): BackendTicketStatus {
  return STATUS_TO_BACKEND[status];
}

export function toUiRequirement(requirement: BackendRequirement): RequirementSource {
  return {
    id: requirement.id,
    projectId: requirement.project_id,
    prompt: requirement.prompt,
    repo: requirement.repo,
    branch: requirement.branch,
    scopePaths: requirement.scope_paths ?? [],
    constraints: requirement.constraints ?? [],
    priority: requirement.priority,
    intent: requirement.intent,
    scale: requirement.scale ?? '',
    granularity: requirement.granularity,
    allowNewFiles: requirement.allow_new_files,
    testCommand: requirement.test_command,
    acceptanceNotes: requirement.acceptance_notes ?? '',
    createdAt: requirement.created_at ?? '',
    cloudInputTokens: requirement.cloud_input_tokens,
    cloudOutputTokens: requirement.cloud_output_tokens,
    cloudCostUsd: requirement.cloud_cost_usd,
  };
}

export function toUiProject(project: BackendProject): Project {
  return {
    id: project.id,
    name: project.name,
    path: project.path,
    defaultBranch: project.default_branch,
    env: project.env ?? {},
    setupCmd: project.setup_cmd ?? '',
    cleanupCmd: project.cleanup_cmd ?? '',
  };
}

export function devTeamChainFromRouting(routing: BackendRoleRouting): string[] {
  const value = routing.dev_team;
  const items = Array.isArray(value) ? value : [value];
  return items.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

export function roleRoutingToRoutes(routing: BackendRoleRouting): RoleRoute[] {
  const devChain = devTeamChainFromRouting(routing);
  return [
    {
      id: 'tech_lead',
      role: 'Tech Lead (decompose + technical audit)',
      note: 'high-level reasoning, cloud only',
      model: toAssignedModel(routing.tech_lead),
    },
    {
      id: 'dev',
      role: 'Dev — code execution',
      note: 'ordered local fallback chain',
      model: toAssignedModel(devChain[0] ?? routing.dev_team),
    },
    {
      id: 'gatekeeper',
      role: 'Gatekeeper (triage / log summary / DoD pre-check)',
      note: 'fast, cheap, local',
      model: toAssignedModel(routing.gatekeeper),
    },
    {
      id: 'escalation',
      role: 'Escalation target (auto)',
      note: 'auto-assigned by orchestrator when local budget exhausted',
      model: toAssignedModel(routing.escalation_target),
    },
  ];
}

export function routesToRoleRouting(routes: RoleRoute[], devTeamChain?: string[]): BackendRoleRouting {
  const byId = new Map(routes.map((route) => [route.id, route.model]));
  const normalize = (model: AssignedModel): string =>
    model === 'Claude · Tech Lead' ? 'claude-tech-lead' : model;
  const chain = (devTeamChain ?? [])
    .map((model) => normalize(model as AssignedModel))
    .filter(Boolean);
  const devTeam = chain.length > 1 ? chain : (chain[0] ?? normalize(byId.get('dev') ?? 'qwen3-coder-next'));
  return {
    tech_lead: normalize(byId.get('tech_lead') ?? 'Claude · Tech Lead'),
    dev_team: devTeam,
    gatekeeper: normalize(byId.get('gatekeeper') ?? 'gemma-4-26b-a4b'),
    escalation_target: normalize(byId.get('escalation') ?? 'Claude · Tech Lead'),
  };
}
