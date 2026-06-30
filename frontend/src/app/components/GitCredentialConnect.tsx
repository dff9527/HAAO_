import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, ExternalLink, Link2, Loader2, XCircle } from 'lucide-react';
import { apiClient, type IntegrationCredential, type IntegrationProvider } from '../api/client';
import type { GitAppInstallInfo, GitCredentialKind, IdentityContext } from '../api/types';
import {
  MOCK_GIT_APP_INSTALL,
  activeGitCredentialKind,
  appendInstallState,
  canManageTeam,
  clearGitInstallCallbackParams,
  forbiddenMessage,
  getMockGitCredentialPreference,
  isForbiddenError,
  maskInstallationId,
  mockIntegrationsWithApp,
  mockTeamPlaneEnabled,
  parseGitInstallCallback,
  setMockGitCredentialPreference,
} from '../teamPlaneUtils';

interface Props {
  provider: 'github' | 'gitlab';
  identityContext: IdentityContext;
  integrations: IntegrationCredential[];
  onForbidden?: (message: string) => void;
  onIntegrationsChange?: () => void;
}

export function GitCredentialConnect({
  provider,
  identityContext,
  integrations,
  onForbidden,
  onIntegrationsChange,
}: Props) {
  const canManage = canManageTeam(identityContext);
  const workspaceId = identityContext.workspace_id;
  const [installUrl, setInstallUrl] = useState('');
  const [installInfo, setInstallInfo] = useState<GitAppInstallInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [preference, setPreference] = useState<GitCredentialKind | null>(null);
  const [callbackMessage, setCallbackMessage] = useState('');
  const [callbackTone, setCallbackTone] = useState<'ok' | 'warn' | 'error'>('ok');

  const providerIntegrations = integrations.filter((item) => item.provider === provider);
  const patCredential = providerIntegrations.find((item) => item.credential_type !== 'app');
  const appCredential = providerIntegrations.find((item) => item.credential_type === 'app');
  const activeKind = activeGitCredentialKind(provider, integrations, preference);
  const installed = Boolean(installInfo?.installed || appCredential);

  const loadInstallInfo = useCallback(async () => {
    setLoading(true);
    try {
      const info = mockTeamPlaneEnabled()
        ? MOCK_GIT_APP_INSTALL[provider]
        : await apiClient.getGitAppInstallInfo(provider, workspaceId);
      if (info) {
        setInstallInfo(info);
        setInstallUrl(info.install_url);
      } else {
        setInstallInfo(null);
        setInstallUrl('');
      }
    } catch {
      if (mockTeamPlaneEnabled()) {
        setInstallInfo(MOCK_GIT_APP_INSTALL[provider]);
        setInstallUrl(MOCK_GIT_APP_INSTALL[provider].install_url);
      } else {
        setInstallInfo(null);
        setInstallUrl('');
      }
    } finally {
      setLoading(false);
    }
  }, [provider, workspaceId]);

  const loadPreference = useCallback(async () => {
    if (mockTeamPlaneEnabled()) {
      setPreference(getMockGitCredentialPreference(workspaceId, provider));
      return;
    }
    try {
      const record = await apiClient.getGitCredentialPreference(provider, workspaceId);
      setPreference(record?.credential_kind ?? null);
    } catch {
      setPreference(null);
    }
  }, [provider, workspaceId]);

  useEffect(() => {
    void loadInstallInfo();
    void loadPreference();
  }, [loadInstallInfo, loadPreference]);

  const providerLabel = provider === 'github' ? 'GitHub' : 'GitLab';
  const accountLabel = installInfo?.account || appCredential?.label;
  const installationId = installInfo?.installation_id;

  useEffect(() => {
    const callback = parseGitInstallCallback(window.location.search);
    if (!callback || callback.provider !== provider) return;
    if (callback.workspaceId !== workspaceId) return;

    void (async () => {
      if (callback.status === 'success' && callback.installationId) {
        setBusy(true);
        try {
          if (mockTeamPlaneEnabled()) {
            MOCK_GIT_APP_INSTALL[provider] = {
              ...MOCK_GIT_APP_INSTALL[provider],
              installed: true,
              account: callback.account ?? `${provider}-account`,
              installation_id: callback.installationId,
              credential_id: `${provider}-app-${callback.installationId}`,
              label: provider === 'github' ? 'HAAO GitHub App' : 'HAAO GitLab App',
            };
            setCallbackMessage(`${providerLabel} App connected.`);
            setCallbackTone('ok');
          } else {
            await apiClient.upsertGitAppInstallation({
              workspace_id: workspaceId,
              provider,
              account: callback.account ?? `${provider}-account`,
              installation_id: callback.installationId,
            });
            setCallbackMessage(`${providerLabel} App connected.`);
            setCallbackTone('ok');
          }
          await loadInstallInfo();
          onIntegrationsChange?.();
        } catch (error) {
          setCallbackMessage(
            error instanceof Error ? error.message : 'Could not save App installation.',
          );
          setCallbackTone('error');
        } finally {
          setBusy(false);
          clearGitInstallCallbackParams();
        }
        return;
      }

      if (callback.status === 'cancelled') {
        setCallbackMessage(`${providerLabel} App install was cancelled.`);
        setCallbackTone('warn');
      } else {
        setCallbackMessage(callback.error ?? `${providerLabel} App install failed.`);
        setCallbackTone('error');
      }
      clearGitInstallCallbackParams();
    })();
  }, [provider, workspaceId, loadInstallInfo, onIntegrationsChange, providerLabel]);

  async function switchCredential(kind: GitCredentialKind) {
    if (!canManage || kind === activeKind) return;
    setBusy(true);
    setCallbackMessage('');
    try {
      if (mockTeamPlaneEnabled()) {
        setMockGitCredentialPreference(workspaceId, provider, kind);
        setPreference(kind);
        return;
      }
      const record = await apiClient.setGitCredentialPreference({
        workspace_id: workspaceId,
        provider,
        credential_kind: kind,
      });
      setPreference(record.credential_kind);
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setCallbackMessage(error instanceof Error ? error.message : 'Could not switch credential.');
        setCallbackTone('error');
      }
    } finally {
      setBusy(false);
    }
  }

  function startInstall() {
    if (!installUrl) return;
    if (mockTeamPlaneEnabled()) {
      const url = new URL(window.location.href);
      url.searchParams.set('haao_git_install', provider);
      url.searchParams.set('installation_id', `${Date.now().toString().slice(-8)}`);
      url.searchParams.set('account', provider === 'github' ? 'acme-corp' : 'acme-group');
      url.searchParams.set('state', workspaceId);
      window.location.assign(url.toString());
      return;
    }
    window.location.assign(appendInstallState(installUrl, workspaceId));
  }

  return (
    <div className="rounded border border-border bg-muted/20 px-3 py-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Link2 size={12} className="text-violet-600 dark:text-violet-400" />
        <span className="text-xs font-medium text-foreground">{providerLabel} credential path</span>
        {activeKind && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
            PRs use · {activeKind === 'app' ? 'App' : 'PAT'}
          </span>
        )}
      </div>
      {callbackMessage && (
        <div
          className={`flex items-start gap-1.5 rounded border px-2 py-1.5 text-[11px] ${
            callbackTone === 'ok'
              ? 'border-emerald-200 dark:border-emerald-900 text-emerald-700 dark:text-emerald-300'
              : callbackTone === 'warn'
                ? 'border-amber-200 dark:border-amber-900 text-amber-700 dark:text-amber-300'
                : 'border-red-200 dark:border-red-900 text-red-700 dark:text-red-300'
          }`}
        >
          {callbackTone === 'ok' ? (
            <CheckCircle2 size={12} className="shrink-0 mt-0.5" />
          ) : callbackTone === 'warn' ? (
            <AlertCircle size={12} className="shrink-0 mt-0.5" />
          ) : (
            <XCircle size={12} className="shrink-0 mt-0.5" />
          )}
          <span>{callbackMessage}</span>
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="rounded border border-border bg-background px-2.5 py-2 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-medium text-foreground">Personal access token</span>
            {activeKind === 'pat' && (
              <span className="text-[10px] text-emerald-600 dark:text-emerald-400">active</span>
            )}
          </div>
          {patCredential ? (
            <p className="text-[10px] text-muted-foreground">
              {patCredential.label || 'PAT'} · {patCredential.configured ? 'configured' : 'not configured'}
            </p>
          ) : (
            <p className="text-[10px] text-muted-foreground">No PAT saved — use the form below.</p>
          )}
          {canManage && patCredential?.configured && activeKind !== 'pat' && (
            <button
              type="button"
              onClick={() => void switchCredential('pat')}
              disabled={busy}
              className="text-[11px] px-2 py-1 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              Use PAT for PRs
            </button>
          )}
        </div>
        <div className="rounded border border-border bg-background px-2.5 py-2 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-medium text-foreground">Install App</span>
            {activeKind === 'app' && (
              <span className="text-[10px] text-emerald-600 dark:text-emerald-400">active</span>
            )}
          </div>
          {loading ? (
            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <Loader2 size={10} className="animate-spin" />
              Loading…
            </div>
          ) : installed ? (
            <div className="space-y-0.5">
              <p className="text-[10px] text-muted-foreground">
                {appCredential?.label || installInfo?.label || `${providerLabel} App`} connected for this workspace.
              </p>
              {accountLabel && (
                <p className="text-[10px] text-muted-foreground">
                  Account · <span className="font-mono text-foreground">{accountLabel}</span>
                </p>
              )}
              {installationId && (
                <p className="text-[10px] text-muted-foreground">
                  Installation · <span className="font-mono text-foreground">{maskInstallationId(installationId)}</span>
                </p>
              )}
            </div>
          ) : (
            <p className="text-[10px] text-muted-foreground">App not installed for this workspace.</p>
          )}
          {canManage && !installed && installUrl && (
            <button
              type="button"
              onClick={startInstall}
              disabled={busy}
              className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              {busy ? <Loader2 size={10} className="animate-spin" /> : <ExternalLink size={10} />}
              Install {providerLabel} App
            </button>
          )}
          {canManage && installed && activeKind !== 'app' && (
            <button
              type="button"
              onClick={() => void switchCredential('app')}
              disabled={busy}
              className="text-[11px] px-2 py-1 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              Use App for PRs
            </button>
          )}
          {!canManage && (
            <p className="text-[10px] text-muted-foreground">App install requires owner or admin.</p>
          )}
        </div>
      </div>
    </div>
  );
}

export function gitIntegrationsForMock(provider: IntegrationProvider): IntegrationCredential[] {
  if (!mockTeamPlaneEnabled()) return [];
  return mockIntegrationsWithApp().filter((item) => item.provider === provider);
}
