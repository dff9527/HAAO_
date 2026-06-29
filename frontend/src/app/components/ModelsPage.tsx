import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Loader2, CheckCircle2, XCircle, Info, Plus, RefreshCw, Trash2, Cloud, Link2 } from 'lucide-react';
import { ModelCard } from './ModelCard';
import { Switch } from './ui/switch';
import { DevFallbackChainEditor } from './DevFallbackChainEditor';
import { DEFAULT_LOCAL_MODELS, formatCloudCost, getModelMeta, isCloudModel } from '../constants';
import { cloudModelDisplayLabel, modelDisplayLabel } from '../modelDisplay';
import type { ModelConfig, RoleRoute, AssignedModel, CloudModel, RequirementSource } from '../types';
import type { BackendLocalModelEndpoint } from '../api/types';
import type { IdentityContext } from '../api/types';
import type { CloudReasonerConfig, IntegrationCredential, IntegrationProvider } from '../api/client';
import { apiClient } from '../api/client';
import { ModelStatusLegend } from './ModelStatusLegend';
import { EvalRunPanel } from './EvalRunPanel';
import { HelpTip } from './HelpTip';
import { HELP_TOOLTIPS } from '../dxUtils';
import { ACTION_DISCLOSURES } from '../trustUtils';
import { ActionDisclosure } from './ActionDisclosure';
import { MembersPanel } from './MembersPanel';
import { AuditLogPanel } from './AuditLogPanel';
import { RunnersPanel } from './RunnersPanel';
import { GitCredentialConnect } from './GitCredentialConnect';
import {
  hasTeamPermission,
  isReadOnlyTeamRole,
  mockIntegrationsWithApp,
  mockTeamPlaneEnabled,
  roleLabel,
  isForbiddenError,
  forbiddenMessage,
} from '../teamPlaneUtils';

type TestStatus = 'idle' | 'running' | 'ok' | 'fail';

interface Props {
  modelConfigs: ModelConfig[];
  roleRoutes: RoleRoute[];
  localModelIds: string[];
  localModelEndpoints: BackendLocalModelEndpoint[];
  cloudReasoner: CloudReasonerConfig | null;
  cloudModels: CloudModel[];
  requirements: RequirementSource[];
  devFallbackChain: string[];
  notificationWebhook: string;
  onUpdateModel: (id: AssignedModel, updates: Partial<ModelConfig>) => void;
  onUpdateRoute: (id: string, model: AssignedModel) => void;
  onUpdateDevFallbackChain: (chain: string[]) => void;
  onSaveLocalModelEndpoints: (endpoints: BackendLocalModelEndpoint[]) => Promise<void>;
  onRefreshLocalModels: () => Promise<{ models: string[]; endpoints: BackendLocalModelEndpoint[] }>;
  onSaveCloudReasoner: (modelId: string) => Promise<CloudReasonerConfig>;
  onAddCloudModel: (payload: {
    label?: string;
    provider: string;
    model_id: string;
    api_key: string;
  }) => Promise<CloudModel>;
  onDeleteCloudModel: (modelId: string) => Promise<void>;
  onSaveNotificationWebhook: (webhookUrl: string) => Promise<string>;
  onLoadModelAdditionalInstructions: (modelId: string) => Promise<string>;
  onSaveModelAdditionalInstructions: (modelId: string, text: string) => Promise<string>;
  allowCloudExecutionModel: boolean;
  onUpdateAllowCloudExecutionModel: (enabled: boolean) => Promise<void>;
  usingMockData?: boolean;
  identityContext?: IdentityContext | null;
  onForbidden?: (message: string) => void;
}

function SectionHeader({ num, title, subtitle }: { num: string; title: string; subtitle?: string }) {
  return (
    <div className="flex items-start gap-3 mb-3">
      <span className="w-6 h-6 rounded flex items-center justify-center bg-muted text-muted-foreground text-xs font-mono shrink-0 mt-0.5">
        {num}
      </span>
      <div>
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {subtitle && <p className="text-[11px] text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
    </div>
  );
}

function ConnectionBlock({
  title,
  hint,
  children,
  className = '',
}: {
  title: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-xl border border-border bg-card px-4 py-4 ${className}`}>
      <div className="mb-3">
        <h3 className="text-xs font-semibold text-foreground">{title}</h3>
        {hint && <p className="text-[11px] text-muted-foreground mt-0.5">{hint}</p>}
      </div>
      {children}
    </div>
  );
}

function CollapsibleConnectionBlock({
  title,
  hint,
  children,
  defaultOpen = false,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="rounded-xl border border-border bg-card group" open={defaultOpen}>
      <summary className="px-4 py-3 cursor-pointer list-none flex items-center gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-xl">
        <span className="text-xs font-semibold text-foreground">{title}</span>
        {hint && <span className="text-[11px] text-muted-foreground truncate">{hint}</span>}
      </summary>
      <div className="px-4 pb-4 border-t border-border pt-3">{children}</div>
    </details>
  );
}

const FORM_INPUT_CLASS =
  'text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground';
const FORM_INPUT_MONO_CLASS = `font-mono ${FORM_INPUT_CLASS}`;

function routeModelOptions(
  routeId: string,
  localModelOptions: string[],
  cloudModels: CloudModel[],
  currentModel: AssignedModel,
): AssignedModel[] {
  const cloudIds = cloudModels.map((model) => model.id);
  if (routeId === 'dev') {
    return [...new Set([...localModelOptions, ...cloudIds, ...(currentModel ? [currentModel] : [])])];
  }
  if (routeId === 'tech_lead') {
    return cloudIds.length ? cloudIds : (currentModel ? [currentModel] : []);
  }
  const options = [...new Set([...localModelOptions, ...cloudIds, currentModel])];
  return options.length ? options : [...localModelOptions, ...cloudIds];
}

function cloudModelOptionLabel(model: CloudModel): string {
  return cloudModelDisplayLabel(model);
}

function modelOptionLabel(model: AssignedModel, cloudModels: CloudModel[]): string {
  return modelDisplayLabel(model, cloudModels);
}

function cloudModelIsDeletable(model: CloudModel): boolean {
  return model.deletable !== false;
}

const INTEGRATION_PROVIDER_OPTIONS: Array<{ id: IntegrationProvider; label: string; tokenPlaceholder: string; scopePlaceholder: string }> = [
  {
    id: 'github',
    label: 'GitHub',
    tokenPlaceholder: 'Personal access token',
    scopePlaceholder: 'repo, contents:write, pull_requests:write',
  },
  {
    id: 'gitlab',
    label: 'GitLab',
    tokenPlaceholder: 'Personal access token',
    scopePlaceholder: 'api, write_repository',
  },
  {
    id: 'slack',
    label: 'Slack',
    tokenPlaceholder: 'Incoming webhook URL',
    scopePlaceholder: 'optional — leave empty for webhooks',
  },
];

function integrationProviderLabel(provider: IntegrationProvider): string {
  return INTEGRATION_PROVIDER_OPTIONS.find((item) => item.id === provider)?.label ?? provider;
}

function cloudModelToConfig(model: CloudModel): ModelConfig {
  return {
    id: model.id,
    name: model.label,
    backend: 'Cloud API',
    contextWindow: 200000,
    status: model.key_configured ? 'Connected' : 'Available',
    params: {
      temperature: 0.7,
      topP: 0.95,
      maxOutputTokens: 8192,
      contextWindowCap: 200000,
      defaultRetryBudget: 2,
      systemPrompt: '',
      additionalInstructions: '',
      fullPromptOverride: '',
      useFullPromptOverride: false,
    },
  };
}

export function ModelsPage({
  modelConfigs,
  roleRoutes,
  localModelIds,
  localModelEndpoints,
  cloudReasoner,
  cloudModels,
  requirements,
  devFallbackChain,
  notificationWebhook,
  onUpdateModel,
  onUpdateRoute,
  onUpdateDevFallbackChain,
  onSaveLocalModelEndpoints,
  onRefreshLocalModels,
  onSaveCloudReasoner,
  onAddCloudModel,
  onDeleteCloudModel,
  onSaveNotificationWebhook,
  onLoadModelAdditionalInstructions,
  onSaveModelAdditionalInstructions,
  allowCloudExecutionModel,
  onUpdateAllowCloudExecutionModel,
  usingMockData = false,
  identityContext = null,
  onForbidden,
}: Props) {
  const [expandedModels, setExpandedModels] = useState<Set<AssignedModel>>(new Set());
  const [cloudExecutionPending, setCloudExecutionPending] = useState(false);
  const [techLeadSaveState, setTechLeadSaveState] = useState<TestStatus>('idle');
  const [techLeadSaveMessage, setTechLeadSaveMessage] = useState('');
  const [endpointRows, setEndpointRows] = useState<BackendLocalModelEndpoint[]>(localModelEndpoints);
  const [registryProvider, setRegistryProvider] = useState('openai');
  const [registryModelId, setRegistryModelId] = useState('');
  const [registryLabel, setRegistryLabel] = useState('');
  const [registryApiKey, setRegistryApiKey] = useState('');
  const [registryState, setRegistryState] = useState<TestStatus>('idle');
  const [registryMessage, setRegistryMessage] = useState('');
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [discoverState, setDiscoverState] = useState<TestStatus>('idle');
  const [discoverMessage, setDiscoverMessage] = useState('');
  const [cloudTestState, setCloudTestState] = useState<Record<string, TestStatus>>({});
  const [cloudTestMessage, setCloudTestMessage] = useState<Record<string, string>>({});
  const [deletingCloudId, setDeletingCloudId] = useState<string | null>(null);
  const [webhookInput, setWebhookInput] = useState(notificationWebhook);
  const [webhookState, setWebhookState] = useState<TestStatus>('idle');
  const [integrations, setIntegrations] = useState<IntegrationCredential[]>([]);
  const [integrationsLoading, setIntegrationsLoading] = useState(true);
  const [integrationProvider, setIntegrationProvider] = useState<IntegrationProvider>('github');
  const [integrationLabel, setIntegrationLabel] = useState('');
  const [integrationToken, setIntegrationToken] = useState('');
  const [integrationScopes, setIntegrationScopes] = useState('');
  const [integrationState, setIntegrationState] = useState<TestStatus>('idle');
  const [integrationMessage, setIntegrationMessage] = useState('');
  const [deletingIntegrationKey, setDeletingIntegrationKey] = useState<string | null>(null);
  const [discoveryState, setDiscoveryState] = useState<TestStatus>('idle');
  const [isSavingEndpoints, setIsSavingEndpoints] = useState(false);
  const endpointDirty = JSON.stringify(endpointRows) !== JSON.stringify(localModelEndpoints);
  const webhookDirty = webhookInput.trim() !== notificationWebhook.trim();
  const localModelOptions = useMemo(
    () => (localModelIds.length ? localModelIds : DEFAULT_LOCAL_MODELS),
    [localModelIds],
  );
  const totalCloudSpend = useMemo(
    () => requirements.reduce((sum, req) => sum + (req.cloudCostUsd ?? 0), 0),
    [requirements],
  );
  const cloudExecutionAssigned = useMemo(
    () =>
      devFallbackChain.some((model) => isCloudModel(model))
      || roleRoutes.some((route) => route.id === 'gatekeeper' && isCloudModel(route.model)),
    [devFallbackChain, roleRoutes],
  );
  const selectedTechLeadId = cloudReasoner?.model_id ?? '';
  const selectedTechLeadModel = cloudModels.find((model) => model.id === selectedTechLeadId);
  const selectedIntegrationProvider = INTEGRATION_PROVIDER_OPTIONS.find((item) => item.id === integrationProvider)
    ?? INTEGRATION_PROVIDER_OPTIONS[0];
  const evalModelOptions = useMemo(
    () => Array.from(new Set([...devFallbackChain, ...localModelOptions, ...cloudModels.map((model) => model.id)])),
    [cloudModels, devFallbackChain, localModelOptions],
  );
  const teamActive = identityContext?.identity_configured === true;
  const canAdminSettings = hasTeamPermission(identityContext, 'admin');
  const viewerLocked = teamActive && isReadOnlyTeamRole(identityContext);
  const adminLocked = teamActive && !canAdminSettings;

  function settingsDisabled(adminOnly = false): boolean {
    if (!teamActive) return false;
    if (viewerLocked) return true;
    if (adminOnly) return adminLocked;
    return false;
  }

  useEffect(() => {
    let active = true;
    setIntegrationsLoading(true);
    const load = mockTeamPlaneEnabled()
      ? Promise.resolve(mockIntegrationsWithApp())
      : apiClient.listIntegrations();
    load
      .then((items) => {
        if (active) setIntegrations(items);
      })
      .catch(() => {
        if (active) setIntegrations([]);
      })
      .finally(() => {
        if (active) setIntegrationsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    setEndpointRows(localModelEndpoints);
  }, [localModelEndpoints]);

  const cloudProviderOptions = cloudReasoner?.providers ?? [];

  async function saveTechLeadModel(modelId: string) {
    setTechLeadSaveState('running');
    setTechLeadSaveMessage('');
    try {
      await onSaveCloudReasoner(modelId);
      setTechLeadSaveState('ok');
    } catch (error) {
      setTechLeadSaveState('fail');
      setTechLeadSaveMessage(error instanceof Error ? error.message : 'Could not save Tech Lead model.');
    }
  }

  async function discoverModels() {
    setDiscoverState('running');
    setDiscoverMessage('');
    try {
      const result = await apiClient.listProviderModels({
        provider: registryProvider,
        api_key: registryApiKey.trim() || undefined,
      });
      if (!result.ok) {
        setDiscoverState('fail');
        setDiscoverMessage(result.message || 'Could not list models.');
        setDiscoveredModels([]);
        return;
      }
      setDiscoveredModels(result.models);
      setDiscoverState('ok');
      if (result.models.length && !result.models.includes(registryModelId)) {
        setRegistryModelId(result.models[0]);
      }
    } catch (error) {
      setDiscoverState('fail');
      setDiscoverMessage(error instanceof Error ? error.message : 'Could not list models.');
      setDiscoveredModels([]);
    }
  }

  async function addRegistryModel() {
    setRegistryState('running');
    setRegistryMessage('');
    try {
      await onAddCloudModel({
        label: registryLabel.trim(),
        provider: registryProvider,
        model_id: registryModelId.trim(),
        api_key: registryApiKey.trim(),
      });
      setRegistryModelId('');
      setRegistryLabel('');
      setRegistryApiKey('');
      setRegistryState('ok');
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setRegistryState('fail');
        setRegistryMessage(error instanceof Error ? error.message : 'Could not add cloud model.');
      }
    }
  }

  async function removeRegistryModel(modelId: string) {
    setDeletingCloudId(modelId);
    try {
      await onDeleteCloudModel(modelId);
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setRegistryMessage(error instanceof Error ? error.message : 'Could not remove cloud model.');
        setRegistryState('fail');
      }
    } finally {
      setDeletingCloudId(null);
    }
  }

  async function testCloudModelEntry(
    key: string,
    payload: { provider: string; model_id: string; api_key?: string },
  ) {
    setCloudTestState((prev) => ({ ...prev, [key]: 'running' }));
    setCloudTestMessage((prev) => ({ ...prev, [key]: '' }));
    try {
      const result = await apiClient.testCloudModel(payload);
      setCloudTestState((prev) => ({ ...prev, [key]: result.ok ? 'ok' : 'fail' }));
      setCloudTestMessage((prev) => ({ ...prev, [key]: result.message }));
    } catch (error) {
      setCloudTestState((prev) => ({ ...prev, [key]: 'fail' }));
      setCloudTestMessage((prev) => ({
        ...prev,
        [key]: error instanceof Error ? error.message : 'Connection test failed.',
      }));
    }
  }

  async function testExistingCloudModel(model: CloudModel) {
    await testCloudModelEntry(model.id, {
      provider: model.provider,
      model_id: model.model_id,
    });
  }

  async function testAddFormCloudModel() {
    if (!registryModelId.trim()) return;
    const payload: { provider: string; model_id: string; api_key?: string } = {
      provider: registryProvider,
      model_id: registryModelId.trim(),
    };
    if (registryApiKey.trim()) {
      payload.api_key = registryApiKey.trim();
    }
    await testCloudModelEntry('add-form', payload);
  }

  useEffect(() => {
    setWebhookInput(notificationWebhook);
  }, [notificationWebhook]);

  function toggleExpanded(id: AssignedModel) {
    setExpandedModels((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function saveEndpoints() {
    setIsSavingEndpoints(true);
    try {
      await onSaveLocalModelEndpoints(normalizeEndpointRows(endpointRows));
    } finally {
      setIsSavingEndpoints(false);
    }
  }

  async function refreshLocalModels() {
    setDiscoveryState('running');
    try {
      await onRefreshLocalModels();
      setDiscoveryState('ok');
    } catch {
      setDiscoveryState('fail');
    }
  }

  async function saveNotificationWebhook() {
    setWebhookState('running');
    try {
      const saved = await onSaveNotificationWebhook(webhookInput.trim());
      setWebhookInput(saved);
      setWebhookState('ok');
    } catch {
      setWebhookState('fail');
    }
  }

  async function addIntegrationCredential() {
    setIntegrationState('running');
    setIntegrationMessage('');
    try {
      const scopes = integrationScopes
        .split(',')
        .map((scope) => scope.trim())
        .filter(Boolean);
      const saved = await apiClient.upsertIntegration({
        provider: integrationProvider,
        token: integrationToken.trim(),
        scopes,
        label: integrationLabel.trim() || undefined,
      });
      const listed = await apiClient.listIntegrations();
      setIntegrations(listed);
      setIntegrationToken('');
      setIntegrationLabel('');
      setIntegrationScopes('');
      setIntegrationState('ok');
      if (!saved.configured) {
        setIntegrationMessage('Credential saved but not marked configured.');
      }
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setIntegrationState('fail');
        setIntegrationMessage(error instanceof Error ? error.message : 'Could not save integration.');
      }
    }
  }

  async function removeIntegrationCredential(provider: IntegrationProvider, credentialId: string) {
    const key = `${provider}:${credentialId}`;
    setDeletingIntegrationKey(key);
    try {
      await apiClient.deleteIntegration(provider, credentialId);
      setIntegrations((items) => items.filter((item) => !(item.provider === provider && item.id === credentialId)));
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setIntegrationMessage(error instanceof Error ? error.message : 'Could not remove integration.');
        setIntegrationState('fail');
      }
    } finally {
      setDeletingIntegrationKey(null);
    }
  }

  function updateEndpoint(index: number, updates: Partial<BackendLocalModelEndpoint>) {
    setEndpointRows((rows) => rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...updates } : row)));
  }

  function addEndpoint() {
    setEndpointRows((rows) => [
      ...rows,
      {
        id: `local-${rows.length + 1}`,
        label: `Local ${rows.length + 1}`,
        base_url: 'http://localhost:1234/v1',
        api_key: '',
      },
    ]);
  }

  function removeEndpoint(index: number) {
    setEndpointRows((rows) => rows.filter((_, rowIndex) => rowIndex !== index));
  }

  const localRoster = modelConfigs.filter((cfg) => cfg.backend === 'LM Studio (local)');

  function renderModelCard(cfg: ModelConfig) {
    return (
      <ModelCard
        key={cfg.id}
        config={cfg}
        isExpanded={expandedModels.has(cfg.id)}
        onToggleExpand={() => toggleExpanded(cfg.id)}
        onUpdate={(updates) => onUpdateModel(cfg.id, updates)}
        onLoadAdditionalInstructions={onLoadModelAdditionalInstructions}
        onSaveAdditionalInstructions={onSaveModelAdditionalInstructions}
      />
    );
  }

  return (
    <div className="flex-1 overflow-y-auto bg-background">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8 space-y-8">

        {/* ── Section 1: Connections ── */}
        <section className="space-y-3">
          <SectionHeader num="1" title="Connections" subtitle="Each block saves independently." />
          {teamActive && identityContext && (
            <div className="rounded-xl border border-border bg-muted/30 px-4 py-2.5 text-xs text-muted-foreground flex flex-wrap items-center gap-2">
              <span className="font-medium text-foreground">Workspace</span>
              <span className="font-mono">{identityContext.workspace_id}</span>
              <span>·</span>
              <span>Signed in as {identityContext.actor_id}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-foreground">
                {roleLabel(identityContext.role)}
              </span>
              {viewerLocked && (
                <span className="text-amber-700 dark:text-amber-300">Read-only settings</span>
              )}
            </div>
          )}

          <CollapsibleConnectionBlock title="Local model servers" hint="LM Studio / OpenAI-compatible URLs.">
            <div className="space-y-2">
              {endpointRows.map((endpoint, index) => (
                <div
                  key={`${endpoint.id}-${index}`}
                  className="grid grid-cols-1 sm:grid-cols-[minmax(0,110px)_1fr_minmax(0,130px)_28px] gap-2"
                >
                  <input
                    type="text"
                    value={endpoint.label}
                    onChange={(e) => updateEndpoint(index, { label: e.target.value })}
                    className={FORM_INPUT_MONO_CLASS}
                    placeholder="Label"
                  />
                  <input
                    type="text"
                    value={endpoint.base_url}
                    onChange={(e) => updateEndpoint(index, { base_url: e.target.value })}
                    className={FORM_INPUT_MONO_CLASS}
                    placeholder="http://localhost:1234/v1"
                  />
                  <input
                    type="password"
                    value={endpoint.api_key ?? ''}
                    onChange={(e) => updateEndpoint(index, { api_key: e.target.value })}
                    className={FORM_INPUT_MONO_CLASS}
                    placeholder="API key (optional)"
                  />
                  <button
                    type="button"
                    onClick={() => removeEndpoint(index)}
                    className="h-7 w-7 flex items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label="Remove endpoint"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2 mt-3">
              <button
                type="button"
                onClick={addEndpoint}
                className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Plus size={11} /> Add
              </button>
              <button
                type="button"
                onClick={refreshLocalModels}
                disabled={discoveryState === 'running'}
                className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {discoveryState === 'running' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
                Refresh discovery
              </button>
              {discoveryState === 'ok' && (
                <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400 shrink-0">
                  <CheckCircle2 size={12} /> {localModelIds.length} models
                </span>
              )}
              {discoveryState === 'fail' && (
                <span className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400 shrink-0">
                  <XCircle size={12} /> Failed
                </span>
              )}
            </div>
            <details className="mt-2 text-[11px] text-muted-foreground">
              <summary className="cursor-pointer select-none">Discovered model IDs</summary>
              <p className="mt-1 break-all font-mono">
                {(localModelIds.length ? localModelIds : DEFAULT_LOCAL_MODELS).join(', ')}
              </p>
            </details>
            {endpointDirty && (
              <div className="mt-3 pt-3 border-t border-border flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs text-amber-600 dark:text-amber-400">Unsaved changes</span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setEndpointRows(localModelEndpoints)}
                    className="text-xs px-2.5 py-1 rounded border border-border hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    Reset
                  </button>
                  <button
                    type="button"
                    onClick={saveEndpoints}
                    disabled={isSavingEndpoints}
                    className="text-xs px-2.5 py-1 rounded bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {isSavingEndpoints ? 'Saving…' : 'Save'}
                  </button>
                </div>
              </div>
            )}
          </CollapsibleConnectionBlock>

          <ConnectionBlock title="Cloud models & API keys" hint="Encrypted on server; Claude default uses env key.">
                {cloudModels.length > 0 && (
                  <div className="space-y-2 mb-3">
                    {cloudModels.map((model) => (
                      <div
                        key={model.id}
                        className="flex flex-wrap items-center gap-2 rounded border border-border bg-background px-2.5 py-2"
                      >
                        <Cloud size={12} className="shrink-0 text-sky-600 dark:text-sky-400" />
                        <span className="text-xs font-medium text-foreground">{cloudModelOptionLabel(model)}</span>
                        {!cloudModelIsDeletable(model) && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
                            default
                          </span>
                        )}
                        <span className="font-mono text-[11px] text-muted-foreground">{model.id}</span>
                        <span
                          className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                            model.key_configured
                              ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
                              : 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
                          }`}
                        >
                          {model.key_configured ? 'key configured' : 'no key'}
                        </span>
                        <button
                          type="button"
                          onClick={() => void testExistingCloudModel(model)}
                          disabled={cloudTestState[model.id] === 'running'}
                          className="text-[11px] px-2 py-1 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {cloudTestState[model.id] === 'running' ? (
                            <Loader2 size={11} className="animate-spin" />
                          ) : (
                            'Test'
                          )}
                        </button>
                        {cloudTestState[model.id] === 'ok' && (
                          <span className="text-[11px] text-emerald-600 dark:text-emerald-400">Connection OK</span>
                        )}
                        {cloudTestState[model.id] === 'fail' && cloudTestMessage[model.id] && (
                          <span className="text-[11px] text-red-600 dark:text-red-400 max-w-[200px] truncate" title={cloudTestMessage[model.id]}>
                            {cloudTestMessage[model.id]}
                          </span>
                        )}
                        {cloudModelIsDeletable(model) ? (
                          <button
                            type="button"
                            onClick={() => void removeRegistryModel(model.id)}
                            disabled={settingsDisabled(true) || deletingCloudId === model.id}
                            className="ml-auto h-7 w-7 flex items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            aria-label={`Remove ${model.label}`}
                          >
                            {deletingCloudId === model.id ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <select
                    value={registryProvider}
                    disabled={settingsDisabled(true)}
                    onChange={(e) => { setRegistryProvider(e.target.value); setRegistryState('idle'); setRegistryMessage(''); setDiscoveredModels([]); setDiscoverState('idle'); setDiscoverMessage(''); }}
                    className={FORM_INPUT_CLASS}
                  >
                    {(cloudProviderOptions.length ? cloudProviderOptions : [{ id: 'openai', label: 'OpenAI', key_configured: true }]).map((provider) => (
                      <option key={provider.id} value={provider.id}>{provider.label}</option>
                    ))}
                  </select>
                  <input
                    value={registryModelId}
                    disabled={settingsDisabled(true)}
                    onChange={(e) => { setRegistryModelId(e.target.value); setRegistryState('idle'); setRegistryMessage(''); }}
                    placeholder="Model id — or Discover to pick"
                    list="discovered-cloud-models"
                    className={FORM_INPUT_MONO_CLASS}
                  />
                  <datalist id="discovered-cloud-models">
                    {discoveredModels.map((model) => (
                      <option key={model} value={model} />
                    ))}
                  </datalist>
                  <input
                    value={registryLabel}
                    onChange={(e) => setRegistryLabel(e.target.value)}
                    placeholder="Label (optional)"
                    className={FORM_INPUT_CLASS}
                  />
                  <input
                    type="password"
                    value={registryApiKey}
                    disabled={settingsDisabled(true)}
                    onChange={(e) => { setRegistryApiKey(e.target.value); setRegistryState('idle'); setRegistryMessage(''); }}
                    placeholder="API key"
                    className={FORM_INPUT_MONO_CLASS}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2 mt-3">
                  <button
                    type="button"
                    onClick={() => void discoverModels()}
                    disabled={settingsDisabled(true) || discoverState === 'running'}
                    title="List the models this provider exposes for your key"
                    className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {discoverState === 'running' ? <Loader2 size={11} className="animate-spin" /> : 'Discover models'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void testAddFormCloudModel()}
                    disabled={cloudTestState['add-form'] === 'running' || !registryModelId.trim()}
                    className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {cloudTestState['add-form'] === 'running' ? <Loader2 size={11} className="animate-spin" /> : 'Test'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void addRegistryModel()}
                    disabled={settingsDisabled(true) || registryState === 'running' || !registryModelId.trim() || !registryApiKey.trim()}
                    className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {registryState === 'running' ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
                    Add cloud model
                  </button>
                  {cloudTestState['add-form'] === 'ok' && (
                    <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400 shrink-0">
                      <CheckCircle2 size={12} /> Connection OK
                    </span>
                  )}
                  {cloudTestState['add-form'] === 'fail' && cloudTestMessage['add-form'] && (
                    <span className="text-xs text-red-600 dark:text-red-400 shrink-0">{cloudTestMessage['add-form']}</span>
                  )}
                  {registryState === 'ok' && (
                    <CheckCircle2 size={12} className="text-emerald-600 dark:text-emerald-400 shrink-0" />
                  )}
                  {registryState === 'fail' && (
                    <XCircle size={12} className="text-red-600 dark:text-red-400 shrink-0" />
                  )}
                </div>
                {registryMessage && (
                  <p className="text-[11px] mt-1.5 text-red-600 dark:text-red-400">{registryMessage}</p>
                )}
                {discoverState === 'ok' && discoveredModels.length > 0 && (
                  <p className="text-[11px] mt-2 text-muted-foreground">
                    {discoveredModels.length} model{discoveredModels.length === 1 ? '' : 's'} found — pick from Model id.
                  </p>
                )}
                {discoverState === 'fail' && discoverMessage && (
                  <p className="text-[11px] mt-2 text-red-600 dark:text-red-400">{discoverMessage}</p>
                )}
          </ConnectionBlock>

          <ConnectionBlock title="Tech Lead" hint="Cloud reasoner for decompose and audit.">
            <div className="flex items-center gap-1.5 mb-2">
              <span className="text-xs font-medium text-foreground">Tech Lead</span>
              <HelpTip text={HELP_TOOLTIPS.tech_lead} label="Tech Lead help" />
            </div>
            {cloudModels.length === 0 ? (
              <p className="text-[11px] text-muted-foreground" title="Add a registry model under Cloud models first.">
                Add a cloud model above first.
              </p>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Cloud size={12} className="shrink-0 text-sky-600 dark:text-sky-400" />
                  <select
                    value={selectedTechLeadId || cloudModels[0]?.id || ''}
                    onChange={(e) => void saveTechLeadModel(e.target.value)}
                    disabled={techLeadSaveState === 'running'}
                    className={`${FORM_INPUT_CLASS} w-full disabled:opacity-50`}
                  >
                    {cloudModels.map((model) => (
                      <option key={model.id} value={model.id}>
                        {cloudModelOptionLabel(model)}
                        {!model.key_configured ? ' (no key)' : ''}
                      </option>
                    ))}
                  </select>
                  {techLeadSaveState === 'running' && (
                    <Loader2 size={12} className="animate-spin text-muted-foreground shrink-0" />
                  )}
                  {techLeadSaveState === 'ok' && (
                    <CheckCircle2 size={12} className="text-emerald-600 dark:text-emerald-400 shrink-0" />
                  )}
                  {techLeadSaveState === 'fail' && (
                    <XCircle size={12} className="text-red-600 dark:text-red-400 shrink-0" />
                  )}
                </div>
                {selectedTechLeadModel && !selectedTechLeadModel.key_configured && (
                  <p
                    className="text-[10px] text-amber-700 dark:text-amber-300"
                    title={
                      cloudModelIsDeletable(selectedTechLeadModel)
                        ? 'Add or update the API key under Cloud models.'
                        : 'Set CLAUDE_API_KEY on the server, or pick a registry model with a stored key.'
                    }
                  >
                    {cloudModelIsDeletable(selectedTechLeadModel)
                      ? 'No API key — update under Cloud models.'
                      : 'No Claude env key — set CLAUDE_API_KEY or pick another model.'}
                  </p>
                )}
                {techLeadSaveMessage && (
                  <p className="text-[10px] text-red-600 dark:text-red-400">{techLeadSaveMessage}</p>
                )}
              </div>
            )}
            <details className="mt-3 text-[11px] text-muted-foreground">
              <summary className="cursor-pointer select-none">Auto-escalation</summary>
              <p className="mt-1">Retries exhausted → reassigns to Tech Lead (always on).</p>
            </details>
          </ConnectionBlock>

          <CollapsibleConnectionBlock title="Integrations" hint="GitHub, GitLab, Slack — encrypted tokens.">
                {integrationsLoading ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground py-1">
                    <Loader2 size={12} className="animate-spin" />
                    Loading integrations…
                  </div>
                ) : integrations.length > 0 ? (
                  <div className="space-y-2 mb-3">
                    {integrations.map((credential) => (
                      <div
                        key={`${credential.provider}-${credential.id}`}
                        className="flex flex-wrap items-center gap-2 rounded border border-border bg-background px-2.5 py-2"
                      >
                        <Link2 size={12} className="shrink-0 text-violet-600 dark:text-violet-400" />
                        <span className="text-xs font-medium text-foreground">
                          {credential.label || integrationProviderLabel(credential.provider)}
                        </span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground uppercase">
                          {credential.provider}
                        </span>
                        {credential.credential_type && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300 uppercase">
                            {credential.credential_type}
                          </span>
                        )}
                        <span className="font-mono text-[11px] text-muted-foreground">{credential.id}</span>
                        <span
                          className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                            credential.configured
                              ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300'
                              : 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
                          }`}
                        >
                          {credential.configured ? 'configured' : 'not configured'}
                        </span>
                        {credential.scopes.length > 0 && (
                          <span className="text-[10px] text-muted-foreground truncate max-w-[180px]" title={credential.scopes.join(', ')}>
                            {credential.scopes.join(', ')}
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={() => void removeIntegrationCredential(credential.provider, credential.id)}
                          disabled={settingsDisabled(true) || deletingIntegrationKey === `${credential.provider}:${credential.id}`}
                          className="ml-auto h-7 w-7 flex items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          aria-label={`Remove ${credential.label || credential.provider}`}
                        >
                          {deletingIntegrationKey === `${credential.provider}:${credential.id}`
                            ? <Loader2 size={12} className="animate-spin" />
                            : <Trash2 size={12} />}
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[11px] text-muted-foreground mb-3">None configured.</p>
                )}
                {teamActive && identityContext && (
                  <div className="space-y-2 mb-3">
                    <GitCredentialConnect
                      provider="github"
                      identityContext={identityContext}
                      integrations={integrations}
                      onForbidden={onForbidden}
                    />
                    <GitCredentialConnect
                      provider="gitlab"
                      identityContext={identityContext}
                      integrations={integrations}
                      onForbidden={onForbidden}
                    />
                  </div>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <select
                    value={integrationProvider}
                    disabled={settingsDisabled(true)}
                    onChange={(e) => {
                      setIntegrationProvider(e.target.value as IntegrationProvider);
                      setIntegrationState('idle');
                      setIntegrationMessage('');
                    }}
                    className={FORM_INPUT_CLASS}
                  >
                    {INTEGRATION_PROVIDER_OPTIONS.map((provider) => (
                      <option key={provider.id} value={provider.id}>{provider.label}</option>
                    ))}
                  </select>
                  <input
                    value={integrationLabel}
                    disabled={settingsDisabled(true)}
                    onChange={(e) => setIntegrationLabel(e.target.value)}
                    placeholder="Label (optional)"
                    className={FORM_INPUT_CLASS}
                  />
                  <input
                    type="password"
                    value={integrationToken}
                    disabled={settingsDisabled(true)}
                    onChange={(e) => {
                      setIntegrationToken(e.target.value);
                      setIntegrationState('idle');
                      setIntegrationMessage('');
                    }}
                    placeholder={selectedIntegrationProvider.tokenPlaceholder}
                    className={`${FORM_INPUT_MONO_CLASS} sm:col-span-2`}
                  />
                  <input
                    value={integrationScopes}
                    disabled={settingsDisabled(true)}
                    onChange={(e) => setIntegrationScopes(e.target.value)}
                    placeholder={selectedIntegrationProvider.scopePlaceholder}
                    className={`${FORM_INPUT_MONO_CLASS} sm:col-span-2`}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2 mt-3">
                  <button
                    type="button"
                    onClick={() => void addIntegrationCredential()}
                    disabled={settingsDisabled(true) || integrationState === 'running' || !integrationToken.trim()}
                    className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {integrationState === 'running' ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
                    Add integration
                  </button>
                  {integrationState === 'ok' && (
                    <CheckCircle2 size={12} className="text-emerald-600 dark:text-emerald-400 shrink-0" />
                  )}
                  {integrationState === 'fail' && (
                    <XCircle size={12} className="text-red-600 dark:text-red-400 shrink-0" />
                  )}
                </div>
                {integrationMessage && (
                  <p className="text-[11px] mt-2 text-red-600 dark:text-red-400">{integrationMessage}</p>
                )}
          </CollapsibleConnectionBlock>

          <ConnectionBlock title="Cloud execution">
            <div className="flex items-start justify-between gap-4">
              <p
                className="text-[11px] text-muted-foreground"
                title="Allows dev/execution on cloud models. Removes hybrid cost routing; off by default."
              >
                Let every role run on cloud models (billable).
              </p>
              <Switch
                checked={allowCloudExecutionModel}
                disabled={cloudExecutionPending || settingsDisabled(true)}
                onCheckedChange={(checked) => {
                  setCloudExecutionPending(true);
                  void onUpdateAllowCloudExecutionModel(checked).finally(() => {
                    setCloudExecutionPending(false);
                  });
                }}
                aria-label="Allow cloud execution model"
              />
            </div>
          </ConnectionBlock>

          <EvalRunPanel
            usingMockData={usingMockData}
            modelOptions={evalModelOptions}
            defaultModelId={devFallbackChain[0] ?? evalModelOptions[0]}
            modelLabel={(modelId) => modelDisplayLabel(modelId, cloudModels)}
          />

          <CollapsibleConnectionBlock title="Webhook notifications" hint="Optional attention alerts.">
            <ActionDisclosure text={ACTION_DISCLOSURES.webhook} />
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <input
                type="url"
                value={webhookInput}
                onChange={(e) => setWebhookInput(e.target.value)}
                placeholder="https://hooks.example.com/haao"
                className={`flex-1 min-w-[200px] ${FORM_INPUT_MONO_CLASS}`}
              />
              <button
                type="button"
                onClick={saveNotificationWebhook}
                disabled={webhookState === 'running' || !webhookDirty}
                className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {webhookState === 'running' ? <Loader2 size={11} className="animate-spin" /> : 'Save'}
              </button>
              {webhookState === 'ok' && (
                <CheckCircle2 size={12} className="text-emerald-600 dark:text-emerald-400 shrink-0" />
              )}
              {webhookState === 'fail' && (
                <XCircle size={12} className="text-red-600 dark:text-red-400 shrink-0" />
              )}
            </div>
          </CollapsibleConnectionBlock>

          {teamActive && identityContext && (
            <>
              <CollapsibleConnectionBlock title="Members" hint="Workspace roles and invites.">
                <MembersPanel identityContext={identityContext} onForbidden={onForbidden} />
              </CollapsibleConnectionBlock>
              <CollapsibleConnectionBlock title="Audit log" hint="Append-only privileged action history.">
                <AuditLogPanel identityContext={identityContext} />
              </CollapsibleConnectionBlock>
              <CollapsibleConnectionBlock title="Runners" hint="Where execution runs — split-plane seam.">
                <RunnersPanel identityContext={identityContext} onForbidden={onForbidden} />
              </CollapsibleConnectionBlock>
            </>
          )}
        </section>

        {/* ── Section 2: Role routing ── */}
        <section>
          <SectionHeader
            num="2"
            title="Default model per role"
            subtitle="Ticket overrides win."
          />
          {(requirements.length > 0 || cloudExecutionAssigned) && (
            <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-muted/30 px-4 py-3 text-xs mb-3">
              <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                <Cloud size={12} />
                <span className="font-semibold text-foreground tabular-nums">
                  {formatCloudCost(totalCloudSpend) ?? '$0.0000'}
                </span>
                cloud spend (project)
              </span>
              {cloudExecutionAssigned && (
                <>
                  <span className="text-muted-foreground/40">·</span>
                  <span className="text-amber-700 dark:text-amber-300" title="No spend cap — costs accrue per ticket.">
                    Cloud execution active
                  </span>
                </>
              )}
            </div>
          )}
          <div className="rounded-xl border border-border overflow-x-auto">
            <table className="w-full text-sm min-w-[480px]">
              <thead>
                <tr className="bg-muted/60 border-b border-border">
                  <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-4 py-2.5 w-[34%]">Role</th>
                  <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2.5 w-[42%]">Model</th>
                  <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2.5">Note</th>
                </tr>
              </thead>
              <tbody>
                {roleRoutes.filter((route) => route.id !== 'tech_lead').map((route, i) => (
                  <tr
                    key={route.id}
                    className={`border-b border-border last:border-0 ${i % 2 === 0 ? '' : 'bg-muted/20'}`}
                  >
                    <td className="px-4 py-3 text-xs text-foreground align-top">
                      <span className="inline-flex items-center gap-1">
                        {route.role}
                        {route.id === 'gatekeeper' && (
                          <HelpTip text={HELP_TOOLTIPS.gatekeeper} label="Gatekeeper help" />
                        )}
                        {route.id === 'escalation' && (
                          <HelpTip text={HELP_TOOLTIPS.escalation} label="Escalation help" />
                        )}
                        {route.id === 'dev' && (
                          <HelpTip text={HELP_TOOLTIPS.cloud_vs_local} label="Local vs cloud help" />
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-3 align-top">
                      {route.id === 'dev' ? (
                        <DevFallbackChainEditor
                          chain={devFallbackChain}
                          localOptions={localModelOptions}
                          cloudModels={cloudModels}
                          onChange={onUpdateDevFallbackChain}
                        />
                      ) : route.id === 'escalation' ? (
                        <div className="flex items-center gap-2">
                          <Cloud size={12} className="shrink-0 text-sky-600 dark:text-sky-400" />
                          <span className="text-xs text-foreground">
                            {selectedTechLeadModel
                              ? cloudModelOptionLabel(selectedTechLeadModel)
                              : modelOptionLabel(selectedTechLeadId, cloudModels)}
                          </span>
                          <span className="text-[10px] text-muted-foreground" title="Follows Tech Lead model from Connections.">
                            (follows Tech Lead)
                          </span>
                        </div>
                      ) : (
                        <div className="space-y-1.5">
                          <div className="flex items-center gap-2">
                            {isCloudModel(route.model) ? (
                              <Cloud size={12} className="shrink-0 text-sky-600 dark:text-sky-400" />
                            ) : (
                              <span
                                className="w-1.5 h-1.5 rounded-full shrink-0"
                                style={{ backgroundColor: getModelMeta(route.model).accent }}
                              />
                            )}
                            <select
                              value={route.model}
                              disabled={viewerLocked}
                              onChange={(e) => onUpdateRoute(route.id, e.target.value as AssignedModel)}
                              className="text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring text-foreground w-full"
                            >
                              {localModelOptions.length > 0 && (
                                <optgroup label="Local">
                                  {routeModelOptions(route.id, localModelOptions, cloudModels, route.model)
                                    .filter((model) => !isCloudModel(model))
                                    .map((model) => (
                                      <option key={model} value={model}>{modelOptionLabel(model, cloudModels)}</option>
                                    ))}
                                </optgroup>
                              )}
                              {cloudModels.length > 0 && (
                                <optgroup label="Cloud (billable)">
                                  {routeModelOptions(route.id, localModelOptions, cloudModels, route.model)
                                    .filter((model) => isCloudModel(model))
                                    .map((model) => {
                                      const registry = cloudModels.find((item) => item.id === model);
                                      return (
                                        <option key={model} value={model}>
                                          {modelOptionLabel(model, cloudModels)}
                                          {registry && !registry.key_configured ? ' (no key)' : ''}
                                        </option>
                                      );
                                    })}
                                </optgroup>
                              )}
                            </select>
                          </div>
                          {isCloudModel(route.model) && (
                            <p className="text-[10px] text-amber-700 dark:text-amber-300" title="Billable — tracked in History.">
                              Billable cloud
                            </p>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-3 text-xs text-muted-foreground align-top">
                      {route.id === 'gatekeeper' && isCloudModel(route.model)
                        ? `${route.note} · billable cloud`
                        : route.note}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details className="mt-2 text-[11px] text-muted-foreground">
            <summary className="cursor-pointer select-none inline-flex items-center gap-1">
              <Info size={11} /> Saves immediately; override per ticket.
            </summary>
          </details>
        </section>

        {/* ── Section 3: Per-model instructions ── */}
        <section>
          <SectionHeader
            num="3"
            title="Per-model instructions"
            subtitle="Saved per model on the server."
          />
          <ModelStatusLegend />
          <div className="space-y-3 mt-3">
            {localRoster.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border px-4 py-8 text-center">
                <p className="text-sm text-muted-foreground">No local models discovered.</p>
                <p className="text-xs text-muted-foreground mt-1">Add a server URL in Connections, then refresh.</p>
                <button
                  type="button"
                  onClick={refreshLocalModels}
                  disabled={discoveryState === 'running'}
                  className="mt-3 inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {discoveryState === 'running' ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
                  Refresh discovery
                </button>
              </div>
            ) : (
              localRoster.map((cfg) => renderModelCard(cfg))
            )}
          </div>
          {cloudModels.length > 0 && (
            <div className="space-y-3 mt-6">
              <p className="text-xs font-medium text-muted-foreground">Cloud models</p>
              {cloudModels.map((model) => renderModelCard(cloudModelToConfig(model)))}
            </div>
          )}
        </section>

        <div className="h-8" />
      </div>
    </div>
  );
}

function normalizeEndpointRows(endpoints: BackendLocalModelEndpoint[]): BackendLocalModelEndpoint[] {
  return endpoints
    .map((endpoint, index) => ({
      id: (endpoint.id || `local-${index + 1}`).trim(),
      label: (endpoint.label || endpoint.id || `Local ${index + 1}`).trim(),
      base_url: endpoint.base_url.trim().replace(/\/+$/, ''),
      api_key: endpoint.api_key ?? '',
    }))
    .filter((endpoint) => endpoint.base_url);
}
