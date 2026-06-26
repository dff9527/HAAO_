import { Home, Cpu, FileText, Activity, BarChart3, Inbox } from 'lucide-react';

export type Page = 'home' | 'activity' | 'insights' | 'inbox' | 'requirements' | 'models';

interface Props {
  currentPage: Page;
  inboxUnreadCount?: number;
  onNavigate: (page: Page) => void;
}

const NAV_ITEMS: { page: Page; label: string; title: string; icon: typeof Home }[] = [
  { page: 'home', label: 'Home — chat and board', title: 'Home', icon: Home },
  { page: 'activity', label: 'Activity — live run stream', title: 'Activity', icon: Activity },
  { page: 'insights', label: 'Insights — metrics and scorecards', title: 'Insights', icon: BarChart3 },
  { page: 'inbox', label: 'Inbox — notifications', title: 'Inbox', icon: Inbox },
  { page: 'requirements', label: 'History — requirements ledger', title: 'History', icon: FileText },
  { page: 'models', label: 'Settings — models and connections', title: 'Settings', icon: Cpu },
];

export function NavSidebar({ currentPage, inboxUnreadCount = 0, onNavigate }: Props) {
  return (
    <nav className="w-11 shrink-0 border-r border-border bg-card flex flex-col items-center pt-2 gap-1" aria-label="Main navigation">
      {NAV_ITEMS.map(({ page, label, title, icon: Icon }) => (
        <button
          key={page}
          type="button"
          onClick={() => onNavigate(page)}
          title={title}
          className={`relative w-8 h-8 flex items-center justify-center rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
            currentPage === page
              ? 'bg-primary text-primary-foreground'
              : 'text-muted-foreground hover:bg-muted hover:text-foreground'
          }`}
          aria-label={label}
          aria-current={currentPage === page ? 'page' : undefined}
        >
          <Icon size={15} />
          {page === 'inbox' && inboxUnreadCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 min-w-[14px] h-3.5 px-0.5 rounded-full bg-red-500 text-white text-[9px] font-semibold leading-none flex items-center justify-center">
              {inboxUnreadCount > 99 ? '99+' : inboxUnreadCount}
            </span>
          )}
        </button>
      ))}
    </nav>
  );
}
