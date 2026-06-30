import { useEffect, useMemo, useState } from 'react';
import { Loader2, Plus, Trash2, UserPlus } from 'lucide-react';
import { apiClient } from '../api/client';
import { ApiError } from '../api/client';
import type { IdentityContext, MembershipRole, WorkspaceMembership, WorkspaceUsage } from '../api/types';
import {
  MOCK_MEMBERSHIPS,
  canManageTeam,
  forbiddenMessage,
  isForbiddenError,
  memberDisplayName,
  mockTeamPlaneEnabled,
  roleBadgeClass,
  roleLabel,
} from '../teamPlaneUtils';

const FORM_INPUT_CLASS =
  'text-xs bg-muted border border-border rounded px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-ring text-foreground';
const FORM_INPUT_MONO_CLASS = `font-mono ${FORM_INPUT_CLASS}`;

const ROLE_OPTIONS: MembershipRole[] = ['owner', 'admin', 'member', 'viewer'];

interface Props {
  identityContext: IdentityContext;
  onForbidden?: (message: string) => void;
}

export function MembersPanel({ identityContext, onForbidden }: Props) {
  const canManage = canManageTeam(identityContext);
  const workspaceId = identityContext.workspace_id;
  const [members, setMembers] = useState<WorkspaceMembership[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [inviteUserId, setInviteUserId] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<MembershipRole>('member');
  const [message, setMessage] = useState('');
  const [usage, setUsage] = useState<WorkspaceUsage | null>(null);

  const sortedMembers = useMemo(
    () => [...members].sort((a, b) => a.created_at.localeCompare(b.created_at)),
    [members],
  );

  async function refreshUsage() {
    if (mockTeamPlaneEnabled()) {
      setUsage(null);
      return;
    }
    try {
      setUsage(await apiClient.getWorkspaceUsage(workspaceId));
    } catch {
      setUsage(null);
    }
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    const load = mockTeamPlaneEnabled()
      ? Promise.resolve(MOCK_MEMBERSHIPS)
      : apiClient.listMemberships(workspaceId);
    load
      .then((items) => {
        if (active) setMembers(items);
      })
      .catch(() => {
        if (active) setMembers(mockTeamPlaneEnabled() ? MOCK_MEMBERSHIPS : []);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [workspaceId]);

  useEffect(() => {
    let active = true;
    if (!mockTeamPlaneEnabled()) {
      apiClient
        .getWorkspaceUsage(workspaceId)
        .then((next) => {
          if (active) setUsage(next);
        })
        .catch(() => {
          if (active) setUsage(null);
        });
    } else {
      setUsage(null);
    }
    return () => {
      active = false;
    };
  }, [workspaceId]);

  async function handleInvite() {
    if (!inviteUserId.trim()) return;
    setSaving(true);
    setMessage('');
    try {
      const membership = mockTeamPlaneEnabled()
        ? {
            user_id: inviteUserId.trim(),
            workspace_id: workspaceId,
            role: inviteRole,
            created_at: new Date().toISOString(),
            email: inviteEmail.trim(),
            display_name: inviteEmail.trim() || inviteUserId.trim(),
          }
        : await apiClient.upsertMembership({
            user_id: inviteUserId.trim(),
            workspace_id: workspaceId,
            role: inviteRole,
            email: inviteEmail.trim(),
            display_name: inviteEmail.trim() || inviteUserId.trim(),
          });
      setMembers((prev) => {
        const without = prev.filter((item) => item.user_id !== membership.user_id);
        return [...without, membership];
      });
      setInviteUserId('');
      setInviteEmail('');
      setInviteRole('member');
      await refreshUsage();
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else if (error instanceof ApiError && (error.status === 402 || error.status === 409)) {
        setMessage('Seat limit reached — upgrade or free a seat before inviting another member.');
      } else {
        setMessage(error instanceof Error ? error.message : 'Could not save membership.');
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleRoleChange(userId: string, role: MembershipRole) {
    const existing = members.find((item) => item.user_id === userId);
    if (!existing || existing.role === role) return;
    setSaving(true);
    setMessage('');
    try {
      const membership = mockTeamPlaneEnabled()
        ? { ...existing, role }
        : await apiClient.upsertMembership({
            user_id: userId,
            workspace_id: workspaceId,
            role,
            email: existing.email,
            display_name: existing.display_name,
          });
      setMembers((prev) => prev.map((item) => (item.user_id === userId ? membership : item)));
      await refreshUsage();
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setMessage(error instanceof Error ? error.message : 'Could not update role.');
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(userId: string) {
    if (userId === identityContext.actor_id) {
      setMessage('You cannot remove yourself.');
      return;
    }
    setSaving(true);
    setMessage('');
    try {
      if (!mockTeamPlaneEnabled()) {
        await apiClient.removeMembership(workspaceId, userId);
      }
      setMembers((prev) => prev.filter((item) => item.user_id !== userId));
      await refreshUsage();
    } catch (error) {
      if (isForbiddenError(error)) {
        onForbidden?.(forbiddenMessage(error));
      } else {
        setMessage(error instanceof Error ? error.message : 'Could not remove member.');
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-3">
      {usage && (
        <div className="rounded border border-border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground flex flex-wrap items-center gap-1.5">
          <span className="font-medium text-foreground">Plan:</span>
          <span>{usage.plan}</span>
          <span>·</span>
          <span className="font-medium text-foreground">Seats:</span>
          <span>
            {usage.seats_used}
            {usage.seat_limit != null ? ` / ${usage.seat_limit}` : ' / unlimited'}
          </span>
          {usage.seat_limit != null && usage.seats_used >= usage.seat_limit && (
            <>
              <span>·</span>
              <span className="text-amber-700 dark:text-amber-300">Seat limit reached</span>
              <a
                href="https://haao.ai/early-access"
                target="_blank"
                rel="noreferrer"
                className="underline text-foreground hover:text-foreground/80"
              >
                Upgrade or contact early access
              </a>
            </>
          )}
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        Workspace members and roles. Owner and admin can invite or change roles; members and viewers are read-only here.
      </p>
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
          <Loader2 size={12} className="animate-spin" />
          Loading members…
        </div>
      ) : (
        <div className="rounded border border-border overflow-x-auto">
          <table className="w-full text-sm min-w-[480px]">
            <thead>
              <tr className="bg-muted/60 border-b border-border">
                <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2">Member</th>
                <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2">Role</th>
                <th className="text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2">Joined</th>
                {canManage && (
                  <th className="text-right text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 py-2 w-24">Actions</th>
                )}
              </tr>
            </thead>
            <tbody>
              {sortedMembers.map((member, index) => (
                <tr
                  key={member.user_id}
                  className={`border-b border-border last:border-0 ${index % 2 === 0 ? '' : 'bg-muted/20'}`}
                >
                  <td className="px-3 py-2.5 align-top">
                    <div className="text-xs font-medium text-foreground">{memberDisplayName(member)}</div>
                    <div className="font-mono text-[10px] text-muted-foreground">{member.user_id}</div>
                    {member.email && member.email !== memberDisplayName(member) && (
                      <div className="text-[10px] text-muted-foreground">{member.email}</div>
                    )}
                  </td>
                  <td className="px-3 py-2.5 align-top">
                    {canManage && member.user_id !== identityContext.actor_id ? (
                      <select
                        value={member.role}
                        disabled={saving}
                        onChange={(e) => void handleRoleChange(member.user_id, e.target.value as MembershipRole)}
                        className="text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                      >
                        {ROLE_OPTIONS.map((role) => (
                          <option key={role} value={role}>{roleLabel(role)}</option>
                        ))}
                      </select>
                    ) : (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${roleBadgeClass(member.role)}`}>
                        {roleLabel(member.role)}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 align-top text-[11px] text-muted-foreground">
                    {new Date(member.created_at).toLocaleDateString()}
                  </td>
                  {canManage && (
                    <td className="px-3 py-2.5 align-top text-right">
                      {member.user_id !== identityContext.actor_id && (
                        <button
                          type="button"
                          onClick={() => void handleRemove(member.user_id)}
                          disabled={saving}
                          className="h-7 w-7 inline-flex items-center justify-center rounded border border-border text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                          aria-label={`Remove ${memberDisplayName(member)}`}
                        >
                          <Trash2 size={12} />
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {canManage && (
        <div className="rounded border border-border bg-background px-3 py-3 space-y-2">
          <div className="flex items-center gap-1.5 text-xs font-medium text-foreground">
            <UserPlus size={12} />
            Invite member
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <input
              value={inviteUserId}
              onChange={(e) => setInviteUserId(e.target.value)}
              placeholder="User ID"
              className={FORM_INPUT_MONO_CLASS}
            />
            <input
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="Email (optional)"
              className={FORM_INPUT_CLASS}
            />
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value as MembershipRole)}
              className={FORM_INPUT_CLASS}
            >
              {ROLE_OPTIONS.filter((role) => role !== 'owner').map((role) => (
                <option key={role} value={role}>{roleLabel(role)}</option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void handleInvite()}
              disabled={saving || !inviteUserId.trim()}
              className="flex items-center justify-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-border hover:bg-muted transition-colors disabled:opacity-50"
            >
              {saving ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
              Add member
            </button>
          </div>
        </div>
      )}
      {!canManage && (
        <p className="text-[11px] text-muted-foreground">Your role ({roleLabel(identityContext.role)}) is read-only for member management.</p>
      )}
      {message && <p className="text-[11px] text-red-600 dark:text-red-400">{message}</p>}
    </div>
  );
}
