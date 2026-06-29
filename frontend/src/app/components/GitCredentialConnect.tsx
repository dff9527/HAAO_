import { useEffect, useState } from 'react';
import { ExternalLink, Link2, Loader2 } from 'lucide-react';
import { apiClient, type IntegrationCredential, type IntegrationProvider } from '../api/client';
import type { IdentityContext } from '../api/types';
import {
  MOCK_GIT_APP_INSTALL,
  activeGitCredentialKind,
  canManageTeam,
  mockIntegrationsWithApp,
  mockTeamPlaneEnabled,
} from '../teamPlaneUtils';

interface Props {
  provider: 'github' | 'gitlab';
  identityContext: IdentityContext;
  integrations: IntegrationCredential[];
  onForbidden?: (message: string) => void;
}

export function GitCredentialConnect({
  provider,
  identityContext,
  integrations,
}: Props) {
  const canManage = canManageTeam(identityContext);
  const workspaceId = identityContext.workspace_id;
  const [installUrl, setInstallUrl] = useState('');
  const [installed, setInstalled] = useState(false);
  const [loading, setLoading] = useState(true);

  const providerIntegrations = integrations.filter((item) => item.provider === provider);
  const patCredential = providerIntegrations.find((item) => item.credential_type !== 'app');
  const appCredential = providerIntegrations.find((item) => item.credential_type === 'app');
  const activeKind = activeGitCredentialKind(provider, integrations);

  useEffect(() => {
    let active = true;
    setLoading(true);
    const load = mockTeamPlaneEnabled()
      ? Promise.resolve(MOCK_GIT_APP_INSTALL[provider])
      : apiClient.getGitAppInstallInfo(provider, workspaceId);
    load
      .then((info) => {
        if (!active) return;
        if (info) {
          setInstallUrl(info.install_url);
          setInstalled(info.installed);
        }
      })
      .catch(() => {
        if (!active) return;
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [provider, workspaceId]);

  const providerLabel = provider === 'github' ? 'GitHub' : 'GitLab';

  return (
    <div className="rounded border border-border bg-muted/20 px-3 py-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Link2 size={12} className="text-violet-600 dark:text-violet-400" />
        <span className="text-xs font-medium text-foreground">{providerLabel} credential path</span>
        {activeKind && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
            active · {activeKind === 'app' ? 'App' : 'PAT'}
          </span>
        )}
      </div>
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
          ) : appCredential || installed ? (
            <p className="text-[10px] text-muted-foreground">
              {appCredential?.label || `${providerLabel} App`} connected for this workspace.
            </p>
          ) : (
            <p className="text-[10px] text-muted-foreground">App not installed for this workspace.</p>
          )}
          {canManage && installUrl && (
            <a
              href={installUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border hover:bg-muted transition-colors"
            >
              <ExternalLink size={10} />
              Install App
            </a>
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
