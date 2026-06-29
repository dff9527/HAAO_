import type {
  AuditEvent,
  GitCredentialKind,
  IdentityContext,
  MembershipRole,
  RunnerRecord,
  TeamPlaneAction,
  WorkspaceMembership,
} from './api/types';
import type { IntegrationCredential } from './api/client';
import { ApiError } from './api/client';

const ROLE_PERMISSIONS: Record<MembershipRole, TeamPlaneAction[]> = {
  owner: ['read', 'mutate', 'admin'],
  admin: ['read', 'mutate', 'admin'],
  member: ['read', 'mutate'],
  viewer: ['read'],
};

export function mockTeamPlaneEnabled(): boolean {
  return import.meta.env.VITE_MOCK_TEAM_PLANE === 'true';
}

export function permissionsForRole(role: MembershipRole): TeamPlaneAction[] {
  return ROLE_PERMISSIONS[role];
}

export function hasTeamPermission(
  context: IdentityContext | null | undefined,
  action: TeamPlaneAction,
): boolean {
  if (!context) return true;
  if (!context.identity_configured || context.implicit_owner) return true;
  return context.permissions.includes(action);
}

export function canManageTeam(context: IdentityContext | null | undefined): boolean {
  return hasTeamPermission(context, 'admin');
}

export function isReadOnlyTeamRole(context: IdentityContext | null | undefined): boolean {
  if (!context?.identity_configured || context.implicit_owner) return false;
  return context.role === 'viewer';
}

export function roleBadgeClass(role: MembershipRole): string {
  switch (role) {
    case 'owner':
      return 'bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300';
    case 'admin':
      return 'bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300';
    case 'member':
      return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300';
    case 'viewer':
      return 'bg-muted text-muted-foreground';
    default:
      return 'bg-muted text-muted-foreground';
  }
}

export function roleLabel(role: MembershipRole): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

const HEARTBEAT_ONLINE_MS = 90_000;

export function deriveRunnerStatus(
  runner: Pick<RunnerRecord, 'revoked_at' | 'last_heartbeat_at'>,
): RunnerRecord['status'] {
  if (runner.revoked_at) return 'revoked';
  if (!runner.last_heartbeat_at) return 'offline';
  const heartbeatMs = Date.parse(runner.last_heartbeat_at);
  if (Number.isNaN(heartbeatMs)) return 'offline';
  return Date.now() - heartbeatMs <= HEARTBEAT_ONLINE_MS ? 'online' : 'offline';
}

export function normalizeRunner(raw: RunnerRecord): RunnerRecord {
  return {
    ...raw,
    status: raw.status ?? deriveRunnerStatus(raw),
  };
}

export function runnerStatusBadgeClass(status: RunnerRecord['status']): string {
  switch (status) {
    case 'online':
      return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300';
    case 'offline':
      return 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300';
    case 'revoked':
      return 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300';
    default:
      return 'bg-muted text-muted-foreground';
  }
}

export function formatAuditTimestamp(ts: string): string {
  const parsed = Date.parse(ts);
  if (Number.isNaN(parsed)) return ts;
  return new Date(parsed).toLocaleString();
}

export function formatRelativeHeartbeat(ts: string | null | undefined): string {
  if (!ts) return 'never';
  const parsed = Date.parse(ts);
  if (Number.isNaN(parsed)) return ts;
  const deltaSec = Math.max(0, Math.round((Date.now() - parsed) / 1000));
  if (deltaSec < 60) return `${deltaSec}s ago`;
  const deltaMin = Math.round(deltaSec / 60);
  if (deltaMin < 60) return `${deltaMin}m ago`;
  const deltaHr = Math.round(deltaMin / 60);
  return `${deltaHr}h ago`;
}

export function activeGitCredentialKind(
  provider: 'github' | 'gitlab',
  integrations: IntegrationCredential[],
): GitCredentialKind | null {
  const providerCreds = integrations.filter(
    (item) => item.provider === provider && item.configured,
  );
  if (providerCreds.length === 0) return null;
  const appCred = providerCreds.find((item) => item.credential_type === 'app');
  if (appCred) return 'app';
  return 'pat';
}

export function isForbiddenError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function forbiddenMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return 'You do not have permission for this action.';
}

export const MOCK_IDENTITY_CONTEXT: IdentityContext = {
  identity_configured: true,
  actor_id: 'user-owner-1',
  workspace_id: 'default',
  role: 'owner',
  implicit_owner: false,
  permissions: permissionsForRole('owner'),
};

export const MOCK_MEMBERSHIPS: WorkspaceMembership[] = [
  {
    user_id: 'user-owner-1',
    workspace_id: 'default',
    role: 'owner',
    created_at: '2026-06-01T10:00:00Z',
    email: 'owner@example.com',
    display_name: 'Alex Owner',
  },
  {
    user_id: 'user-admin-1',
    workspace_id: 'default',
    role: 'admin',
    created_at: '2026-06-02T11:00:00Z',
    email: 'admin@example.com',
    display_name: 'Sam Admin',
  },
  {
    user_id: 'user-member-1',
    workspace_id: 'default',
    role: 'member',
    created_at: '2026-06-03T12:00:00Z',
    email: 'dev@example.com',
    display_name: 'Jordan Dev',
  },
  {
    user_id: 'user-viewer-1',
    workspace_id: 'default',
    role: 'viewer',
    created_at: '2026-06-04T13:00:00Z',
    email: 'viewer@example.com',
    display_name: 'Riley Viewer',
  },
];

export const MOCK_AUDIT_EVENTS: AuditEvent[] = [
  {
    id: 1,
    actor_id: 'user-owner-1',
    workspace_id: 'default',
    action: 'runner.token.issue',
    target: 'runner-a1b2c3',
    ts: '2026-06-28T09:15:00Z',
    ip: '10.0.0.4',
  },
  {
    id: 2,
    actor_id: 'user-admin-1',
    workspace_id: 'default',
    action: 'integration.app.install',
    target: 'github:app-default',
    ts: '2026-06-28T10:02:00Z',
    ip: '10.0.0.8',
  },
  {
    id: 3,
    actor_id: 'user-admin-1',
    workspace_id: 'default',
    action: 'cloud_model.add',
    target: 'cloud-openai-gpt4',
    ts: '2026-06-28T11:30:00Z',
  },
  {
    id: 4,
    actor_id: 'user-member-1',
    workspace_id: 'default',
    action: 'ticket.accept',
    target: 'T-012',
    ts: '2026-06-28T14:45:00Z',
    ip: '192.168.1.22',
  },
  {
    id: 5,
    actor_id: 'user-owner-1',
    workspace_id: 'default',
    action: 'membership.role_change',
    target: 'user-viewer-1',
    ts: '2026-06-29T08:00:00Z',
  },
];

export const MOCK_RUNNERS: RunnerRecord[] = [
  {
    id: 'runner-a1b2c3',
    workspace_id: 'default',
    label: 'mac-studio-runner',
    created_at: '2026-06-20T08:00:00Z',
    last_heartbeat_at: new Date(Date.now() - 30_000).toISOString(),
    status: 'online',
    active_lease: { ticket_id: 'T-012', job_id: 'job-8842' },
  },
  {
    id: 'runner-d4e5f6',
    workspace_id: 'default',
    label: 'ci-fallback',
    created_at: '2026-06-22T12:00:00Z',
    last_heartbeat_at: new Date(Date.now() - 600_000).toISOString(),
    status: 'offline',
    active_lease: null,
  },
  {
    id: 'runner-revoked',
    workspace_id: 'default',
    label: 'old-laptop',
    created_at: '2026-06-10T09:00:00Z',
    revoked_at: '2026-06-25T16:00:00Z',
    last_heartbeat_at: '2026-06-25T15:00:00Z',
    status: 'revoked',
    active_lease: null,
  },
];

export const MOCK_GIT_APP_INSTALL = {
  github: {
    provider: 'github' as const,
    install_url: 'https://github.com/apps/haao-orchestrator/installations/new',
    installed: true,
    credential_id: 'github-app-default',
    label: 'HAAO GitHub App',
  },
  gitlab: {
    provider: 'gitlab' as const,
    install_url: 'https://gitlab.com/oauth/authorize?client_id=haao-app',
    installed: false,
  },
};

export function mockIntegrationsWithApp(): IntegrationCredential[] {
  return [
    {
      provider: 'github',
      id: 'github-pat-1',
      label: 'Personal token',
      scopes: ['repo'],
      configured: true,
      created_at: '2026-06-01T00:00:00Z',
      updated_at: '2026-06-01T00:00:00Z',
      credential_type: 'pat',
    },
    {
      provider: 'github',
      id: 'github-app-default',
      label: 'HAAO GitHub App',
      scopes: ['contents', 'pull_requests'],
      configured: true,
      created_at: '2026-06-28T10:00:00Z',
      updated_at: '2026-06-28T10:00:00Z',
      credential_type: 'app',
    },
  ];
}

export function memberDisplayName(member: WorkspaceMembership): string {
  return member.display_name?.trim()
    || member.email?.trim()
    || member.user_id;
}
