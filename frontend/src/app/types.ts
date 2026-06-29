export type TicketType = 'feature' | 'bugfix' | 'refactor' | 'test' | 'chore';
export type TicketStatus =
  | 'Backlog'
  | 'Ready'
  | 'In Progress'
  | 'Diff review'
  | 'Review'
  | 'Awaiting acceptance'
  | 'Done'
  | 'Blocked'
  | 'Abandoned'
  | 'Split';

export interface DiffStats {
  files_touched: number;
  lines_added: number;
  lines_removed: number;
  out_of_scope_files: string[];
}
export type TestStatus = 'none' | 'testing' | 'pass' | 'fail';
export type LogLevel = 'info' | 'warn' | 'error' | 'success';
export type AssignedModel = string;
export type Priority = 'high' | 'medium' | 'low';
export type RequirementIntent = 'feature' | 'bugfix' | 'refactor' | 'chore' | 'spike';
export type RequirementScale = 'small' | 'medium' | 'large' | '';
export type RequirementGranularity = 'coarse' | 'balanced' | 'fine';

export interface Project {
  id: string;
  name: string;
  path: string;
  defaultBranch: string;
  env: Record<string, string>;
  setupCmd: string;
  cleanupCmd: string;
}

export interface ContextFile {
  path: string;
  reason: string;
}

export interface DefinitionOfDone {
  tests: string[];
  acceptanceCriteria: string[];
}

export interface LogEntry {
  time: string;
  level: LogLevel;
  message: string;
}

export interface TechnicalAudit {
  verdict: string;
  feedback?: string;
  checkedCriteria: string[];
  auditor: string;
}

export interface ModelParams {
  temperature: number;
  topP: number;
  maxOutputTokens: number;
  contextWindowCap: number;
  defaultRetryBudget: number;
  /** @deprecated use additionalInstructions */
  systemPrompt: string;
  additionalInstructions: string;
  fullPromptOverride: string;
  useFullPromptOverride: boolean;
}

export interface ModelConfig {
  id: AssignedModel;
  name: string;
  backend: 'LM Studio (local)' | 'Cloud API';
  quant?: string;
  contextWindow: number;
  moeActive?: string;
  status: 'Loaded' | 'Available' | 'Connected';
  params: ModelParams;
}

export interface RoleRoute {
  id: string;
  role: string;
  note: string;
  model: AssignedModel;
}

export interface ConnectionSettings {
  lmStudioUrl: string;
}

export interface RequirementSource {
  id: string;           // e.g. "R-006"
  projectId?: string;
  prompt: string;
  repo: string;
  branch: string;
  scopePaths: string[];
  constraints: string[];
  priority: Priority;
  intent?: RequirementIntent;
  scale?: RequirementScale;
  granularity?: RequirementGranularity;
  allowNewFiles?: boolean;
  testCommand?: string;
  acceptanceNotes: string;
  createdAt: string;
  cloudInputTokens?: number;
  cloudOutputTokens?: number;
  cloudCostUsd?: number;
}

export interface Ticket {
  id: string;
  title: string;
  type: TicketType;
  status: TicketStatus;
  priority: Priority;
  assignedModel: AssignedModel;
  retryCount: number;
  retryBudget: number;
  testStatus: TestStatus;
  contextFiles: ContextFile[];
  definitionOfDone: DefinitionOfDone;
  agentLog: LogEntry[];
  // Gate flags
  needsApproval?: boolean;         // Gate 1: just decomposed, awaiting PO approval
  awaitingAcceptance?: boolean;    // Gate 2: passed tech audit, awaiting PO accept/reject
  technicalAudit?: TechnicalAudit; // Result of Claude TL's automated audit
  rejectionFeedback?: string;      // Feedback attached when PO rejects
  // Orchestrator auto-action flags
  autoDispatched?: boolean;        // Moved to In Progress by orchestrator automatically
  autoEscalated?: boolean;         // Reassigned to Claude TL by orchestrator
  isNew?: boolean;                 // Newly decomposed (visual flag)
  requirementId?: string;          // Traceability: which R-xxx created this
  projectId?: string;              // Project scope for multi-repo boards
  projectName?: string;            // Display label for project tag
  taskDescription?: string;        // Editable task body
  pendingDiff?: string;            // Unified diff awaiting human approval
  diffRejectionFeedback?: string;  // Feedback from diff rejection
  gitBranch?: string;              // Branch created for approved diff
  gitCommit?: string;              // Commit created for approved diff
  gitBaseBranch?: string;          // Base branch for merge
  gitMergedTo?: string;            // Branch this ticket was merged into
  gitMergeCommit?: string;         // Merge commit hash
  gitRevertCommit?: string;        // Revert commit hash
  prUrl?: string;                  // Open PR link (never includes token)
  prStatus?: string;               // opened | updated | …
  lastInterventionNotification?: InterventionNotification;
  // Wave 5 — recovery lineage + patch safety + provenance
  childTicketIds?: string[];
  splitFrom?: string;
  splitFeedback?: string;
  splitAt?: string;
  abandonReason?: string;
  abandonedAt?: string;
  diffStats?: DiffStats;
  reasonerPromptVersion?: string;
  lastRunModelId?: string;
  // Wave 7 — throughput: deps, lease, scheduling
  dependsOn?: string[];
  lease?: TicketLease;
  readyState?: TicketReadyState;
  conflictNote?: string;
  graphReady?: boolean;
  graphBlocked?: boolean;
}

export interface TicketLease {
  workerId: string;
  expiresAt?: string;
  heartbeatAt?: string;
}

export type TicketReadyState =
  | 'ready'
  | 'waiting_dependencies'
  | 'conflict'
  | 'not_ready'
  | 'terminal';

export interface InterventionNotification {
  ticketId: string;
  status: string;
  reason: string;
  ticketUrl: string;
  sentAt?: string;
}

export type ChatMessageRole = 'user' | 'agent' | 'system_report';
export type ChatReportKind = 'done' | 'blocked' | 'needs_you';
export type ChatAttachmentKind = 'file' | 'image';

export interface ChatAttachment {
  id: string;
  filename: string;
  mime: string;
  size: number;
  kind: ChatAttachmentKind;
  stored_path: string;
}

export interface ChatMessage {
  id: string;
  project_id: string;
  role: ChatMessageRole;
  text: string;
  requirement_id?: string;
  ticket_id?: string;
  report_kind?: ChatReportKind;
  segment_id: string;
  created_at: string;
  attachment_ids?: string[];
  attachments?: ChatAttachment[];
}

export interface CloudModel {
  id: string;
  label: string;
  provider: string;
  model_id: string;
  key_configured: boolean;
  deletable?: boolean;
}

export interface ChatSegment {
  id: string;
  project_id: string;
  title: string;
  summary: string;
  created_at: string;
  is_active: boolean;
}
