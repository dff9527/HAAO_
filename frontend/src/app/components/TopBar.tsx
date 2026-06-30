import { Moon, Sun, HelpCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { Project } from '../types';
import { ProjectSwitcher } from './ProjectSwitcher';
import brandLogoUrl from '@/assets/frame.png';
import brandLogoLiteUrl from '@/assets/frame-lite.png';

interface Props {
  darkMode: boolean;
  onToggleDark: () => void;
  boardLive?: boolean;
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
  onOpenSetupWizard?: () => void;
  oidcConfigured?: boolean;
  ssoLoading?: boolean;
  ssoError?: string | null;
  authSummary?: {
    actorId: string;
    workspaceId: string;
    role: string;
  } | null;
  onSignInSso?: () => void;
  onSignOut?: () => void;
}

export function TopBar({
  darkMode,
  onToggleDark,
  boardLive = false,
  projects,
  selectedProjectId,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  onUpdateProjectSettings,
  onOpenSetupWizard,
  oidcConfigured = false,
  ssoLoading = false,
  ssoError = null,
  authSummary = null,
  onSignInSso,
  onSignOut,
}: Props) {
  const [helpOpen, setHelpOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const helpRef = useRef<HTMLDivElement>(null);
  const accountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!helpOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (!helpRef.current?.contains(event.target as Node)) setHelpOpen(false);
    }
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [helpOpen]);

  useEffect(() => {
    if (!accountOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (!accountRef.current?.contains(event.target as Node)) setAccountOpen(false);
    }
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [accountOpen]);

  return (
    <header className="relative h-14 flex items-center justify-between px-3 sm:px-4 border-b border-border bg-card shrink-0 gap-2 min-w-0">
      <div className="flex items-center gap-2 min-w-0 z-10 max-w-[42%] sm:max-w-[45%]">
        <ProjectSwitcher
          projects={projects}
          selectedProjectId={selectedProjectId}
          onSelectProject={onSelectProject}
          onCreateProject={onCreateProject}
          onDeleteProject={onDeleteProject}
          onUpdateProjectSettings={onUpdateProjectSettings}
        />
      </div>

      <div
        className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center gap-2 pointer-events-none select-none px-2"
        aria-label="HAAO — Hybrid AI-Agile Orchestrator"
      >
        <img
          src={darkMode ? brandLogoUrl : brandLogoLiteUrl}
          alt="HAAO"
          className="h-9 sm:h-10 w-auto object-contain shrink-0 max-w-[5.5rem] sm:max-w-none"
          width={88}
          height={44}
        />
        <p className="hidden xl:block text-sm leading-tight text-muted-foreground font-medium whitespace-nowrap">
          Hybrid AI-Agile Orchestrator
        </p>
      </div>

      <div className="flex items-center gap-1.5 shrink-0 z-10">
        {onOpenSetupWizard && (
          <div ref={helpRef} className="relative">
            <button
              type="button"
              onClick={() => setHelpOpen((value) => !value)}
              className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Help and setup"
              aria-expanded={helpOpen}
            >
              <HelpCircle size={14} />
            </button>
            {helpOpen && (
              <div className="absolute right-0 top-[calc(100%+4px)] z-50 w-52 rounded-lg border border-border bg-card p-1 shadow-xl">
                <button
                  type="button"
                  onClick={() => {
                    setHelpOpen(false);
                    onOpenSetupWizard();
                  }}
                  className="w-full rounded-md px-3 py-2 text-left text-xs hover:bg-muted transition-colors"
                >
                  Setup wizard
                </button>
              </div>
            )}
          </div>
        )}
        <button
          type="button"
          onClick={onToggleDark}
          className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {darkMode ? <Sun size={14} /> : <Moon size={14} />}
        </button>
        {oidcConfigured && (
          <div ref={accountRef} className="relative">
            <button
              type="button"
              onClick={() => setAccountOpen((value) => !value)}
              className="h-7 rounded px-2 text-[11px] border border-border text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Account and SSO"
              aria-expanded={accountOpen}
            >
              {authSummary ? authSummary.actorId : 'Sign in with SSO'}
            </button>
            {accountOpen && (
              <div className="absolute right-0 top-[calc(100%+4px)] z-50 w-64 rounded-lg border border-border bg-card p-2 shadow-xl text-[11px] space-y-2">
                {authSummary ? (
                  <>
                    <div className="px-1 text-muted-foreground">
                      <p className="text-foreground">{authSummary.actorId}</p>
                      <p>Workspace: {authSummary.workspaceId}</p>
                      <p>Role: {authSummary.role}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setAccountOpen(false);
                        onSignOut?.();
                      }}
                      disabled={ssoLoading}
                      className="w-full rounded-md border border-border px-2.5 py-1.5 text-left hover:bg-muted transition-colors disabled:opacity-50"
                    >
                      {ssoLoading ? 'Signing out…' : 'Sign out'}
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setAccountOpen(false);
                      onSignInSso?.();
                    }}
                    disabled={ssoLoading}
                    className="w-full rounded-md border border-border px-2.5 py-1.5 text-left hover:bg-muted transition-colors disabled:opacity-50"
                  >
                    {ssoLoading ? 'Redirecting…' : 'Sign in with SSO'}
                  </button>
                )}
                {ssoError && <p className="px-1 text-red-600 dark:text-red-400">{ssoError}</p>}
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
