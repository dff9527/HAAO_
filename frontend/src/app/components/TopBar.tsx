import { Moon, Sun } from 'lucide-react';
import type { Project } from '../types';
import { AddWorkMenu } from './AddWorkMenu';
import { ProjectSwitcher } from './ProjectSwitcher';
import brandLogoUrl from '@/assets/frame.png';
import brandLogoLiteUrl from '@/assets/frame-lite.png';

interface Props {
  darkMode: boolean;
  onToggleDark: () => void;
  onNewReq: () => void;
  onNewTicket: () => void;
  showBoardControls: boolean;
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
    },
  ) => Promise<void>;
}

export function TopBar({
  darkMode,
  onToggleDark,
  onNewReq,
  onNewTicket,
  showBoardControls,
  boardLive = false,
  projects,
  selectedProjectId,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  onUpdateProjectSettings,
}: Props) {
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
        <button
          type="button"
          onClick={onToggleDark}
          className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {darkMode ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        {showBoardControls && (
          <AddWorkMenu onNewRequirement={onNewReq} onNewTicket={onNewTicket} />
        )}
      </div>
    </header>
  );
}
