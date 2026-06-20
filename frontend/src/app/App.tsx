import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DndProvider } from 'react-dnd';
import { HTML5Backend } from 'react-dnd-html5-backend';
import { TopBar } from './components/TopBar';
import { NavSidebar } from './components/NavSidebar';
import { KanbanBoard } from './components/KanbanBoard';
import { BoardToolbar } from './components/BoardToolbar';
import { TicketDetail } from './components/TicketDetail';
import { RequirementComposer } from './components/RequirementComposer';
import { RequirementSummaryModal } from './components/RequirementSummaryModal';
import { NewTicketModal } from './components/NewTicketModal';
import { ToastStack, type ToastMessage } from './components/Toast';
import { ModelsPage } from './components/ModelsPage';
import { RequirementsPage } from './components/RequirementsPage';
import { INITIAL_MODEL_CONFIGS, INITIAL_ROLE_ROUTES } from './data/modelsData';
import { INITIAL_REQUIREMENTS, INITIAL_TICKETS } from './data/mockData';
import type { AssignedModel, ModelConfig, Project, RequirementSource, RoleRoute, Ticket, TicketStatus } from './types';
import type { Page } from './components/NavSidebar';
import { apiClient, type AutoWorkerStatus, type CloudReasonerConfig } from './api/client';
import { roleRoutingToRoutes, routesToRoleRouting, devTeamChainFromRouting, toBackendStatus, toUiProject, toUiTicket, toUiRequirement } from './api/adapter';
import type { BackendLocalModelEndpoint, BackendManualTicketCreateRequest, BackendTicket } from './api/types';
import { DEFAULT_LOCAL_MODELS } from './constants';
import { boardHasActiveWork, countNeedsAttention } from './ticketAttention';
import { EMPTY_BOARD_FILTERS, matchesBoardFilters, type BoardFilters } from './boardFilters';
import { useBoardKeyboardShortcuts } from './useBoardKeyboardShortcuts';

export default function App() {
  const [currentPage, setCurrentPage] = useState<Page>('board');
  const [darkMode, setDarkMode] = useState(false);
  const [tickets, setTickets] = useState<Ticket[]>(INITIAL_TICKETS);
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null);
  const [showNewReq, setShowNewReq] = useState(false);
  const [showNewTicket, setShowNewTicket] = useState(false);
  const [ticketsLoading, setTicketsLoading] = useState(true);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [requirements, setRequirements] = useState<RequirementSource[]>(INITIAL_REQUIREMENTS);
  const [viewingRequirementId, setViewingRequirementId] = useState<string | null>(null);
  const [modelConfigs, setModelConfigs] = useState<ModelConfig[]>(INITIAL_MODEL_CONFIGS);
  const [roleRoutes, setRoleRoutes] = useState<RoleRoute[]>(INITIAL_ROLE_ROUTES);
  const [localModelIds, setLocalModelIds] = useState<string[]>(DEFAULT_LOCAL_MODELS);
  const [localModelEndpoints, setLocalModelEndpoints] = useState<BackendLocalModelEndpoint[]>([]);
  const [claudeModel, setClaudeModel] = useState('claude-sonnet-4-6');
  const [cloudReasoner, setCloudReasoner] = useState<CloudReasonerConfig | null>(null);
  const [devFallbackChain, setDevFallbackChain] = useState<string[]>(DEFAULT_LOCAL_MODELS.slice(0, 1));
  const [notificationWebhook, setNotificationWebhook] = useState('');
  const [usingMockData, setUsingMockData] = useState(false);
  const [attentionFilter, setAttentionFilter] = useState(false);
  const [boardFilters, setBoardFilters] = useState<BoardFilters>(EMPTY_BOARD_FILTERS);
  const [boardLive, setBoardLive] = useState(false);
  const [workerStatus, setWorkerStatus] = useState<AutoWorkerStatus | null>(null);
  const [workerPending, setWorkerPending] = useState(false);
  const boardSearchRef = useRef<HTMLInputElement>(null);
  const [projects, setProjects] = useState<Project[]>([
    { id: 'default', name: 'HAAO', path: '', defaultBranch: 'main', env: {}, setupCmd: '', cleanupCmd: '' },
  ]);
  const [selectedProjectId, setSelectedProjectId] = useState('default');

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );
  const projectPathReady = Boolean(selectedProject?.path?.trim());
  const modelsConfigured = localModelEndpoints.length > 0;
  const attentionCount = useMemo(() => countNeedsAttention(tickets), [tickets]);
  const filteredTicketCount = useMemo(
    () => tickets.filter((ticket) => matchesBoardFilters(ticket, boardFilters, attentionFilter)).length,
    [attentionFilter, boardFilters, tickets],
  );
  const hasActiveBoardWork = useMemo(() => boardHasActiveWork(tickets), [tickets]);

  const selectedTicket = useMemo(
    () => tickets.find((ticket) => ticket.id === selectedTicketId) ?? null,
    [selectedTicketId, tickets],
  );

  const projectNameById = useMemo(
    () => Object.fromEntries(projects.map((project) => [project.id, project.name])),
    [projects],
  );

  const pushToast = useCallback((text: string, tone: ToastMessage['tone'] = 'error') => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setToasts((prev) => [...prev, { id, text, tone }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((toast) => toast.id !== id));
    }, 5000);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const closeTopOverlay = useCallback(() => {
    if (selectedTicketId) {
      setSelectedTicketId(null);
      return;
    }
    if (showNewReq) {
      setShowNewReq(false);
      return;
    }
    if (showNewTicket) {
      setShowNewTicket(false);
    }
  }, [selectedTicketId, showNewReq, showNewTicket]);

  useBoardKeyboardShortcuts({
    enabled: currentPage === 'board',
    searchInputRef: boardSearchRef,
    onCloseOverlay: closeTopOverlay,
    onFocusSearch: () => setCurrentPage('board'),
  });

  function messageFromError(error: unknown, fallback: string): string {
    return error instanceof Error && error.message ? error.message : fallback;
  }

  const replaceTicketFromBackend = useCallback((backendTicket: BackendTicket) => {
    const projectId = typeof backendTicket.metadata?.project_id === 'string'
      ? backendTicket.metadata.project_id
      : selectedProjectId;
    const ui = toUiTicket(backendTicket, projectNameById[projectId]);
    setTickets((prev) => {
      const index = prev.findIndex((ticket) => ticket.id === ui.id);
      if (index === -1) return [...prev, ui];
      const next = [...prev];
      next[index] = ui;
      return next;
    });
  }, [projectNameById, selectedProjectId]);

  const loadTickets = useCallback(async () => {
    setTicketsLoading(true);
    try {
      const backendTickets = await apiClient.listTickets(selectedProjectId);
      setTickets(
        backendTickets.map((ticket) => {
          const projectId = typeof ticket.metadata?.project_id === 'string'
            ? ticket.metadata.project_id
            : selectedProjectId;
          return toUiTicket(ticket, projectNameById[projectId]);
        }),
      );
      setUsingMockData(false);
    } catch {
      setUsingMockData(true);
    } finally {
      setTicketsLoading(false);
    }
  }, [projectNameById, selectedProjectId]);

  const refreshTickets = useCallback(async () => {
    if (usingMockData) return;
    try {
      const backendTickets = await apiClient.listTickets(selectedProjectId);
      setTickets(
        backendTickets.map((ticket) => {
          const projectId = typeof ticket.metadata?.project_id === 'string'
            ? ticket.metadata.project_id
            : selectedProjectId;
          return toUiTicket(ticket, projectNameById[projectId]);
        }),
      );
    } catch {
      // Keep the last known board state during transient refresh failures.
    }
  }, [projectNameById, selectedProjectId, usingMockData]);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode);
  }, [darkMode]);

  useEffect(() => {
    if (currentPage !== 'board') setSelectedTicketId(null);
  }, [currentPage]);

  useEffect(() => {
    loadTickets();
  }, [loadTickets]);

  // Load persisted requirements for the active project from the backend.
  // Without this the Requirements page only reflected in-memory session state,
  // so requirements from earlier sessions / other projects appeared to vanish.
  useEffect(() => {
    if (usingMockData) return;
    let active = true;
    apiClient
      .listRequirements(selectedProjectId)
      .then((reqs) => {
        if (active) setRequirements(reqs.map(toUiRequirement));
      })
      .catch(() => {
        // Keep the last known requirements on transient failures.
      });
    return () => {
      active = false;
    };
  }, [selectedProjectId, usingMockData]);

  useEffect(() => {
    if (currentPage !== 'board' || usingMockData) {
      setBoardLive(false);
      return;
    }

    const pollActive = hasActiveBoardWork || tickets.length > 0;
    setBoardLive(pollActive);
    if (!pollActive) return;

    const intervalId = window.setInterval(() => {
      void refreshTickets();
    }, 4000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [currentPage, hasActiveBoardWork, refreshTickets, tickets.length, usingMockData]);

  useEffect(() => {
    if (attentionCount === 0 && attentionFilter) {
      setAttentionFilter(false);
    }
  }, [attentionCount, attentionFilter]);

  useEffect(() => {
    if (currentPage !== 'board' || usingMockData) return;
    let active = true;
    const poll = () => {
      apiClient
        .getWorkerStatus()
        .then((status) => {
          if (active) setWorkerStatus(status);
        })
        .catch(() => {});
    };
    poll();
    const intervalId = window.setInterval(poll, 5000);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [currentPage, usingMockData]);

  // Auto-run follows the active board project: if the worker is running but
  // bound to a different project, rebind it to the selected one so the new
  // project's tickets keep progressing without a manual stop/start.
  useEffect(() => {
    if (usingMockData) return;
    if (!workerStatus?.running) return;
    if (workerStatus.project_id === selectedProjectId) return;
    let cancelled = false;
    apiClient
      .startWorker(selectedProjectId, { allow_dirty_workspace: workerStatus.allow_dirty_workspace })
      .then((status) => {
        if (!cancelled) setWorkerStatus(status);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, workerStatus?.running, workerStatus?.project_id, workerStatus?.allow_dirty_workspace, usingMockData]);

  const handleToggleAutoRun = useCallback(async () => {
    setWorkerPending(true);
    try {
      const next = workerStatus?.running
        ? await apiClient.stopWorker()
        : await apiClient.startWorker(selectedProjectId, { allow_dirty_workspace: false });
      setWorkerStatus(next);
      pushToast(next.running ? 'Auto-run started.' : 'Auto-run stopped.', 'success');
    } catch (error) {
      pushToast(messageFromError(error, 'Could not toggle auto-run.'));
    } finally {
      setWorkerPending(false);
    }
  }, [pushToast, selectedProjectId, workerStatus?.running]);

  useEffect(() => {
    apiClient
      .listProjects()
      .then((backendProjects) => {
        const uiProjects = backendProjects.map(toUiProject);
        setProjects(uiProjects);
        setSelectedProjectId((current) =>
          uiProjects.some((project) => project.id === current) ? current : uiProjects[0]?.id ?? 'default',
        );
      })
      .catch(() => {
      });
  }, []);

  useEffect(() => {
    apiClient
      .getClaudeModel()
      .then((model) => {
        setClaudeModel(model);
        setModelConfigs((prev) => syncClaudeModelConfig(prev, model));
      })
      .catch(() => {
      });
    apiClient
      .getCloudReasoner()
      .then((config) => {
        setCloudReasoner(config);
      })
      .catch(() => {
        setCloudReasoner(null);
      });
  }, []);

  useEffect(() => {
    apiClient
      .getRoleRouting()
      .then((routing) => {
        setRoleRoutes(roleRoutingToRoutes(routing));
        setDevFallbackChain(devTeamChainFromRouting(routing));
      })
      .catch(() => {
      });
    apiClient
      .getNotificationSettings()
      .then((webhookUrl) => {
        setNotificationWebhook(webhookUrl);
      })
      .catch(() => {
      });
  }, []);

  useEffect(() => {
    apiClient
      .listLocalModelEndpoints()
      .then((endpoints) => {
        setLocalModelEndpoints(endpoints);
      })
      .catch(() => {
      });
    apiClient
      .listAvailableLocalModels()
      .then(({ models, endpoints }) => {
        const nextModels = models.length ? models : DEFAULT_LOCAL_MODELS;
        setLocalModelIds(nextModels);
        setLocalModelEndpoints(endpoints);
        setModelConfigs((prev) => mergeLocalModelConfigs(prev, nextModels));
      })
      .catch(() => {
        setLocalModelIds(DEFAULT_LOCAL_MODELS);
      });
  }, []);

  useEffect(() => {
    if (!selectedTicketId) return;
    apiClient
      .getTicket(selectedTicketId, selectedProjectId)
      .then((ticket) => {
        replaceTicketFromBackend(ticket);
      })
      .catch(() => {
      });

    let ws: WebSocket | null = null;
    try {
      ws = apiClient.ticketLogsWs(selectedTicketId, selectedProjectId);
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as { ts: string; level: string; message: string };
          setTickets((prev) =>
            prev.map((ticket) =>
              ticket.id === selectedTicketId
                ? {
                    ...ticket,
                    agentLog: [
                      ...ticket.agentLog,
                      {
                        time: payload.ts,
                        level: payload.level === 'warn' || payload.level === 'error' ? payload.level : 'info',
                        message: payload.message,
                      },
                    ],
                  }
                : ticket,
            ),
          );
        } catch {
          return;
        }
      };
    } catch {
      return;
    }
    return () => {
      ws?.close();
    };
  }, [replaceTicketFromBackend, selectedProjectId, selectedTicketId]);

  const runAndReplace = useCallback(
    async (
      ticketId: string,
      call: (id: string, projectId?: string) => Promise<BackendTicket>,
      successMessage?: string,
    ) => {
      try {
        const updated = await call(ticketId, selectedProjectId);
        replaceTicketFromBackend(updated);
        if (successMessage) pushToast(successMessage, 'success');
      } catch (error) {
        pushToast(messageFromError(error, 'Action failed.'));
      }
    },
    [pushToast, replaceTicketFromBackend, selectedProjectId],
  );

  const handleMoveTicket = useCallback(
    async (ticketId: string, newStatus: TicketStatus) => {
      const previous = tickets;
      setTickets((prev) => prev.map((ticket) => (ticket.id === ticketId ? { ...ticket, status: newStatus } : ticket)));
      try {
        const updated = await apiClient.moveTicket(ticketId, toBackendStatus(newStatus), selectedProjectId);
        replaceTicketFromBackend(updated);
      } catch (error) {
        setTickets(previous);
        pushToast(messageFromError(error, 'Could not move ticket.'));
      }
    },
    [pushToast, replaceTicketFromBackend, selectedProjectId, tickets],
  );

  const handleUpdateTicket = useCallback(
    async (ticketId: string, updates: Partial<Ticket>) => {
      setTickets((prev) => prev.map((ticket) => (ticket.id === ticketId ? { ...ticket, ...updates } : ticket)));
      if (!updates.assignedModel) return;
      const model = updates.assignedModel === 'Claude · Tech Lead' ? 'claude-tech-lead' : updates.assignedModel;
      try {
        const updated = await apiClient.assignModel(ticketId, model, selectedProjectId);
        replaceTicketFromBackend(updated);
      } catch (error) {
        pushToast(messageFromError(error, 'Could not assign model.'));
      }
    },
    [replaceTicketFromBackend, selectedProjectId],
  );

  const handleRetry = useCallback(
    (ticketId: string) => runAndReplace(ticketId, apiClient.retryTicket, 'Retry started.'),
    [runAndReplace],
  );
  const handleRun = useCallback(
    (ticketId: string) => runAndReplace(ticketId, apiClient.executeTicket, 'Work started.'),
    [runAndReplace],
  );
  const handleCancel = useCallback(
    (ticketId: string) => runAndReplace(ticketId, apiClient.cancelTicket, 'Run stopped.'),
    [runAndReplace],
  );
  const handleApproveDiff = useCallback(
    (ticketId: string) => runAndReplace(ticketId, apiClient.approveDiff, 'Changes approved.'),
    [runAndReplace],
  );
  const handleMerge = useCallback(
    (ticketId: string) => runAndReplace(ticketId, apiClient.mergeTicket, 'Branch merged.'),
    [runAndReplace],
  );
  const handleRevert = useCallback(
    (ticketId: string) => runAndReplace(ticketId, apiClient.revertTicket, 'Merge reverted.'),
    [runAndReplace],
  );
  const handleRejectDiff = useCallback(
    (ticketId: string, feedback: string) => runAndReplace(
      ticketId,
      (id, projectId) => apiClient.rejectDiff(id, feedback, projectId),
      'Changes sent back for rework.',
    ),
    [runAndReplace],
  );
  const handleUpdateAndRerun = useCallback(
    async (ticketId: string, payload: {
      task_description?: string;
      dod_tests?: string[];
      assigned_model?: string;
    }) => {
      try {
        const model = payload.assigned_model
          ? payload.assigned_model === 'Claude · Tech Lead'
            ? 'claude-tech-lead'
            : payload.assigned_model
          : undefined;
        const updated = await apiClient.updateTicket(
          ticketId,
          { ...payload, assigned_model: model, rerun: true },
          selectedProjectId,
        );
        replaceTicketFromBackend(updated);
      } catch (error) {
        pushToast(messageFromError(error, 'Could not save and rerun ticket.'));
      }
    },
    [replaceTicketFromBackend, selectedProjectId],
  );
  const handleApprove = useCallback((ticketId: string) => {
    const previous = tickets;
    setTickets((prev) =>
      prev.map((ticket) =>
        ticket.id === ticketId
          ? { ...ticket, status: 'Ready' as TicketStatus, needsApproval: false, autoDispatched: true }
          : ticket,
      ),
    );
    void apiClient
      .approveTicket(ticketId, selectedProjectId)
      .then((updated) => {
        replaceTicketFromBackend(updated);
        pushToast('Ticket approved for development.', 'success');
      })
      .catch((error) => {
        setTickets(previous);
        pushToast(messageFromError(error, 'Gate 1 approve failed.'));
      });
  }, [pushToast, replaceTicketFromBackend, selectedProjectId, tickets]);

  const handleAccept = useCallback((ticketId: string) => {
    const previous = tickets;
    setTickets((prev) =>
      prev.map((ticket) =>
        ticket.id === ticketId ? { ...ticket, status: 'Done' as TicketStatus, awaitingAcceptance: false } : ticket,
      ),
    );
    void apiClient
      .acceptTicket(ticketId, selectedProjectId)
      .then((updated) => {
        replaceTicketFromBackend(updated);
        pushToast('Ticket accepted and closed.', 'success');
      })
      .catch((error) => {
        setTickets(previous);
        pushToast(messageFromError(error, 'Gate 2 accept failed.'));
      });
  }, [pushToast, replaceTicketFromBackend, selectedProjectId, tickets]);
  const handleEscalate = useCallback(
    (ticketId: string) => runAndReplace(ticketId, (id, projectId) => apiClient.escalateTicket(id, { reason: 'manual_escalation' }, projectId)),
    [runAndReplace],
  );
  const handleReject = useCallback(
    (ticketId: string, feedback: string) => runAndReplace(
      ticketId,
      (id, projectId) => apiClient.rejectTicket(id, feedback, projectId),
      'Ticket sent back to Backlog.',
    ),
    [runAndReplace],
  );

  const handleDelete = useCallback(async (ticketId: string, force = false) => {
    try {
      await apiClient.deleteTicket(ticketId, force, selectedProjectId);
      setTickets((prev) => prev.filter((ticket) => ticket.id !== ticketId));
      setSelectedTicketId(null);
      pushToast('Ticket deleted.', 'success');
    } catch (error) {
      pushToast(messageFromError(error, 'Could not delete ticket.'));
    }
  }, [pushToast, selectedProjectId]);

  const handleCreateProject = useCallback(async (payload: { name: string; path: string }) => {
    const project = toUiProject(await apiClient.createProject(payload));
    setProjects((prev) => [...prev.filter((item) => item.id !== project.id), project]);
    setSelectedProjectId(project.id);
  }, []);

  const handleDeleteProject = useCallback(async (projectId: string) => {
    await apiClient.deleteProject(projectId);
    setProjects((prev) => {
      const next = prev.filter((project) => project.id !== projectId);
      setSelectedProjectId((current) =>
        current === projectId ? next[0]?.id ?? 'default' : current,
      );
      return next;
    });
    setSelectedTicketId(null);
  }, []);

  const handleUpdateProjectSettings = useCallback(
    async (
      projectId: string,
      payload: {
        env: Record<string, string>;
        setupCmd: string;
        cleanupCmd: string;
        defaultBranch: string;
      },
    ) => {
      const project = toUiProject(
        await apiClient.updateProjectSettings(projectId, {
          env: payload.env,
          setup_cmd: payload.setupCmd,
          cleanup_cmd: payload.cleanupCmd,
          default_branch: payload.defaultBranch,
        }),
      );
      setProjects((prev) => prev.map((item) => (item.id === project.id ? project : item)));
    },
    [],
  );

  const handleCreateManualTicket = useCallback(async (payload: BackendManualTicketCreateRequest) => {
    const backendTicket = await apiClient.createManualTicket(payload);
    replaceTicketFromBackend(backendTicket);
    pushToast('Ticket created and added to Ready.', 'success');
  }, [pushToast, replaceTicketFromBackend]);

  const handleSaveModelAdditionalInstructions = useCallback(async (modelId: string, text: string) => {
    const saved = await apiClient.updateModelAdditionalInstructions(modelId, text);
    return saved;
  }, []);

  const handleLoadModelAdditionalInstructions = useCallback(async (modelId: string) => {
    const text = await apiClient.getModelAdditionalInstructions(modelId);
    return text;
  }, []);

  const handleUpdateModel = useCallback((id: AssignedModel, updates: Partial<ModelConfig>) => {
    setModelConfigs((prev) => prev.map((model) => (model.id === id ? { ...model, ...updates } : model)));
  }, []);

  const handleUpdateRoute = useCallback((routeId: string, model: AssignedModel) => {
    setRoleRoutes((prev) => {
      const next = prev.map((route) => (route.id === routeId ? { ...route, model } : route));
      void apiClient
        .updateRoleRouting(routesToRoleRouting(next, devFallbackChain))
        .then((routing) => {
          setRoleRoutes(roleRoutingToRoutes(routing));
          setDevFallbackChain(devTeamChainFromRouting(routing));
        })
        .catch((error) => {
          pushToast(messageFromError(error, 'Could not save role routing.'));
        });
      return next;
    });
  }, [devFallbackChain, pushToast]);

  const handleUpdateDevFallbackChain = useCallback((chain: string[]) => {
    const normalized = chain.length ? chain : devFallbackChain;
    setDevFallbackChain(normalized);
    void apiClient
      .updateRoleRouting(routesToRoleRouting(roleRoutes, normalized))
      .then((routing) => {
        setRoleRoutes(roleRoutingToRoutes(routing));
        setDevFallbackChain(devTeamChainFromRouting(routing));
      })
      .catch((error) => {
        pushToast(messageFromError(error, 'Could not save dev fallback chain.'));
      });
  }, [devFallbackChain, pushToast, roleRoutes]);

  const handleSaveNotificationWebhook = useCallback(async (webhookUrl: string) => {
    const saved = await apiClient.updateNotificationSettings(webhookUrl);
    setNotificationWebhook(saved);
    return saved;
  }, []);

  const handleSaveLocalModelEndpoints = useCallback(async (endpoints: BackendLocalModelEndpoint[]) => {
    const saved = await apiClient.updateLocalModelEndpoints(endpoints);
    setLocalModelEndpoints(saved);
    const available = await apiClient.listAvailableLocalModels();
    const nextModels = available.models.length ? available.models : DEFAULT_LOCAL_MODELS;
    setLocalModelIds(nextModels);
    setLocalModelEndpoints(available.endpoints);
    setModelConfigs((prev) => mergeLocalModelConfigs(prev, nextModels));
  }, []);

  const handleRefreshLocalModels = useCallback(async () => {
    const available = await apiClient.listAvailableLocalModels();
    const nextModels = available.models.length ? available.models : DEFAULT_LOCAL_MODELS;
    setLocalModelIds(nextModels);
    setLocalModelEndpoints(available.endpoints);
    setModelConfigs((prev) => mergeLocalModelConfigs(prev, nextModels));
    return available;
  }, []);

  const handleSaveCloudReasoner = useCallback(async (modelId: string) => {
    const config = await apiClient.updateCloudReasoner(modelId);
    setCloudReasoner(config);
    return config;
  }, []);

  return (
    <DndProvider backend={HTML5Backend}>
      <div className="h-screen flex flex-col bg-background text-foreground overflow-hidden">
        <TopBar
          darkMode={darkMode}
          onToggleDark={() => setDarkMode((value) => !value)}
          onNewReq={() => setShowNewReq(true)}
          onNewTicket={() => setShowNewTicket(true)}
          showBoardControls={currentPage === 'board'}
          boardLive={boardLive}
          projects={projects}
          selectedProjectId={selectedProjectId}
          onSelectProject={setSelectedProjectId}
          onCreateProject={handleCreateProject}
          onDeleteProject={handleDeleteProject}
          onUpdateProjectSettings={handleUpdateProjectSettings}
        />
        {usingMockData && (
          <div className="px-4 py-2 text-xs bg-amber-50 text-amber-800 border-b border-amber-200 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-900">
            Cannot reach the API — showing demo ticket data. Start the backend on port 8000 to sync live tickets.
          </div>
        )}

        <div className="flex flex-1 overflow-hidden flex-col min-w-0">
          <div className="flex flex-1 overflow-hidden min-h-0">
            <NavSidebar currentPage={currentPage} onNavigate={setCurrentPage} />
            {currentPage === 'board' ? (
              <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
                <BoardToolbar
                  ref={boardSearchRef}
                  attentionCount={attentionCount}
                  attentionFilter={attentionFilter}
                  onToggleAttentionFilter={() => setAttentionFilter((value) => !value)}
                  boardLive={boardLive}
                  autoRunRunning={Boolean(workerStatus?.running)}
                  autoRunPending={workerPending}
                  autoRunError={workerStatus?.last_error ?? ''}
                  onToggleAutoRun={handleToggleAutoRun}
                  projectPathReady={projectPathReady}
                  modelsConfigured={modelsConfigured}
                  ticketCount={tickets.length}
                  filteredCount={filteredTicketCount}
                  loading={ticketsLoading}
                  filters={boardFilters}
                  onFiltersChange={setBoardFilters}
                  onClearFilters={() => setBoardFilters(EMPTY_BOARD_FILTERS)}
                  onOpenSetup={() => setCurrentPage('models')}
                />
                <KanbanBoard
                  tickets={tickets}
                  loading={ticketsLoading}
                  selectedTicketId={selectedTicketId}
                  attentionFilter={attentionFilter}
                  boardFilters={boardFilters}
                  onMoveTicket={handleMoveTicket}
                  onSelectTicket={setSelectedTicketId}
                  onApproveTicket={handleApprove}
                  onAcceptTicket={handleAccept}
                />
              </div>
            ) : currentPage === 'requirements' ? (
              <RequirementsPage
                requirements={requirements}
                tickets={tickets}
                onOpenRequirement={setViewingRequirementId}
                onNewRequirement={() => setShowNewReq(true)}
              />
            ) : (
              <ModelsPage
                modelConfigs={modelConfigs}
              roleRoutes={roleRoutes}
              localModelIds={localModelIds}
              localModelEndpoints={localModelEndpoints}
              cloudReasoner={cloudReasoner}
              devFallbackChain={devFallbackChain}
              notificationWebhook={notificationWebhook}
              onUpdateModel={handleUpdateModel}
              onUpdateRoute={handleUpdateRoute}
              onUpdateDevFallbackChain={handleUpdateDevFallbackChain}
              onSaveLocalModelEndpoints={handleSaveLocalModelEndpoints}
              onRefreshLocalModels={handleRefreshLocalModels}
              onSaveCloudReasoner={handleSaveCloudReasoner}
              onSaveNotificationWebhook={handleSaveNotificationWebhook}
              onLoadModelAdditionalInstructions={handleLoadModelAdditionalInstructions}
              onSaveModelAdditionalInstructions={handleSaveModelAdditionalInstructions}
            />
          )}
          </div>
        </div>

        <footer className="shrink-0 border-t border-border bg-card px-4 py-1.5 flex items-center justify-center sm:justify-between gap-2">
          <span className="text-[11px] text-muted-foreground">
            © {new Date().getFullYear()} HAAO · Hybrid AI-Agile Orchestrator
          </span>
          <span className="hidden sm:inline text-[11px] text-muted-foreground/70">
            Designed &amp; built by HAAO
          </span>
        </footer>

        {selectedTicket && (
          <TicketDetail
            ticket={selectedTicket}
            onClose={() => setSelectedTicketId(null)}
            onUpdate={(updates) => handleUpdateTicket(selectedTicket.id, updates)}
            onMove={(status) => handleMoveTicket(selectedTicket.id, status)}
            onRetry={() => handleRetry(selectedTicket.id)}
            onRun={() => handleRun(selectedTicket.id)}
            onCancel={() => handleCancel(selectedTicket.id)}
            onApproveDiff={() => handleApproveDiff(selectedTicket.id)}
            onRejectDiff={(feedback) => handleRejectDiff(selectedTicket.id, feedback)}
            onMerge={() => handleMerge(selectedTicket.id)}
            onRevert={() => handleRevert(selectedTicket.id)}
            onUpdateAndRerun={(payload) => handleUpdateAndRerun(selectedTicket.id, payload)}
            onApprove={() => handleApprove(selectedTicket.id)}
            onAccept={() => handleAccept(selectedTicket.id)}
            onReject={(feedback) => handleReject(selectedTicket.id, feedback)}
            onDelete={(force) => handleDelete(selectedTicket.id, force)}
            onEscalate={() => handleEscalate(selectedTicket.id)}
            requirementSource={
              selectedTicket.requirementId
                ? requirements.find((requirement) => requirement.id === selectedTicket.requirementId)
                : undefined
            }
            onViewRequirement={() => setViewingRequirementId(selectedTicket.requirementId ?? null)}
            localModelIds={localModelIds}
            projectPathReady={projectPathReady}
          />
        )}

        {showNewReq && (
          <RequirementComposer
            onClose={() => setShowNewReq(false)}
            onAddTickets={(newTickets) => setTickets((prev) => [...prev, ...newTickets])}
            onAddRequirement={(req) => setRequirements((prev) =>
              prev.some((r) => r.id === req.id)
                ? prev.map((r) => (r.id === req.id ? req : r))
                : [...prev, req],
            )}
            requirementCount={requirements.length}
            currentTicketCount={tickets.length}
            onDecomposeRequirement={apiClient.decomposeRequirement}
            onConfirmRequirement={apiClient.confirmRequirement}
            onDiscardRequirement={apiClient.discardRequirement}
            projects={projects}
            selectedProjectId={selectedProjectId}
            onSelectProject={setSelectedProjectId}
            onGetProjectConventions={apiClient.getProjectConventions}
            localModelIds={localModelIds}
            projectPathReady={projectPathReady}
            onDecomposeError={(message) => pushToast(message, 'error')}
          />
        )}

        {showNewTicket && (
          <NewTicketModal
            onClose={() => setShowNewTicket(false)}
            projects={projects}
            selectedProjectId={selectedProjectId}
            localModelIds={localModelIds}
            projectPathReady={projectPathReady}
            onCreate={handleCreateManualTicket}
          />
        )}

        {viewingRequirementId && (() => {
          const req = requirements.find((item) => item.id === viewingRequirementId);
          return req ? <RequirementSummaryModal requirement={req} onClose={() => setViewingRequirementId(null)} /> : null;
        })()}

        <ToastStack toasts={toasts} onDismiss={dismissToast} />
      </div>
    </DndProvider>
  );
}

function mergeLocalModelConfigs(current: ModelConfig[], modelIds: string[]): ModelConfig[] {
  const cloudConfigs = current.filter((config) => config.backend === 'Cloud API');
  const existingById = new Map(current.map((config) => [config.id, config]));
  const localConfigs = modelIds.map((modelId) => existingById.get(modelId) ?? createLocalModelConfig(modelId));
  return [...localConfigs, ...cloudConfigs];
}

function createLocalModelConfig(modelId: string): ModelConfig {
  return {
    id: modelId,
    name: modelId,
    backend: 'LM Studio (local)',
    contextWindow: 32768,
    status: 'Loaded',
    params: {
      temperature: 0.2,
      topP: 0.95,
      maxOutputTokens: 4096,
      contextWindowCap: 32768,
      defaultRetryBudget: 3,
      systemPrompt: '',
      additionalInstructions: '',
      fullPromptOverride: '',
      useFullPromptOverride: false,
    },
  };
}

function syncClaudeModelConfig(current: ModelConfig[], claudeModel: string): ModelConfig[] {
  return current.map((config) =>
    config.backend === 'Cloud API'
      ? {
          ...config,
          name: claudeModel || config.name,
        }
      : config,
  );
}
