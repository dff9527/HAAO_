const USER_ID_KEY = 'haao_user_id';
const WORKSPACE_ID_KEY = 'haao_workspace_id';

export function getStoredUserId(): string {
  try {
    return localStorage.getItem(USER_ID_KEY) ?? '';
  } catch {
    return '';
  }
}

export function getStoredWorkspaceId(): string {
  try {
    return localStorage.getItem(WORKSPACE_ID_KEY) ?? 'default';
  } catch {
    return 'default';
  }
}

export function setStoredIdentity(userId: string, workspaceId: string): void {
  try {
    const trimmedUser = userId.trim();
    const trimmedWorkspace = workspaceId.trim() || 'default';
    if (trimmedUser) {
      localStorage.setItem(USER_ID_KEY, trimmedUser);
    } else {
      localStorage.removeItem(USER_ID_KEY);
    }
    localStorage.setItem(WORKSPACE_ID_KEY, trimmedWorkspace);
  } catch {
    // Ignore storage failures.
  }
}

export function identityHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const userId = getStoredUserId();
  const workspaceId = getStoredWorkspaceId();
  if (userId) {
    headers['x-haao-user-id'] = userId;
  }
  if (workspaceId) {
    headers['x-haao-workspace-id'] = workspaceId;
  }
  return headers;
}
