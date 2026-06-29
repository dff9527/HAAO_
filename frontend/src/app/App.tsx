import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';
import { DndProvider } from 'react-dnd';
import { HTML5Backend } from 'react-dnd-html5-backend';
import { TopBar } from './components/TopBar';
import { NavSidebar } from './components/NavSidebar';
import { KanbanBoard } from './components/KanbanBoard';
import { ChatPanel, type ChatLayoutMode } from './components/ChatPanel';
import { BoardToolbar } from './components/BoardToolbar';
import { DependencyGraphPanel } from './components/DependencyGraphPanel';
import { TicketDetail } from './components/TicketDetail';
import { RequirementComposer } from './components/RequirementComposer';
import { RequirementSummaryModal } from './components/RequirementSummaryModal';
import { NewTicketModal } from './components/NewTicketModal';
import { ToastStack, type ToastMessage } from './components/Toast';
import { ApiTokenPrompt } from './components/ApiTokenPrompt';
import { ModelsPage } from './components/ModelsPage';
import { ActivityPage } from './components/ActivityPage';
import { InsightsPage } from './components/InsightsPage';
import { InboxPage } from './components/InboxPage';
import { DecisionCenterPage } from './components/DecisionCenterPage';
import { OnboardingWizard } from './components/OnboardingWizard';
import { RequirementsPage } from './components/RequirementsPage';
import { INITIAL_MODEL_CONFIGS, INITIAL_ROLE_ROUTES } from './data/modelsData';
import { INITIAL_REQUIREMENTS, INITIAL_TICKETS, INITIAL_CHAT_MESSAGES, INITIAL_CLOUD_MODELS } from './data/mockData';
import type { AssignedModel, ChatMessage, CloudModel, ModelConfig, Project, RequirementSource, RoleRoute, Ticket, TicketStatus } from './types';
import type { Page } from './components/NavSidebar';
import { apiClient, type AutoWorkerStatus, type CloudReasonerConfig, type IntegrationCredential } from './api/client';
import { roleRoutingToRoutes, routesToRoleRouting, devTeamChainFromRouting, toBackendStatus, toUiProject, toUiTicket, toUiRequirement } from './api/adapter';
import type { BackendLocalModelEndpoint, BackendManualTicketCreateRequest, BackendTicket, InboxUnreadCount, TicketGraphPayload, IdentityContext } from './api/types';
import { buildMockGraph, mergeGraphIntoTickets, MOCK_WORKER_STATUS } from './throughputUtils';
import { MOCK_INBOX_UNREAD_COUNT } from './inboxUtils';
import { ONBOARDING_DISMISSED_KEY } from './dxUtils';
import { MOCK_DECISIONS, decisionTotalCount } from './trustUtils';
import { MOCK_IDENTITY_CONTEXT, mockTeamPlaneEnabled } from './teamPlaneUtils';
import { setStoredIdentity } from './api/authIdentity';
import { DEFAULT_LOCAL_MODELS } from './constants';
import { boardHasActiveWork, countNeedsAttention } from './ticketAttention';
import { EMPTY_BOARD_FILTERS, matchesBoardFilters, type BoardFilters } from './boardFilters';
import { useBoardKeyboardShortcuts } from './useBoardKeyboardShortcuts';

export default function App() {
  const [currentPage, setCurrentPage] = useState<Page>('home');
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
  const [cloudModels, setCloudModels] = useState<CloudModel[]>(INITIAL_CLOUD_MODELS);
  const [devFallbackChain, setDevFallbackChain] = useState<string[]>(DEFAULT_LOCAL_MODELS.slice(0, 1));
  const [notificationWebhook, setNotificationWebhook] = useState('');
  const [usingMockData, setUsingMockData] = useState(false);
  const [attentionFilter, setAttentionFilter] = useState(false);
  const [boardFilters, setBoardFilters] = useState<BoardFilters>(EMPTY_BOARD_FILTERS);
  const [boardLive, setBoardLive] = useState(false);
  const [workerStatus, setWorkerStatus] = useState<AutoWorkerStatus | null>(null);
  const [workerPending, setWorkerPending] = useState(false);
  const [showDependencies, setShowDependencies] = useState(false);
  const [ticketGraph, setTicketGraph] = useState<TicketGraphPayload | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const boardSearchRef = useRef<HTMLInputElement>(null);
  const [projects, setProjects] = useState<Project[]>([
    { id: 'default', name: 'HAAO', path: '', defaultBranch: 'main', env: {}, setupCmd: '', cleanupCmd: '' },
  ]);
  const [selectedProjectId, setSelectedProjectId] = useState('default');
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(INITIAL_CHAT_MESSAGES);
  const [chatSending, setChatSending] = useState(false);
  const [chatLayoutMode, setChatLayoutMode] = useState<ChatLayoutMode>('balanced');
  const [allowCloudExecutionModel, setAllowCloudExecutionModel] = useState(false);
  const [integrations, setIntegrations] = useState<IntegrationCredential[]>([]);
  const [identityContext, setIdentityContext] = useState<IdentityContext | null>(null);
  const [inboxUnreadCount, setInboxUnreadCount] = useState<InboxUnreadCount>({ total: 0, by_project: {} });
  const [decisionCount, setDecisionCount] = useState(0);
  const [chatReasonerMode, setChatReasonerMode] = useState<'cloud' | 'local'>('cloud');
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const [onboardingDismissed, setOnboardingDismissed] = useState(
    () => typeof window !== 'undefined' && window.localStorage.getItem(ONBOARDING_DISMISSED_KEY) === '1',
  );
  const [seedingDemo, setSeedingDemo] = useState(false);
  const lastChatMessageIdRef = useRef<string | undefined>(INITIAL_CHAT_MESSAGES.at(-1)?.id);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );
  const projectPathReady = Boolean(selectedProject?.path?.trim());
  const modelsConfigured = localModelEndpoints.length > 0;
  const needsSetup = !projectPathReady || !modelsConfigured;
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

  const prIntegrationConfigured = useMemo(
    () => integrations.some((item) => item.configured && (item.provider === 'github' || item.provider === 'gitlab')),
    [integrations],
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
    enabled: currentPage === 'home',
    searchInputRef: boardSearchRef,
    onCloseOverlay: closeTopOverlay,
    onFocusSearch: () => setCurrentPage('home'),
  });

  function messageFromError(error: unknown, fallback: string): string {
    return error instanceof Error && error.message ? error.message : fallback;
  }

  const replaceTicketsFromBackend = useCallback((backendTickets: BackendTicket[]) => {
    setTickets((prev) => {
      const next = [...prev];
      for (const backendTicket of backendTickets) {
        const projectId = typeof backendTicket.metadata?.project_id === 'string'
          ? backendTicket.metadata.project_id
          : selectedProjectId;
        const ui = toUiTicket(backendTicket, projectNameById[projectId]);
        const index = next.findIndex((ticket) => ticket.id === ui.id);
        if (index === -1) next.push(ui);
        else next[index] = ui;
      }
      return next;
    });
  }, [projectNameById, selectedProjectId]);

  const replaceTicketFromBackend = useCallback((backendTicket: BackendTicket) => {
    replaceTicketsFromBackend([backendTicket]);
  }, [replaceTicketsFromBackend]);

  const loadTicketGraph = useCallback(async (baseTickets: Ticket[]) => {
    if (usingMockData) {
      setTicketGraph(buildMockGraph(baseTickets));
      return baseTickets;
    }
    setGraphLoading(true);
    try {
      const graph = await apiClient.getTicketGraph(selectedProjectId);
      setTicketGraph(graph);
      return mergeGraphIntoTickets(baseTickets, graph);
    } catch {
      setTicketGraph(null);
      return baseTickets;
    } finally {
      setGraphLoading(false);
    }
  }, [selectedProjectId, usingMockData]);

  const loadTickets = useCallback(async () => {
    setTicketsLoading(true);
    try {
      const backendTickets = await apiClient.listTickets(selectedProjectId);
      const uiTickets = backendTickets.map((ticket) => {
        const projectId = typeof ticket.metadata?.project_id === 'string'
          ? ticket.metadata.project_id
          : selectedProjectId;
        return toUiTicket(ticket, projectNameById[projectId]);
      });
      setTickets(await loadTicketGraph(uiTickets));
      setUsingMockData(false);
    } catch {
      const mockTickets = INITIAL_TICKETS;
      setTickets(await loadTicketGraph(mockTickets));
      setUsingMockData(true);
      setWorkerStatus(MOCK_WORKER_STATUS);
    } finally {
      setTicketsLoading(false);
    }
  }, [loadTicketGraph, projectNameById, selectedProjectId]);

  const refreshTickets = useCallback(async () => {
    if (usingMockData) return;
    try {
      const backendTickets = await apiClient.listTickets(selectedProjectId);
      const uiTickets = backendTickets.map((ticket) => {
        const projectId = typeof ticket.metadata?.project_id === 'string'
          ? ticket.metadata.project_id
          : selectedProjectId;
        return toUiTicket(ticket, projectNameById[projectId]);
      });
      setTickets(await loadTicketGraph(uiTickets));
    } catch {
      // Keep the last known board state during transient refresh failures.
    }
  }, [loadTicketGraph, projectNameById, selectedProjectId, usingMockData]);

  const loadChatMessages = useCallback(async () => {
    if (usingMockData) {
      setChatMessages(INITIAL_CHAT_MESSAGES);
      return;
    }
    try {
      const messages = await apiClient.listChatMessages(selectedProjectId);
      setChatMessages(messages);
    } catch {
      setChatMessages(INITIAL_CHAT_MESSAGES);
    }
  }, [selectedProjectId, usingMockData]);

  const refreshChatMessages = useCallback(async () => {
    if (usingMockData) return;
    const after = lastChatMessageIdRef.current;
    try {
      const messages = await apiClient.listChatMessages(selectedProjectId, after ? { after } : undefined);
      if (messages.length) {
        setChatMessages((prev) => [...prev, ...messages]);
      }
    } catch {
      // Keep the last known chat state during transient refresh failures.
    }
  }, [selectedProjectId, usingMockData]);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode);
  }, [darkMode]);

  useEffect(() => {
    if (mockTeamPlaneEnabled()) {
      setIdentityContext(MOCK_IDENTITY_CONTEXT);
      setStoredIdentity(MOCK_IDENTITY_CONTEXT.actor_id, MOCK_IDENTITY_CONTEXT.workspace_id);
      return;
    }
    if (usingMockData) {
      setIdentityContext({
        identity_configured: false,
        actor_id: 'implicit-owner',
        workspace_id: 'default',
        role: 'owner',
        implicit_owner: true,
        permissions: ['read', 'mutate', 'admin'],
      });
      return;
    }
    let active = true;
    apiClient
      .getIdentityContext()
      .then((context) => {
        if (!active) return;
        setIdentityContext(context);
        if (context.identity_configured && !context.implicit_owner) {
          setStoredIdentity(context.actor_id, context.workspace_id);
        }
      })
      .catch(() => {
        if (active) {
          setIdentityContext({
            identity_configured: false,
            actor_id: 'implicit-owner',
            workspace_id: 'default',
            role: 'owner',
            implicit_owner: true,
            permissions: ['read', 'mutate', 'admin'],
          });
        }
      });
    return () => {
      active = false;
    };
  }, [usingMockData]);

  useEffect(() => {
    if (currentPage !== 'home') setSelectedTicketId(null);
  }, [currentPage]);

  useEffect(() => {
    lastChatMessageIdRef.current = chatMessages.at(-1)?.id;
  }, [chatMessages]);

  useEffect(() => {
    loadTickets();
    void loadChatMessages();
  }, [loadTickets, loadChatMessages]);

  useEffect(() => {
    if (currentPage !== 'home' || usingMockData) {
      setBoardLive(false);
      return;
    }

    const pollActive = hasActiveBoardWork || tickets.length > 0 || chatMessages.length > 0;
    setBoardLive(pollActive);
    if (!pollActive) return;

    const intervalId = window.setInterval(() => {
      void refreshTickets();
      void refreshChatMessages();
    }, 5000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [chatMessages.length, currentPage, hasActiveBoardWork, refreshChatMessages, refreshTickets, tickets.length, usingMockData]);

  // Load persisted requirements for the active project from the backend.
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
    if (attentionCount === 0 && attentionFilter) {
      setAttentionFilter(false);
    }
  }, [attentionCount, attentionFilter]);

  useEffect(() => {
    if (usingMockData) {
      setWorkerStatus(MOCK_WORKER_STATUS);
      return;
    }
    if (currentPage !== 'home') return;
    let active = true;
    const poll = () => {
      apiClient
        .getWorkerStatus()
        .then((status) => {
          if (active) {
            setWorkerStatus({
              ...status,
              max_workers: status.max_workers ?? status.worker_statuses?.length ?? 1,
            });
          }
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

  useEffect(() => {
    if (usingMockData) {
      setInboxUnreadCount(MOCK_INBOX_UNREAD_COUNT);
      return;
    }
    let active = true;
    const poll = () => {
      apiClient
        .listNotifications({ projectId: selectedProjectId, limit: 1 })
        .then((data) => {
          if (active) setInboxUnreadCount(data.unread_count);
        })
        .catch(() => {});
    };
    poll();
    const intervalId = window.setInterval(poll, 5000);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [selectedProjectId, usingMockData]);

  useEffect(() => {
    if (usingMockData) {
      setDecisionCount(decisionTotalCount(MOCK_DECISIONS.counts));
      return;
    }
    let active = true;
    const poll = () => {
      apiClient
        .getDecisions(selectedProjectId)
        .then((data) => {
          if (active) setDecisionCount(decisionTotalCount(data.counts));
        })
        .catch(() => {});
    };
    poll();
    const intervalId = window.setInterval(poll, 5000);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [selectedProjectId, usingMockData]);

  useEffect(() => {
    if (usingMockData || onboardingDismissed || ticketsLoading) return;
    if (needsSetup) setOnboardingOpen(true);
  }, [needsSetup, onboardingDismissed, ticketsLoading, usingMockData]);

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

  const dismissOnboarding = useCallback(() => {
    setOnboardingDismissed(true);
    window.localStorage.setItem(ONBOARDING_DISMISSED_KEY, '1');
    setOnboardingOpen(false);
  }, []);

  const handleSeedDemo = useCallback(async () => {
    if (usingMockData) {
      pushToast('Connect to the API to seed the demo project.', 'error');
      return;
    }
    setSeedingDemo(true);
    try {
      const seeded = await apiClient.seedDemoProject();
      const backendProjects = await apiClient.listProjects();
      const uiProjects = backendProjects.map(toUiProject);
      setProjects(uiProjects);
      const demoProjectId = seeded.project.id;
      setSelectedProjectId(demoProjectId);
      const [backendTickets, backendRequirements] = await Promise.all([
        apiClient.listTickets(demoProjectId),
        apiClient.listRequirements(demoProjectId),
      ]);
      setTickets(
        backendTickets.map((ticket) =>
          toUiTicket(ticket, seeded.project.name),
        ),
      );
      setRequirements(backendRequirements.map(toUiRequirement));
      setViewingRequirementId(seeded.requirement.id);
      dismissOnboarding();
      pushToast('Demo project ready — review R-001 to approve tickets.', 'success');
    } catch (error) {
      pushToast(messageFromError(error, 'Could not seed demo project.'));
    } finally {
      setSeedingDemo(false);
    }
  }, [dismissOnboarding, pushToast, usingMockData]);

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
      .listCloudModels()
      .then((models) => {
        setCloudModels(models);
      })
      .catch(() => {
      });
  }, []);

  useEffect(() => {
    apiClient
      .getCloudExecutionSettings()
      .then((settings) => {
        setAllowCloudExecutionModel(settings.allow_cloud_execution_model);
      })
      .catch(() => {
      });
  }, []);

  useEffect(() => {
    apiClient
      .getChatReasonerConfig()
      .then((config) => setChatReasonerMode(config.mode))
      .catch(() => {});
  }, []);

  const handleToggleChatReasoner = useCallback(() => {
    setChatReasonerMode((current) => {
      const next = current === 'cloud' ? 'local' : 'cloud';
      apiClient.updateChatReasonerConfig(next).catch(() => {
        setChatReasonerMode(current);
      });
      return next;
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
      .listIntegrations()
      .then((items) => setIntegrations(items))
      .catch(() => setIntegrations([]));
  }, [currentPage]);

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
  const handleOpenPr = useCallback(async (ticketId: string) => {
    try {
      const result = await apiClient.openTicketPr(ticketId, selectedProjectId);
      const ticket = await apiClient.getTicket(ticketId, selectedProjectId);
      replaceTicketFromBackend(ticket);
      pushToast(`PR ${result.status}: ${result.pr_url}`, 'success');
    } catch (error) {
      pushToast(messageFromError(error, 'Could not open or update PR.'));
      throw error;
    }
  }, [pushToast, replaceTicketFromBackend, selectedProjectId]);

  const handleInboxOpenTicket = useCallback((ticketId: string, notificationProjectId: string) => {
    if (notificationProjectId !== selectedProjectId) {
      setSelectedProjectId(notificationProjectId);
    }
    setCurrentPage('home');
    setSelectedTicketId(ticketId);
  }, [selectedProjectId]);

  const handleInboxOpenRequirement = useCallback((requirementId: string, notificationProjectId: string) => {
    if (notificationProjectId !== selectedProjectId) {
      setSelectedProjectId(notificationProjectId);
    }
    setViewingRequirementId(requirementId);
  }, [selectedProjectId]);

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

  const handleSplitTicket = useCallback(
    async (ticketId: string, feedback: string) => {
      try {
        const result = await apiClient.splitTicket(ticketId, feedback, selectedProjectId);
        const ticketsToMerge = [result.ticket, ...(result.children ?? [])];
        replaceTicketsFromBackend(ticketsToMerge);
        pushToast(
          `Split into ${result.child_ticket_ids.length} ticket${result.child_ticket_ids.length === 1 ? '' : 's'}: ${result.child_ticket_ids.join(', ')}.`,
          'success',
        );
      } catch (error) {
        pushToast(messageFromError(error, 'Split failed.'));
      }
    },
    [pushToast, replaceTicketsFromBackend, selectedProjectId],
  );

  const handleAbandonTicket = useCallback(
    (ticketId: string, reason: string) => runAndReplace(
      ticketId,
      (id, projectId) => apiClient.abandonTicket(id, reason, projectId),
      'Ticket abandoned.',
    ),
    [runAndReplace],
  );

  const handleAssignModelAndRetry = useCallback(async (ticketId: string, model: string) => {
    try {
      await apiClient.assignModel(ticketId, model, selectedProjectId);
      const updated = await apiClient.retryTicket(ticketId, selectedProjectId);
      replaceTicketFromBackend(updated);
      pushToast('Model changed and ticket retried.', 'success');
    } catch (error) {
      pushToast(messageFromError(error, 'Could not change model and retry.'));
    }
  }, [pushToast, replaceTicketFromBackend, selectedProjectId]);

  const handleUpdateDependsOn = useCallback(async (ticketId: string, dependsOn: string[]) => {
    if (usingMockData) {
      setTickets((prev) => {
        const next = prev.map((ticket) => (ticket.id === ticketId ? { ...ticket, dependsOn } : ticket));
        setTicketGraph(buildMockGraph(next));
        return next;
      });
      pushToast('Dependencies updated.', 'success');
      return;
    }
    try {
      const updated = await apiClient.updateTicket(ticketId, { depends_on: dependsOn }, selectedProjectId);
      const ui = toUiTicket(updated, projectNameById[selectedProjectId]);
      const mergedBase = tickets.map((ticket) => (ticket.id === ticketId ? { ...ui, dependsOn } : ticket));
      setTickets(await loadTicketGraph(mergedBase));
      pushToast('Dependencies updated.', 'success');
    } catch (error) {
      pushToast(messageFromError(error, 'Could not update dependencies.'));
    }
  }, [loadTicketGraph, projectNameById, pushToast, selectedProjectId, tickets, usingMockData]);

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

  const handleAddCloudModel = useCallback(async (payload: {
    label?: string;
    provider: string;
    model_id: string;
    api_key: string;
  }) => {
    const model = await apiClient.addCloudModel(payload);
    const models = await apiClient.listCloudModels();
    setCloudModels(models);
    return model;
  }, []);

  const handleDeleteCloudModel = useCallback(async (modelId: string) => {
    await apiClient.deleteCloudModel(modelId);
    const models = await apiClient.listCloudModels();
    setCloudModels(models);
  }, []);

  const handleUpdateAllowCloudExecutionModel = useCallback(async (enabled: boolean) => {
    const saved = await apiClient.updateCloudExecutionSettings(enabled);
    setAllowCloudExecutionModel(saved.allow_cloud_execution_model);
  }, []);

  const handleToggleAttentionFilter = useCallback(() => {
    if (currentPage !== 'home') {
      setCurrentPage('home');
    }
    setAttentionFilter((value) => !value);
  }, [currentPage]);

  const handleSendChatMessage = useCallback(async (text: string, attachmentIds: string[] = []) => {
    setChatSending(true);
    const now = new Date().toISOString();
    try {
      if (usingMockData) {
        const userMessage: ChatMessage = {
          id: `msg-${Date.now()}`,
          project_id: selectedProjectId,
          role: 'user',
          text,
          segment_id: 'seg-default',
          created_at: now,
          attachment_ids: attachmentIds,
        };
        const agentMessage: ChatMessage = {
          id: `msg-${Date.now() + 1}`,
          project_id: selectedProjectId,
          role: 'agent',
          text: 'Got it — I will file this as a proposal when the backend is connected. For now this is demo mode.',
          segment_id: 'seg-default',
          created_at: now,
        };
        setChatMessages((prev) => [...prev, userMessage, agentMessage]);
        return;
      }
      const result = await apiClient.sendChatMessage(selectedProjectId, text, attachmentIds);
      setChatMessages((prev) => [...prev, ...result.messages]);
      if (result.filed_requirement_ids.length > 0) {
        void loadTickets();
        const reqs = await apiClient.listRequirements(selectedProjectId);
        setRequirements(reqs.map(toUiRequirement));
      }
    } catch (error) {
      pushToast(messageFromError(error, 'Could not send chat message.'));
    } finally {
      setChatSending(false);
    }
  }, [loadTickets, pushToast, selectedProjectId, usingMockData]);

  const handleChatCollapse = useCallback(() => {
    setChatLayoutMode((mode) => (mode === 'rail' ? 'hidden' : 'rail'));
  }, []);

  const handleChatExpand = useCallback(() => {
    setChatLayoutMode((mode) => (mode === 'hidden' ? 'rail' : 'balanced'));
  }, []);

  const handleBoardExpandToggle = useCallback(() => {
    setChatLayoutMode((mode) => {
      if (mode === 'balanced') return 'rail';
      if (mode === 'hidden') return 'rail';
      return 'balanced';
    });
  }, []);

  return (
    <DndProvider backend={HTML5Backend}>
      <div className="h-screen flex flex-col bg-background text-foreground overflow-hidden">
        <TopBar
          darkMode={darkMode}
          onToggleDark={() => setDarkMode((value) => !value)}
          boardLive={boardLive}
          projects={projects}
          selectedProjectId={selectedProjectId}
          onSelectProject={setSelectedProjectId}
          onCreateProject={handleCreateProject}
          onDeleteProject={handleDeleteProject}
          onUpdateProjectSettings={handleUpdateProjectSettings}
          onOpenSetupWizard={() => setOnboardingOpen(true)}
        />
        {usingMockData && (
          <div className="px-4 py-2 text-xs bg-amber-50 text-amber-800 border-b border-amber-200 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-900">
            Cannot reach the API — showing demo ticket data. Start the backend on port 8000 to sync live tickets.
          </div>
        )}

        <div className="flex flex-1 overflow-hidden flex-col min-w-0">
          <div className="flex flex-1 overflow-hidden min-h-0">
            <NavSidebar
              currentPage={currentPage}
              inboxUnreadCount={inboxUnreadCount.by_project[selectedProjectId] ?? 0}
              decisionCount={decisionCount}
              onNavigate={setCurrentPage}
            />
            {currentPage === 'home' ? (
              <div className="flex flex-1 min-w-0 overflow-hidden">
                <ChatPanel
                  messages={chatMessages}
                  requirements={requirements}
                  tickets={tickets}
                  layoutMode={chatLayoutMode}
                  sending={chatSending}
                  chatReasonerMode={chatReasonerMode}
                  projectId={selectedProjectId}
                  usingMockData={usingMockData}
                  onSend={handleSendChatMessage}
                  onToggleReasoner={handleToggleChatReasoner}
                  onCollapse={handleChatCollapse}
                  onExpand={handleChatExpand}
                  onSelectTicket={setSelectedTicketId}
                  onOpenRequirement={setViewingRequirementId}
                  onUploadError={(message) => pushToast(message, 'error')}
                />
                <div className="flex flex-1 flex-col min-w-0 overflow-hidden relative">
                  <div className="absolute top-2 right-2 z-10">
                    <button
                      type="button"
                      onClick={handleBoardExpandToggle}
                      title={chatLayoutMode === 'balanced' ? 'Expand board' : 'Restore split view'}
                      aria-label={chatLayoutMode === 'balanced' ? 'Expand board to full width' : 'Restore split view'}
                      className="h-7 w-7 flex items-center justify-center rounded-md border border-border bg-card/90 text-muted-foreground shadow-sm hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {chatLayoutMode === 'balanced' ? <Maximize2 size={14} /> : <Minimize2 size={14} />}
                    </button>
                  </div>
                  <BoardToolbar
                    ref={boardSearchRef}
                    boardLive={boardLive}
                    attentionCount={attentionCount}
                    attentionFilter={attentionFilter}
                    onToggleAttentionFilter={handleToggleAttentionFilter}
                    onNewRequirement={() => setShowNewReq(true)}
                    onNewTicket={() => setShowNewTicket(true)}
                    workerStatus={workerStatus}
                    workerPending={workerPending}
                    autoRunNotice={workerStatus?.last_skipped_reason ?? ''}
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
                    tickets={tickets}
                    showDependencies={showDependencies}
                    onToggleDependencies={() => setShowDependencies((value) => !value)}
                  />
                  {showDependencies && (
                    <DependencyGraphPanel
                      graph={ticketGraph}
                      tickets={tickets}
                      loading={graphLoading}
                      onSelectTicket={setSelectedTicketId}
                    />
                  )}
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
              </div>
            ) : currentPage === 'activity' ? (
              <ActivityPage
                projectId={selectedProjectId}
                tickets={tickets}
                cloudModels={cloudModels}
                usingMockData={usingMockData}
                onSelectTicket={(ticketId) => {
                  setCurrentPage('home');
                  setSelectedTicketId(ticketId);
                }}
              />
            ) : currentPage === 'insights' ? (
              <InsightsPage
                projectId={selectedProjectId}
                cloudModels={cloudModels}
                usingMockData={usingMockData}
              />
            ) : currentPage === 'inbox' ? (
              <InboxPage
                projectId={selectedProjectId}
                projectNameById={projectNameById}
                usingMockData={usingMockData}
                onUnreadCountChange={setInboxUnreadCount}
                onOpenTicket={handleInboxOpenTicket}
                onOpenRequirement={handleInboxOpenRequirement}
              />
            ) : currentPage === 'decisions' ? (
              <DecisionCenterPage
                projectId={selectedProjectId}
                usingMockData={usingMockData}
                onOpenTicket={(ticketId) => {
                  setCurrentPage('home');
                  setSelectedTicketId(ticketId);
                }}
                onOpenRequirement={(requirementId) => setViewingRequirementId(requirementId)}
                onApproveTicket={(ticketId) => {
                  setCurrentPage('home');
                  setSelectedTicketId(ticketId);
                  void handleApprove(ticketId);
                }}
                onAcceptTicket={(ticketId) => {
                  setCurrentPage('home');
                  setSelectedTicketId(ticketId);
                  void handleAccept(ticketId);
                }}
              />
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
              cloudModels={cloudModels}
              requirements={requirements}
              devFallbackChain={devFallbackChain}
              notificationWebhook={notificationWebhook}
              onUpdateModel={handleUpdateModel}
              onUpdateRoute={handleUpdateRoute}
              onUpdateDevFallbackChain={handleUpdateDevFallbackChain}
              onSaveLocalModelEndpoints={handleSaveLocalModelEndpoints}
              onRefreshLocalModels={handleRefreshLocalModels}
              onSaveCloudReasoner={handleSaveCloudReasoner}
              onAddCloudModel={handleAddCloudModel}
              onDeleteCloudModel={handleDeleteCloudModel}
              onSaveNotificationWebhook={handleSaveNotificationWebhook}
              onLoadModelAdditionalInstructions={handleLoadModelAdditionalInstructions}
              onSaveModelAdditionalInstructions={handleSaveModelAdditionalInstructions}
              allowCloudExecutionModel={allowCloudExecutionModel}
              onUpdateAllowCloudExecutionModel={handleUpdateAllowCloudExecutionModel}
              usingMockData={usingMockData}
              identityContext={identityContext}
              onForbidden={(message) => pushToast(message)}
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
            onOpenPr={() => handleOpenPr(selectedTicket.id)}
            onSplit={(feedback) => handleSplitTicket(selectedTicket.id, feedback)}
            onAbandon={(reason) => handleAbandonTicket(selectedTicket.id, reason)}
            onAssignModelAndRetry={(model) => void handleAssignModelAndRetry(selectedTicket.id, model)}
            onOpenTicket={(id) => setSelectedTicketId(id)}
            onUpdateDependsOn={(dependsOn) => handleUpdateDependsOn(selectedTicket.id, dependsOn)}
            allTickets={tickets}
            usingMockData={usingMockData}
            prIntegrationConfigured={prIntegrationConfigured}
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
            usingMockData={usingMockData}
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
          return req ? (
            <RequirementSummaryModal
              requirement={req}
              usingMockData={usingMockData}
              onClose={() => setViewingRequirementId(null)}
            />
          ) : null;
        })()}

        <ToastStack toasts={toasts} onDismiss={dismissToast} />
        <ApiTokenPrompt />
        <OnboardingWizard
          open={onboardingOpen}
          projectPathReady={projectPathReady}
          modelsConfigured={modelsConfigured}
          seedingDemo={seedingDemo}
          onClose={() => setOnboardingOpen(false)}
          onDismiss={dismissOnboarding}
          onOpenModels={() => {
            setOnboardingOpen(false);
            setCurrentPage('models');
          }}
          onNewRequirement={() => {
            setOnboardingOpen(false);
            setShowNewReq(true);
          }}
          onSeedDemo={() => void handleSeedDemo()}
        />
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
