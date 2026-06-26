import type {
  BackendManualTicketCreateRequest,
  BackendRequirement,
  BackendRequirementDecomposeRequest,
  BackendProject,
  BackendProjectConventions,
  BackendLocalModelEndpoint,
  BackendRoleRouting,
  BackendTicket,
  BackendTicketStatus,
  RunEvent,
  InsightsPayload,
  InsightsRange,
  InboxNotification,
  InboxNotificationsResponse,
  EvalRun,
  EvalTaskSet,
  RequirementTemplate,
  DemoSeedResult,
  RequirementShareSummary,
} from './types';
import type { ChatAttachment, ChatMessage, ChatSegment, CloudModel } from '../types';
import { getStoredApiToken, promptForApiToken, setStoredApiToken } from './authToken';

const API_PREFIX = '/api';

export interface CloudReasonerProvider {
  id: string;
  label: string;
  key_configured: boolean;
}

export interface CloudReasonerConfig {
  model_id: string;
  provider: string;
  providers: CloudReasonerProvider[];
}

export type IntegrationProvider = 'github' | 'gitlab' | 'slack';

export interface IntegrationCredential {
  provider: IntegrationProvider;
  id: string;
  label: string;
  scopes: string[];
  configured: boolean;
  created_at: string;
  updated_at: string;
}

export interface AutoWorkerStatus {
  running: boolean;
  interval_sec: number;
  max_cycles_per_tick: number;
  allow_dirty_workspace: boolean;
  last_started_at: string | null;
  last_run_at: string | null;
  last_error: string;
  last_skipped_reason: string;
  project_id: string | null;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function responseErrorMessage(response: Response): Promise<string> {
  const body = await response.text();
  if (!body) return `Request failed: ${response.status}`;
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === 'string') return parsed.detail;
    if (Array.isArray(parsed.detail)) {
      return parsed.detail
        .map((item) => {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object' && 'msg' in item) return String(item.msg);
          return '';
        })
        .filter(Boolean)
        .join('; ') || body;
    }
  } catch {
    return body;
  }
  return body;
}

function authHeaders(extra?: HeadersInit): HeadersInit {
  const headers: Record<string, string> = {};
  const token = getStoredApiToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (extra) {
    if (extra instanceof Headers) {
      extra.forEach((value, key) => {
        headers[key] = value;
      });
    } else if (Array.isArray(extra)) {
      for (const [key, value] of extra) {
        headers[key] = value;
      }
    } else {
      Object.assign(headers, extra);
    }
  }
  return headers;
}

async function handleUnauthorized<T>(
  message: string,
  retried: boolean,
  retry: () => Promise<T>,
): Promise<T | null> {
  if (retried) {
    return null;
  }
  const token = await promptForApiToken(message);
  if (!token) {
    return null;
  }
  setStoredApiToken(token);
  return retry();
}

async function uploadRequest<T>(path: string, body: FormData, retried = false): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    method: 'POST',
    headers: authHeaders(),
    body,
  });
  if (response.status === 401) {
    const message = await responseErrorMessage(response);
    const retry = await handleUnauthorized(message, retried, () => uploadRequest<T>(path, body, true));
    if (retry !== null) {
      return retry;
    }
    throw new ApiError(401, message);
  }
  if (!response.ok) {
    throw new ApiError(response.status, await responseErrorMessage(response));
  }
  return (await response.json()) as T;
}

async function request<T>(path: string, init?: RequestInit, retried = false): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(init?.headers),
    },
  });

  if (response.status === 401) {
    const message = await responseErrorMessage(response);
    const retry = await handleUnauthorized(message, retried, () => request<T>(path, init, true));
    if (retry !== null) {
      return retry;
    }
    throw new ApiError(401, message);
  }

  if (!response.ok) {
    throw new ApiError(response.status, await responseErrorMessage(response));
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function projectQuery(projectId?: string, prefix = '?'): string {
  return projectId ? `${prefix}project_id=${encodeURIComponent(projectId)}` : '';
}

export const apiClient = {
  listProjects: async (): Promise<BackendProject[]> => {
    const data = await request<{ projects: BackendProject[] }>('/projects');
    return data.projects;
  },

  createProject: async (payload: { name: string; path: string; default_branch?: string }): Promise<BackendProject> => {
    const data = await request<{ project: BackendProject }>('/projects', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return data.project;
  },

  updateProjectSettings: async (
    projectId: string,
    payload: {
      env?: Record<string, string>;
      env_allowlist?: string[];
      test_allow_network?: boolean;
      sandbox_mode?: 'auto' | 'docker' | 'unshare' | 'none';
      setup_cmd?: string;
      cleanup_cmd?: string;
      default_branch?: string;
    },
  ): Promise<BackendProject> => {
    const data = await request<{ project: BackendProject }>(`/projects/${projectId}/settings`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
    return data.project;
  },

  deleteProject: async (projectId: string): Promise<void> => {
    await request<{ deleted: boolean; project_id: string }>(`/projects/${projectId}`, { method: 'DELETE' });
  },

  getProjectConventions: async (projectId: string): Promise<BackendProjectConventions> =>
    request(`/projects/${projectId}/conventions`),

  getModelAdditionalInstructions: async (modelId: string): Promise<string> => {
    const data = await request<{ model_id: string; additional_instructions: string }>(
      `/models/${encodeURIComponent(modelId)}/additional_instructions`,
    );
    return data.additional_instructions ?? '';
  },

  updateModelAdditionalInstructions: async (
    modelId: string,
    additionalInstructions: string,
  ): Promise<string> => {
    const data = await request<{ model_id: string; additional_instructions: string }>(
      `/models/${encodeURIComponent(modelId)}/additional_instructions`,
      {
        method: 'PUT',
        body: JSON.stringify({ additional_instructions: additionalInstructions }),
      },
    );
    return data.additional_instructions ?? '';
  },

  listLocalModelEndpoints: async (): Promise<BackendLocalModelEndpoint[]> => {
    const data = await request<{ endpoints: BackendLocalModelEndpoint[] }>('/models/local/endpoints');
    return data.endpoints;
  },

  updateLocalModelEndpoints: async (
    endpoints: BackendLocalModelEndpoint[],
  ): Promise<BackendLocalModelEndpoint[]> => {
    const data = await request<{ endpoints: BackendLocalModelEndpoint[] }>('/models/local/endpoints', {
      method: 'PUT',
      body: JSON.stringify({ endpoints }),
    });
    return data.endpoints;
  },

  listAvailableLocalModels: async (): Promise<{
    models: string[];
    endpoints: BackendLocalModelEndpoint[];
  }> => request('/models/local/available'),

  listTickets: async (projectId?: string): Promise<BackendTicket[]> => {
    const data = await request<{ tickets: BackendTicket[] }>(`/tickets${projectQuery(projectId)}`);
    return data.tickets;
  },

  createManualTicket: async (payload: BackendManualTicketCreateRequest): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>('/tickets', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return data.ticket;
  },

  getTicket: async (ticketId: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}${projectQuery(projectId)}`);
    return data.ticket;
  },

  approveTicket: async (ticketId: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/approve${projectQuery(projectId)}`, { method: 'POST' });
    return data.ticket;
  },

  acceptTicket: async (ticketId: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/accept${projectQuery(projectId)}`, { method: 'POST' });
    return data.ticket;
  },

  rejectTicket: async (ticketId: string, feedback: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/reject${projectQuery(projectId)}`, {
      method: 'POST',
      body: JSON.stringify({ feedback }),
    });
    return data.ticket;
  },

  assignModel: async (ticketId: string, model: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/assign_model${projectQuery(projectId)}`, {
      method: 'POST',
      body: JSON.stringify({ model }),
    });
    return data.ticket;
  },

  moveTicket: async (ticketId: string, status: BackendTicketStatus, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/move${projectQuery(projectId)}`, {
      method: 'POST',
      body: JSON.stringify({ status }),
    });
    return data.ticket;
  },

  retryTicket: async (ticketId: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/retry${projectQuery(projectId)}`, { method: 'POST' });
    return data.ticket;
  },

  escalateTicket: async (
    ticketId: string,
    payload: { reason: string; escalated_to?: string },
    projectId?: string,
  ): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/escalate${projectQuery(projectId)}`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return data.ticket;
  },

  executeTicket: async (ticketId: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/execute${projectQuery(projectId)}`, { method: 'POST' });
    return data.ticket;
  },

  cancelTicket: async (ticketId: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/cancel${projectQuery(projectId)}`, { method: 'POST' });
    return data.ticket;
  },

  updateTicket: async (
    ticketId: string,
    payload: {
      task_description?: string;
      task_target_files?: string[];
      dod_tests?: string[];
      assigned_model?: string;
      rerun?: boolean;
    },
    projectId?: string,
  ): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}${projectQuery(projectId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
    return data.ticket;
  },

  approveDiff: async (ticketId: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/diff/approve${projectQuery(projectId)}`, { method: 'POST' });
    return data.ticket;
  },

  rejectDiff: async (ticketId: string, feedback: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/diff/reject${projectQuery(projectId)}`, {
      method: 'POST',
      body: JSON.stringify({ feedback }),
    });
    return data.ticket;
  },

  mergeTicket: async (ticketId: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/merge${projectQuery(projectId)}`, { method: 'POST' });
    return data.ticket;
  },

  revertTicket: async (ticketId: string, projectId?: string): Promise<BackendTicket> => {
    const data = await request<{ ticket: BackendTicket }>(`/tickets/${ticketId}/revert${projectQuery(projectId)}`, { method: 'POST' });
    return data.ticket;
  },

  getClaudeModel: async (): Promise<string> => {
    const data = await request<{ model: string }>('/config/claude-model');
    return data.model;
  },

  getCloudReasoner: async (): Promise<CloudReasonerConfig> =>
    request('/config/cloud-reasoner'),

  updateCloudReasoner: async (modelId: string): Promise<CloudReasonerConfig> =>
    request('/config/cloud-reasoner', {
      method: 'PUT',
      body: JSON.stringify({ model_id: modelId }),
    }),

  deleteTicket: async (ticketId: string, force = false, projectId?: string): Promise<void> => {
    const params = new URLSearchParams();
    if (force) params.set('force', 'true');
    if (projectId) params.set('project_id', projectId);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    await request<{ deleted: boolean; ticket_id: string }>(`/tickets/${ticketId}${suffix}`, { method: 'DELETE' });
  },

  decomposeRequirement: async (payload: BackendRequirementDecomposeRequest): Promise<{
    requirement_id: string;
    requirement: BackendRequirement;
    proposed_tickets: BackendTicket[];
  }> => request('/requirements/decompose', { method: 'POST', body: JSON.stringify(payload) }),

  confirmRequirement: async (requirementId: string, tickets: BackendTicket[], projectId?: string): Promise<{
    requirement: BackendRequirement;
    tickets: BackendTicket[];
  }> =>
    request(`/requirements/${requirementId}/confirm${projectQuery(projectId)}`, {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, tickets }),
    }),

  discardRequirement: async (requirementId: string, projectId?: string): Promise<void> =>
    request(`/requirements/${requirementId}/discard${projectQuery(projectId)}`, { method: 'POST' }),

  listRequirements: async (projectId?: string): Promise<BackendRequirement[]> => {
    const data = await request<{ requirements: BackendRequirement[] }>(`/requirements${projectQuery(projectId)}`);
    return data.requirements;
  },

  getRoleRouting: async (): Promise<BackendRoleRouting> => {
    const data = await request<{ routing: BackendRoleRouting }>('/config/role-routing');
    return data.routing;
  },

  updateRoleRouting: async (routing: BackendRoleRouting): Promise<BackendRoleRouting> => {
    const data = await request<{ routing: BackendRoleRouting }>('/config/role-routing', {
      method: 'PUT',
      body: JSON.stringify({ routing }),
    });
    return data.routing;
  },

  getNotificationSettings: async (): Promise<string> => {
    const data = await request<{ webhook_url: string }>('/config/notifications');
    return data.webhook_url ?? '';
  },

  updateNotificationSettings: async (webhookUrl: string): Promise<string> => {
    const data = await request<{ webhook_url: string }>('/config/notifications', {
      method: 'PUT',
      body: JSON.stringify({ webhook_url: webhookUrl }),
    });
    return data.webhook_url ?? '';
  },

  getWorkerStatus: async (): Promise<AutoWorkerStatus> =>
    request('/orchestrator/worker/status'),

  startWorker: async (
    projectId: string,
    opts?: { interval_sec?: number; max_cycles_per_tick?: number; allow_dirty_workspace?: boolean },
  ): Promise<AutoWorkerStatus> =>
    request('/orchestrator/worker/start', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, ...opts }),
    }),

  stopWorker: async (): Promise<AutoWorkerStatus> =>
    request('/orchestrator/worker/stop', { method: 'POST' }),

  uploadChatAttachment: async (projectId: string, file: File): Promise<ChatAttachment> => {
    const body = new FormData();
    body.append('project_id', projectId);
    body.append('file', file);
    return uploadRequest<ChatAttachment>('/chat/attachments', body);
  },

  chatAttachmentContentUrl: (attachmentId: string, projectId: string): string =>
    `${API_PREFIX}/chat/attachments/${encodeURIComponent(attachmentId)}/content?project_id=${encodeURIComponent(projectId)}`,

  sendChatMessage: async (
    projectId: string,
    text: string,
    attachmentIds: string[] = [],
  ): Promise<{
    messages: ChatMessage[];
    filed_requirement_ids: string[];
  }> =>
    request('/chat/messages', {
      method: 'POST',
      body: JSON.stringify({
        project_id: projectId,
        text,
        attachment_ids: attachmentIds,
      }),
    }),

  listChatMessages: async (
    projectId: string,
    opts?: { segmentId?: string; after?: string; limit?: number },
  ): Promise<ChatMessage[]> => {
    const params = new URLSearchParams({ project_id: projectId });
    if (opts?.segmentId) params.set('segment_id', opts.segmentId);
    if (opts?.after) params.set('after', opts.after);
    if (opts?.limit != null) params.set('limit', String(opts.limit));
    const data = await request<{ messages: ChatMessage[] }>(
      `/chat/messages?${params.toString()}`,
    );
    return data.messages;
  },

  createChatSegment: async (projectId: string, title: string): Promise<ChatSegment> =>
    request('/chat/segments', {
      method: 'POST',
      body: JSON.stringify({ project_id: projectId, title }),
    }),

  listChatSegments: async (projectId: string): Promise<ChatSegment[]> => {
    const data = await request<{ segments: ChatSegment[] }>(
      `/chat/segments?project_id=${encodeURIComponent(projectId)}`,
    );
    return data.segments;
  },

  listCloudModels: async (): Promise<CloudModel[]> => {
    const data = await request<{ models: CloudModel[] }>('/config/cloud-models');
    return data.models;
  },

  addCloudModel: async (payload: {
    label?: string;
    provider: string;
    model_id: string;
    api_key: string;
  }): Promise<CloudModel> =>
    request('/config/cloud-models', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  deleteCloudModel: async (modelId: string): Promise<void> => {
    await request(`/config/cloud-models/${encodeURIComponent(modelId)}`, { method: 'DELETE' });
  },

  testCloudModel: async (payload: {
    provider: string;
    model_id: string;
    api_key?: string;
  }): Promise<{ ok: boolean; message: string }> =>
    request('/config/cloud-models/test', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listProviderModels: async (payload: {
    provider: string;
    api_key?: string;
  }): Promise<{ ok: boolean; models: string[]; message: string }> =>
    request('/config/cloud-models/list-models', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getCloudExecutionSettings: async (): Promise<{ allow_cloud_execution_model: boolean }> =>
    request('/config/cloud-execution'),

  updateCloudExecutionSettings: async (
    allowCloudExecutionModel: boolean,
  ): Promise<{ allow_cloud_execution_model: boolean }> =>
    request('/config/cloud-execution', {
      method: 'PUT',
      body: JSON.stringify({ allow_cloud_execution_model: allowCloudExecutionModel }),
    }),

  listIntegrations: async (provider?: IntegrationProvider): Promise<IntegrationCredential[]> => {
    const suffix = provider ? `?provider=${encodeURIComponent(provider)}` : '';
    const data = await request<{ integrations: IntegrationCredential[] }>(`/config/integrations${suffix}`);
    return data.integrations;
  },

  upsertIntegration: async (payload: {
    provider: IntegrationProvider;
    token: string;
    scopes?: string[];
    label?: string;
    id?: string;
  }): Promise<IntegrationCredential> =>
    request('/config/integrations', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  deleteIntegration: async (provider: IntegrationProvider, credentialId: string): Promise<void> => {
    await request(`/config/integrations/${encodeURIComponent(provider)}/${encodeURIComponent(credentialId)}`, {
      method: 'DELETE',
    });
  },

  openTicketPr: async (
    ticketId: string,
    projectId?: string,
  ): Promise<{ pr_url: string; status: string }> =>
    request(`/tickets/${ticketId}/pr${projectQuery(projectId)}`, { method: 'POST' }),

  getInsights: async (projectId: string, range: InsightsRange = '30d'): Promise<InsightsPayload> => {
    const params = new URLSearchParams({
      project_id: projectId,
      range,
    });
    return request(`/insights?${params.toString()}`);
  },

  listEvalTaskSets: async (): Promise<EvalTaskSet[]> => {
    const data = await request<{ task_sets: EvalTaskSet[] }>('/evals/task-sets');
    return data.task_sets;
  },

  listEvalRuns: async (opts?: {
    modelId?: string;
    taskSetId?: string;
    limit?: number;
  }): Promise<EvalRun[]> => {
    const params = new URLSearchParams();
    if (opts?.modelId) params.set('model_id', opts.modelId);
    if (opts?.taskSetId) params.set('task_set_id', opts.taskSetId);
    if (opts?.limit != null) params.set('limit', String(opts.limit));
    const suffix = params.toString() ? `?${params.toString()}` : '';
    const data = await request<{ eval_runs: EvalRun[] }>(`/evals${suffix}`);
    return data.eval_runs;
  },

  startEvalRun: async (payload: {
    model_id: string;
    task_set_id: string;
    trials?: number;
  }): Promise<EvalRun> => {
    const data = await request<{ eval_run: EvalRun }>('/evals/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return data.eval_run;
  },

  listNotifications: async (opts?: {
    projectId?: string;
    unreadOnly?: boolean;
    limit?: number;
  }): Promise<InboxNotificationsResponse> => {
    const params = new URLSearchParams();
    if (opts?.projectId) params.set('project_id', opts.projectId);
    if (opts?.unreadOnly) params.set('unread_only', 'true');
    if (opts?.limit != null) params.set('limit', String(opts.limit));
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return request(`/notifications${suffix}`);
  },

  markNotificationRead: async (notificationId: number): Promise<InboxNotificationsResponse & { notification: InboxNotification }> =>
    request(`/notifications/${notificationId}/read`, { method: 'POST' }),

  markAllNotificationsRead: async (projectId?: string): Promise<{ updated: number; unread_count: InboxNotificationsResponse['unread_count'] }> => {
    const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    return request(`/notifications/read-all${suffix}`, { method: 'POST' });
  },

  listRunEvents: async (opts: {
    projectId: string;
    after?: number;
    limit?: number;
    ticketId?: string;
  }): Promise<RunEvent[]> => {
    const params = new URLSearchParams({ project_id: opts.projectId });
    if (opts.after != null) params.set('after', String(opts.after));
    if (opts.limit != null) params.set('limit', String(opts.limit));
    if (opts.ticketId) params.set('ticket_id', opts.ticketId);
    const data = await request<{ events: RunEvent[] }>(`/run-events?${params.toString()}`);
    return data.events;
  },

  getChatReasonerConfig: async (): Promise<{ mode: 'cloud' | 'local' }> =>
    request('/config/chat-reasoner'),

  updateChatReasonerConfig: async (
    mode: 'cloud' | 'local',
  ): Promise<{ mode: 'cloud' | 'local' }> =>
    request('/config/chat-reasoner', {
      method: 'PUT',
      body: JSON.stringify({ mode }),
    }),

  seedDemoProject: async (): Promise<DemoSeedResult> => {
    const data = await request<{ project: DemoSeedResult['project']; requirement: DemoSeedResult['requirement']; proposed_tickets: DemoSeedResult['proposed_tickets'] }>(
      '/demo/seed',
      { method: 'POST' },
    );
    return data;
  },

  listRequirementTemplates: async (): Promise<RequirementTemplate[]> => {
    const data = await request<{ templates: RequirementTemplate[] }>('/requirement-templates');
    return data.templates;
  },

  saveRequirementTemplate: async (payload: {
    id?: string;
    title: string;
    prompt: string;
    scope_paths?: string[];
    constraints?: string[];
  }): Promise<RequirementTemplate> => {
    const data = await request<{ template: RequirementTemplate }>('/requirement-templates', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    return data.template;
  },

  deleteRequirementTemplate: async (templateId: string): Promise<void> => {
    await request(`/requirement-templates/${encodeURIComponent(templateId)}`, { method: 'DELETE' });
  },

  getRequirementSummary: async (requirementId: string): Promise<RequirementShareSummary> => {
    const data = await request<{ summary: RequirementShareSummary }>(
      `/requirements/${encodeURIComponent(requirementId)}/summary`,
    );
    return data.summary;
  },

  ticketLogsWs: (ticketId: string, projectId?: string): WebSocket => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const params = new URLSearchParams();
    if (projectId) params.set('project_id', projectId);
    const token = getStoredApiToken();
    if (token) params.set('token', token);
    const suffix = params.toString() ? `?${params.toString()}` : '';
    return new WebSocket(`${protocol}//${window.location.host}${API_PREFIX}/tickets/${ticketId}/logs${suffix}`);
  },
};
