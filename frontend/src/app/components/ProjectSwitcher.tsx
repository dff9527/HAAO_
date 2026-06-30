import { useEffect, useRef, useState } from 'react';
import { FolderGit2, Plus, Settings, Trash2, X } from 'lucide-react';
import type { Project } from '../types';

interface Props {
  projects: Project[];
  selectedProjectId: string;
  onSelectProject: (projectId: string) => void;
  onCreateProject: (payload: { name: string; path: string }) => Promise<void>;
  onDeleteProject: (projectId: string) => Promise<void>;
  onUpdateProjectSettings: (
    projectId: string,
    payload: {
      env: Record<string, string>;
      setupCmd: string;
      cleanupCmd: string;
      defaultBranch: string;
      sandboxMode: 'auto' | 'strict' | 'docker' | 'unshare' | 'none';
    },
  ) => Promise<void>;
}

export function ProjectSwitcher({
  projects,
  selectedProjectId,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  onUpdateProjectSettings,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [isAdding, setIsAdding] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [name, setName] = useState('');
  const [path, setPath] = useState('');
  const [defaultBranch, setDefaultBranch] = useState('');
  const [envText, setEnvText] = useState('');
  const [setupCmd, setSetupCmd] = useState('');
  const [cleanupCmd, setCleanupCmd] = useState('');
  const [sandboxMode, setSandboxMode] = useState<'auto' | 'strict' | 'docker' | 'unshare' | 'none'>('auto');
  const [error, setError] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const selectedProject = projects.find((project) => project.id === selectedProjectId);
  const panelOpen = isAdding || isEditing;

  function loadSelectedProjectFields() {
    if (!selectedProject) return;
    setDefaultBranch(selectedProject.defaultBranch);
    setEnvText(formatEnv(selectedProject.env));
    setSetupCmd(selectedProject.setupCmd);
    setCleanupCmd(selectedProject.cleanupCmd);
    setSandboxMode(selectedProject.sandboxMode);
  }

  function closePanels() {
    setIsAdding(false);
    setIsEditing(false);
    setConfirmDelete(false);
    setError('');
  }

  function openSettings() {
    if (!selectedProject) return;
    setError('');
    loadSelectedProjectFields();
    setConfirmDelete(false);
    setIsAdding(false);
    setIsEditing((value) => !value);
  }

  function openAdd() {
    setError('');
    setConfirmDelete(false);
    setIsEditing(false);
    setIsAdding((value) => !value);
  }

  useEffect(() => {
    if (!panelOpen) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') closePanels();
    }

    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) closePanels();
    }

    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('mousedown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('mousedown', onPointerDown);
    };
  }, [panelOpen]);

  async function submit() {
    if (!name.trim() || !path.trim()) return;
    setError('');
    setIsSaving(true);
    try {
      await onCreateProject({ name: name.trim(), path: path.trim() });
      setName('');
      setPath('');
      setIsAdding(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not add project');
    } finally {
      setIsSaving(false);
    }
  }

  async function saveSettings() {
    if (!selectedProject) return;
    setError('');
    setIsSaving(true);
    try {
      await onUpdateProjectSettings(selectedProject.id, {
        env: parseEnv(envText),
        setupCmd: setupCmd.trim(),
        cleanupCmd: cleanupCmd.trim(),
        defaultBranch: defaultBranch.trim() || selectedProject.defaultBranch,
        sandboxMode,
      });
      setIsEditing(false);
      setConfirmDelete(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not save project settings');
    } finally {
      setIsSaving(false);
    }
  }

  async function deleteProject() {
    if (!selectedProject) return;
    setError('');
    setIsSaving(true);
    try {
      await onDeleteProject(selectedProject.id);
      closePanels();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not delete project');
    } finally {
      setIsSaving(false);
    }
  }

  const panelClassName = 'absolute left-0 top-9 z-40 w-[min(440px,calc(100vw-2rem))] max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-card p-3 shadow-xl space-y-3';

  return (
    <div ref={rootRef} className="relative flex items-center gap-1.5">
      <FolderGit2 size={15} className="text-muted-foreground shrink-0" aria-hidden="true" />
      <select
        value={selectedProjectId}
        onChange={(event) => {
          setConfirmDelete(false);
          setError('');
          closePanels();
          onSelectProject(event.target.value);
        }}
        className="h-7 max-w-[min(220px,40vw)] rounded border border-border bg-background px-2 text-xs font-medium outline-none focus:ring-1 focus:ring-ring"
        aria-label="Select project"
      >
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={openAdd}
        className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={isAdding ? 'Close add project panel' : 'Add project'}
        aria-expanded={isAdding}
      >
        {isAdding ? <X size={13} /> : <Plus size={13} />}
      </button>
      <button
        type="button"
        onClick={openSettings}
        className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={isEditing ? 'Close project settings' : 'Project settings'}
        aria-expanded={isEditing}
      >
        {isEditing ? <X size={13} /> : <Settings size={13} />}
      </button>

      {isAdding && (
        <div className={panelClassName} role="dialog" aria-label="Add project">
          <div>
            <div className="text-xs font-semibold text-foreground">Add project</div>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Connect a local repo that HAAO can read and run tickets against.
            </p>
          </div>
          <label className="block space-y-1">
            <span className="text-[11px] font-medium text-muted-foreground">Project name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="my-project"
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-ring"
            />
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-medium text-muted-foreground">Repository path</span>
            <input
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder="/path/to/your/git/repo"
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs font-mono outline-none focus:ring-1 focus:ring-ring"
            />
            <span className="block text-[11px] leading-relaxed text-muted-foreground">
              Local path to an existing git repository root.
            </span>
          </label>
          {error && <div className="text-[11px] text-red-600 dark:text-red-400">{error}</div>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setIsAdding(false)} className="rounded border border-border px-2 py-1 text-xs hover:bg-muted">
              Cancel
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={!name.trim() || !path.trim() || isSaving}
              className="rounded bg-primary px-2.5 py-1 text-xs text-primary-foreground disabled:opacity-40"
            >
              {isSaving ? 'Adding...' : 'Add'}
            </button>
          </div>
        </div>
      )}

      {isEditing && selectedProject && (
        <div className={panelClassName} role="dialog" aria-label="Project settings">
          <div>
            <div className="text-xs font-semibold text-foreground">Project settings</div>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {selectedProject.name} · Used when HAAO creates worktrees and runs acceptance tests.
            </p>
          </div>
          <label className="block space-y-1">
            <span className="text-[11px] font-medium text-muted-foreground">Default branch</span>
            <input
              value={defaultBranch}
              onChange={(event) => setDefaultBranch(event.target.value)}
              placeholder="main"
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs font-mono outline-none focus:ring-1 focus:ring-ring"
            />
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-medium text-muted-foreground">Test environment variables</span>
            <textarea
              value={envText}
              onChange={(event) => setEnvText(event.target.value)}
              placeholder={'KEY=value\nANOTHER_KEY=value'}
              rows={4}
              className="w-full resize-none rounded border border-border bg-background px-2 py-1.5 text-xs font-mono outline-none focus:ring-1 focus:ring-ring"
            />
            <span className="block text-[11px] leading-relaxed text-muted-foreground">
              Optional. One KEY=value per line; passed to setup, tests, and cleanup.
            </span>
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-medium text-muted-foreground">Sandbox tier</span>
            <select
              value={sandboxMode}
              onChange={(event) => setSandboxMode(event.target.value as 'auto' | 'strict' | 'docker' | 'unshare' | 'none')}
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs font-mono outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="auto">auto</option>
              <option value="strict">strict</option>
              <option value="docker">docker</option>
              <option value="unshare">unshare</option>
              <option value="none">none</option>
            </select>
            <span className="block text-[11px] leading-relaxed text-muted-foreground">
              `strict` prefers hard isolation and degrades loudly if unavailable.
            </span>
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-medium text-muted-foreground">Setup command</span>
            <input
              value={setupCmd}
              onChange={(event) => setSetupCmd(event.target.value)}
              placeholder="npm install"
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs font-mono outline-none focus:ring-1 focus:ring-ring"
            />
            <span className="block text-[11px] leading-relaxed text-muted-foreground">
              Optional. Runs before each ticket&apos;s tests inside the project worktree.
            </span>
          </label>
          <label className="block space-y-1">
            <span className="text-[11px] font-medium text-muted-foreground">Cleanup command</span>
            <input
              value={cleanupCmd}
              onChange={(event) => setCleanupCmd(event.target.value)}
              placeholder="npm run clean"
              className="w-full rounded border border-border bg-background px-2 py-1.5 text-xs font-mono outline-none focus:ring-1 focus:ring-ring"
            />
            <span className="block text-[11px] leading-relaxed text-muted-foreground">
              Optional. Runs after tests finish, even when tests fail.
            </span>
          </label>
          {error && <div className="text-[11px] text-red-600 dark:text-red-400">{error}</div>}
          <div className="flex items-center justify-between gap-2 border-t border-border pt-3">
            {selectedProject.id !== 'default' ? (
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                disabled={isSaving}
                className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-red-50 hover:text-red-700 disabled:opacity-40 dark:hover:bg-red-950 dark:hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Trash2 size={11} aria-hidden="true" />
                Delete project…
              </button>
            ) : (
              <span className="text-[11px] text-muted-foreground">Default project cannot be deleted.</span>
            )}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={closePanels} className="rounded border border-border px-2 py-1 text-xs hover:bg-muted">
                Cancel
              </button>
              <button
                type="button"
                onClick={saveSettings}
                disabled={isSaving}
                className="rounded bg-primary px-2.5 py-1 text-xs text-primary-foreground disabled:opacity-40"
              >
                {isSaving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
          {confirmDelete && (
            <div className="rounded border border-red-200 bg-red-50 p-2 text-[11px] leading-relaxed text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
              <p>
                Delete {selectedProject.name}? This removes this project and its HAAO tickets, requirements, and logs.
                Your git repo files are not deleted.
              </p>
              <div className="mt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="rounded border border-red-200 bg-card px-2 py-1 text-xs text-foreground hover:bg-muted dark:border-red-800"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={deleteProject}
                  disabled={isSaving}
                  className="rounded bg-red-600 px-2 py-1 text-xs text-white hover:bg-red-700 disabled:opacity-40"
                >
                  {isSaving ? 'Deleting...' : 'Delete project'}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatEnv(env: Record<string, string>): string {
  return Object.entries(env)
    .map(([key, value]) => `${key}=${value}`)
    .join('\n');
}

function parseEnv(value: string): Record<string, string> {
  return Object.fromEntries(
    value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const separator = line.indexOf('=');
        if (separator === -1) return [line, ''];
        return [line.slice(0, separator).trim(), line.slice(separator + 1).trim()];
      })
      .filter(([key]) => key),
  );
}
