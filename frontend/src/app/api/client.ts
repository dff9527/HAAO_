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
} from './types';

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

export interface AutoWorkerStatus {
  running: boolean;
  interval_sec: number;
  max_cycles_per_tick: number;
  allow_dirty_workspace: boolean;
  last_started_at: string | null;
  last_run_at: string | null;
  last_error: string;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });

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

  ticketLogsWs: (ticketId: string, projectId?: string): WebSocket => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return new WebSocket(`${protocol}//${window.location.host}${API_PREFIX}/tickets/${ticketId}/logs${projectQuery(projectId)}`);
  },
};
