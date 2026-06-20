import { useEffect, useMemo, useState } from 'react';
import { Loader2, CheckCircle2, XCircle, Info, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { ModelCard } from './ModelCard';
import { DevFallbackChainEditor } from './DevFallbackChainEditor';
import { DEFAULT_LOCAL_MODELS, getModelMeta } from '../constants';
import { CLAUDE_TECH_LEAD, CLOUD_ONLY_ROUTE_IDS } from '../copy';
import type { ModelConfig, RoleRoute, AssignedModel } from '../types';
import type { BackendLocalModelEndpoint } from '../api/types';
import type { CloudReasonerConfig } from '../api/client';

type TestStatus = 'idle' | 'running' | 'ok' | 'fail';

interface Props {
  modelConfigs: ModelConfig[];
  roleRoutes: RoleRoute[];
  localModelIds: string[];
  localModelEndpoints: BackendLocalModelEndpoint[];
  cloudReasoner: CloudReasonerConfig | null;
  devFallbackChain: string[];
  notificationWebhook: string;
  onUpdateModel: (id: AssignedModel, updates: Partial<ModelConfig>) => void;
  onUpdateRoute: (id: string, model: AssignedModel) => void;
  onUpdateDevFallbackChain: (chain: string[]) => void;
  onSaveLocalModelEndpoints: (endpoints: BackendLocalModelEndpoint[]) => Promise<void>;
  onRefreshLocalModels: () => Promise<{ models: string[]; endpoints: BackendLocalModelEndpoint[] }>;
  onSaveCloudReasoner: (modelId: string) => Promise<CloudReasonerConfig>;
  onSaveNotificationWebhook: (webhookUrl: string) => Promise<string>;
  onLoadModelAdditionalInstructions: (modelId: string) => Promise<string>;
  onSaveModelAdditionalInstructions: (modelId: string, text: string) => Promise<string>;
}

function SectionHeader({ num, title, subtitle }: { num: string; title: string; subtitle?: string }) {
  return (
    <div className="flex items-start gap-3 mb-4">
      <span className="w-6 h-6 rounded flex items-center justify-center bg-muted text-muted-foreground text-xs font-mono shrink-0 mt-0.5">
        {num}
      </span>
      <div>
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
    </div>
  );
}

function routeModelOptions(routeId: string, localModelOptions: string[], currentModel: AssignedModel): AssignedModel[] {
  if (routeId === 'dev') return localModelOptions;
  if (CLOUD_ONLY_ROUTE_IDS.has(routeId)) {
    return [CLAUDE_TECH_LEAD];
  }
  const options = [...new Set([...localModelOptions, currentModel])].filter((m) => m !== CLAUDE_TECH_LEAD);
  return options.length ? options : localModelOptions;
}

export function ModelsPage({
  modelConfigs,
  roleRoutes,
  localModelIds,
  localModelEndpoints,
  cloudReasoner,
  devFallbackChain,
  notificationWebhook,
  onUpdateModel,
  onUpdateRoute,
  onUpdateDevFallbackChain,
  onSaveLocalModelEndpoints,
  onRefreshLocalModels,
  onSaveCloudReasoner,
  onSaveNotificationWebhook,
  onLoadModelAdditionalInstructions,
  onSaveModelAdditionalInstructions,
}: Props) {
  const [expandedModels, setExpandedModels] = useState<Set<AssignedModel>>(new Set());
  const [endpointRows, setEndpointRows] = useState<BackendLocalModelEndpoint[]>(localModelEndpoints);
  const [cloudProvider, setCloudProvider] = useState('anthropic');
  const [cloudModelId, setCloudModelId] = useState('');
  const [cloudSaveState, setCloudSaveState] = useState<TestStatus>('idle');
  const [cloudSaveMessage, setCloudSaveMessage] = useState('');
  const [webhookInput, setWebhookInput] = useState(notificationWebhook);
  const [webhookState, setWebhookState] = useState<TestStatus>('idle');
  const [discoveryState, setDiscoveryState] = useState<TestStatus>('idle');
  const [isSavingEndpoints, setIsSavingEndpoints] = useState(false);
  const endpointDirty = JSON.stringify(endpointRows) !== JSON.stringify(localModelEndpoints);
  const webhookDirty = webhookInput.trim() !== notificationWebhook.trim();
  const localModelOptions = useMemo(
    () => (localModelIds.length ? localModelIds : DEFAULT_LOCAL_MODELS),
    [localModelIds],
  );

  useEffect(() => {
    setEndpointRows(localModelEndpoints);
  }, [localModelEndpoints]);

  useEffect(() => {
    if (!cloudReasoner) return;
    const idx = cloudReasoner.model_id.indexOf(':');
    if (idx > 0) {
      setCloudProvider(cloudReasoner.model_id.slice(0, idx));
      setCloudModelId(cloudReasoner.model_id.slice(idx + 1));
    } else {
      setCloudProvider(cloudReasoner.provider || 'anthropic');
      setCloudModelId(cloudReasoner.model_id);
    }
  }, [cloudReasoner]);

  const cloudProviderOptions = cloudReasoner?.providers ?? [];
  const providerKeyConfigured = cloudProviderOptions.find((p) => p.id === cloudProvider)?.key_configured ?? true;

  async function saveCloudReasoner() {
    setCloudSaveState('running');
    setCloudSaveMessage('');
    try {
      await onSaveCloudReasoner(`${cloudProvider}:${cloudModelId.trim()}`);
      setCloudSaveState('ok');
    } catch (error) {
      setCloudSaveState('fail');
      setCloudSaveMessage(error instanceof Error ? error.message : 'Could not save cloud reasoner.');
    }
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
  const cloudRoster = modelConfigs.filter((cfg) => cfg.backend === 'Cloud API');

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
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8 space-y-10">

        {/* ── Section 1: Connections ── */}
        <section>
          <SectionHeader
            num="1"
            title="Connections"
            subtitle="Server endpoints and notification settings. Each section saves independently."
          />
          <div className="rounded-xl border border-border bg-card overflow-hidden">
            <div className="divide-y divide-border">
              <div className="px-4 py-4">
                <label className="text-xs font-medium text-foreground block mb-1.5">
                  Local model server URLs
                </label>
                <p className="text-[11px] text-muted-foreground mb-2">
                  OpenAI-compatible base URLs (for example LM Studio). Saved to the server.
                </p>
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
                        className="font-mono text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
                        placeholder="Label"
                      />
                      <input
                        type="text"
                        value={endpoint.base_url}
                        onChange={(e) => updateEndpoint(index, { base_url: e.target.value })}
                        className="font-mono text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
                        placeholder="http://localhost:1234/v1"
                      />
                      <input
                        type="password"
                        value={endpoint.api_key ?? ''}
                        onChange={(e) => updateEndpoint(index, { api_key: e.target.value })}
                        className="font-mono text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
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
                <div className="flex flex-wrap items-center gap-2 mt-2">
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
                <details className="mt-1 text-[11px] text-muted-foreground">
                  <summary className="cursor-pointer select-none">View discovered model IDs</summary>
                  <p className="mt-1 break-all font-mono">
                    {(localModelIds.length ? localModelIds : DEFAULT_LOCAL_MODELS).join(', ')}
                  </p>
                </details>
              </div>

              <div className="px-4 py-4">
                <div>
                  <label className="text-xs font-medium text-foreground block mb-1.5">
                    Cloud reasoner (Tech Lead)
                  </label>
                  <p className="text-[11px] text-muted-foreground mb-1.5">
                    The cloud model that decomposes requirements and runs the technical audit. Pick a provider and model, then Save.
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <select
                      value={cloudProvider}
                      onChange={(e) => { setCloudProvider(e.target.value); setCloudSaveState('idle'); setCloudSaveMessage(''); }}
                      className="text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
                    >
                      {(cloudProviderOptions.length ? cloudProviderOptions : [{ id: 'anthropic', label: 'Claude (Anthropic)', key_configured: true }]).map((p) => (
                        <option key={p.id} value={p.id}>{p.label}{p.key_configured ? '' : ' (no key)'}</option>
                      ))}
                    </select>
                    <input
                      value={cloudModelId}
                      onChange={(e) => { setCloudModelId(e.target.value); setCloudSaveState('idle'); setCloudSaveMessage(''); }}
                      placeholder="e.g. gpt-4o, gemini-2.0-flash, claude-sonnet-4-6"
                      className="flex-1 min-w-[200px] font-mono text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
                    />
                    <button
                      type="button"
                      onClick={saveCloudReasoner}
                      disabled={cloudSaveState === 'running' || !cloudModelId.trim()}
                      className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {cloudSaveState === 'running' ? <Loader2 size={11} className="animate-spin" /> : 'Save'}
                    </button>
                    {cloudSaveState === 'ok' && (
                      <CheckCircle2 size={12} className="text-emerald-600 dark:text-emerald-400 shrink-0" />
                    )}
                    {cloudSaveState === 'fail' && (
                      <XCircle size={12} className="text-red-600 dark:text-red-400 shrink-0" />
                    )}
                  </div>
                  {!providerKeyConfigured && (
                    <p className="text-[11px] mt-1.5 text-amber-600 dark:text-amber-400">
                      No API key configured for this provider on the server. Set it in the server environment (e.g. <span className="font-mono">OPENAI_API_KEY</span> / <span className="font-mono">GEMINI_API_KEY</span> / <span className="font-mono">CLAUDE_API_KEY</span>).
                    </p>
                  )}
                  {cloudSaveMessage && (
                    <p className="text-[11px] mt-1.5 text-red-600 dark:text-red-400">{cloudSaveMessage}</p>
                  )}
                </div>
              </div>

              <div className="px-4 py-4">
                <label className="text-xs font-medium text-foreground block mb-1.5">
                  Notify me when attention is needed
                </label>
                <p className="text-[11px] text-muted-foreground mb-2">
                  Optional webhook URL. Leave empty to see alerts only in this app.
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    type="url"
                    value={webhookInput}
                    onChange={(e) => setWebhookInput(e.target.value)}
                    placeholder="https://hooks.example.com/haao"
                    className="flex-1 min-w-[200px] font-mono text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
                  />
                  <button
                    type="button"
                    onClick={saveNotificationWebhook}
                    disabled={webhookState === 'running' || !webhookDirty}
                    className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {webhookState === 'running' ? <Loader2 size={11} className="animate-spin" /> : 'Save webhook'}
                  </button>
                  {webhookState === 'ok' && (
                    <CheckCircle2 size={12} className="text-emerald-600 dark:text-emerald-400 shrink-0" />
                  )}
                  {webhookState === 'fail' && (
                    <XCircle size={12} className="text-red-600 dark:text-red-400 shrink-0" />
                  )}
                </div>
              </div>

              <div className="px-4 py-4 bg-muted/20">
                <p className="text-xs font-medium text-foreground">Auto-escalation to the cloud reasoner</p>
                <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">
                  When local retry attempts are exhausted, HAAO automatically reassigns the ticket to the Tech Lead. This is always enabled on the server — there is no separate toggle.
                </p>
              </div>
            </div>

            {endpointDirty && (
              <div className="px-4 py-3 border-t border-border bg-amber-50/50 dark:bg-amber-950/20 flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs text-amber-600 dark:text-amber-400">Unsaved endpoint changes</span>
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
                    {isSavingEndpoints ? 'Saving…' : 'Save endpoints'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* ── Section 2: Role routing ── */}
        <section>
          <SectionHeader
            num="2"
            title="Default model per role"
            subtitle="Which model HAAO picks for each job. Ticket-level overrides win."
          />
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
                {roleRoutes.map((route, i) => (
                  <tr
                    key={route.id}
                    className={`border-b border-border last:border-0 ${i % 2 === 0 ? '' : 'bg-muted/20'}`}
                  >
                    <td className="px-4 py-3 text-xs text-foreground align-top">{route.role}</td>
                    <td className="px-3 py-3 align-top">
                      {route.id === 'dev' ? (
                        <DevFallbackChainEditor
                          chain={devFallbackChain}
                          options={localModelOptions}
                          onChange={onUpdateDevFallbackChain}
                        />
                      ) : CLOUD_ONLY_ROUTE_IDS.has(route.id) ? (
                        <div className="flex items-center gap-2">
                          <span
                            className="w-1.5 h-1.5 rounded-full shrink-0"
                            style={{ backgroundColor: getModelMeta(CLAUDE_TECH_LEAD).accent }}
                          />
                          <span className="text-xs text-foreground">{getModelMeta(CLAUDE_TECH_LEAD).label}</span>
                          <span className="text-[10px] text-muted-foreground">(cloud only)</span>
                        </div>
                      ) : (
                        <div className="flex items-center gap-2">
                          <span
                            className="w-1.5 h-1.5 rounded-full shrink-0"
                            style={{ backgroundColor: getModelMeta(route.model).accent }}
                          />
                          <select
                            value={route.model}
                            onChange={(e) => onUpdateRoute(route.id, e.target.value as AssignedModel)}
                            className="text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring text-foreground w-full"
                          >
                            {routeModelOptions(route.id, localModelOptions, route.model).map((m) => (
                              <option key={m} value={m}>{getModelMeta(m).label}</option>
                            ))}
                          </select>
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-3 text-xs text-muted-foreground align-top">{route.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-start gap-1.5 mt-2">
            <Info size={11} className="text-muted-foreground shrink-0 mt-0.5" />
            <p className="text-[11px] text-muted-foreground">
              Changes save immediately. Override the assigned model on an individual ticket when needed.
            </p>
          </div>
        </section>

        {/* ── Section 3: Per-model instructions ── */}
        <section>
          <SectionHeader
            num="3"
            title="Per-model instructions"
            subtitle="Extra guidance appended to each model's prompt. Saved to the server per model."
          />
          <div className="space-y-3">
            {localRoster.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border px-4 py-8 text-center">
                <p className="text-sm text-muted-foreground">No local models discovered yet.</p>
                <p className="text-xs text-muted-foreground mt-1">
                  Add a local model server URL in section 1, save, then refresh discovery.
                </p>
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
          {cloudRoster.length > 0 && (
            <div className="space-y-3 mt-6">
              <p className="text-xs font-medium text-muted-foreground">Cloud models</p>
              {cloudRoster.map((cfg) => renderModelCard(cfg))}
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
