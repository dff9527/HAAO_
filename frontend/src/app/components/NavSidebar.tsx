import { LayoutGrid, Cpu, FileText } from 'lucide-react';

export type Page = 'board' | 'requirements' | 'models';

interface Props {
  currentPage: Page;
  onNavigate: (page: Page) => void;
}

const NAV_ITEMS: { page: Page; label: string; title: string; icon: typeof LayoutGrid }[] = [
  { page: 'board', label: 'Kanban board', title: 'Board', icon: LayoutGrid },
  { page: 'requirements', label: 'Requirements', title: 'Requirements', icon: FileText },
  { page: 'models', label: 'Setup — models & connections', title: 'Setup', icon: Cpu },
];

export function NavSidebar({ currentPage, onNavigate }: Props) {
  return (
    <nav className="w-11 shrink-0 border-r border-border bg-card flex flex-col items-center pt-2 gap-1" aria-label="Main navigation">
      {NAV_ITEMS.map(({ page, label, title, icon: Icon }) => (
        <button
          key={page}
          type="button"
          onClick={() => onNavigate(page)}
          title={title}
          className={`w-8 h-8 flex items-center justify-center rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
            currentPage === page
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:bg-muted hover:text-foreground'
          }`}
          aria-label={label}
          aria-current={currentPage === page ? 'page' : undefined}
        >
          <Icon size={15} />
        </button>
      ))}
    </nav>
  );
}
