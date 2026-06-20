import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Plus, Sparkles, TicketPlus } from 'lucide-react';

interface Props {
  onNewRequirement: () => void;
  onNewTicket: () => void;
}

export function AddWorkMenu({ onNewRequirement, onNewTicket }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }

    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }

    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('mousedown', onPointerDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('mousedown', onPointerDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative flex items-center gap-1">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="hidden sm:flex h-7 items-center gap-1.5 px-2.5 rounded border border-border text-xs font-medium hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <Plus size={12} aria-hidden="true" />
        Add work
        <ChevronDown size={12} className="text-muted-foreground" aria-hidden="true" />
      </button>

      <button
        type="button"
        onClick={() => {
          setOpen(false);
          onNewRequirement();
        }}
        className="sm:hidden h-8 w-8 flex items-center justify-center rounded border border-border hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label="New requirement"
        title="New requirement"
      >
        <Sparkles size={14} className="text-amber-500" />
      </button>
      <button
        type="button"
        onClick={() => {
          setOpen(false);
          onNewTicket();
        }}
        className="sm:hidden h-8 w-8 flex items-center justify-center rounded border border-border hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label="Single ticket"
        title="Single ticket"
      >
        <TicketPlus size={14} className="text-muted-foreground" />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+4px)] z-50 w-72 rounded-lg border border-border bg-card p-1 shadow-xl"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onNewRequirement();
            }}
            className="w-full rounded-md px-3 py-2.5 text-left hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="flex items-start gap-2">
              <Sparkles size={14} className="text-amber-500 mt-0.5 shrink-0" />
              <div>
                <p className="text-xs font-medium text-foreground">New requirement</p>
                <p className="text-[11px] text-muted-foreground mt-0.5 leading-snug">
                  Describe a feature or change — Tech Lead decomposes it into tickets.
                </p>
              </div>
            </div>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onNewTicket();
            }}
            className="w-full rounded-md px-3 py-2.5 text-left hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="flex items-start gap-2">
              <TicketPlus size={14} className="text-muted-foreground mt-0.5 shrink-0" />
              <div>
                <p className="text-xs font-medium text-foreground">Single ticket</p>
                <p className="text-[11px] text-muted-foreground mt-0.5 leading-snug">
                  Add one scoped task directly to the backlog.
                </p>
              </div>
            </div>
          </button>
        </div>
      )}
    </div>
  );
}
