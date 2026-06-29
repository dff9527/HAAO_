import { KanbanColumn } from './KanbanColumn';
import { COLUMNS, isTerminalTicketStatus } from '../constants';
import { matchesBoardFilters, type BoardFilters } from '../boardFilters';
import { sortReviewColumnTickets } from '../ticketAttention';
import type { Ticket, TicketStatus } from '../types';

interface Props {
  tickets: Ticket[];
  loading?: boolean;
  selectedTicketId: string | null;
  attentionFilter?: boolean;
  boardFilters?: BoardFilters;
  onMoveTicket: (ticketId: string, newStatus: TicketStatus) => void;
  onSelectTicket: (id: string) => void;
  onApproveTicket: (id: string) => void;
  onAcceptTicket: (id: string) => void;
}

export function KanbanBoard({
  tickets,
  loading = false,
  selectedTicketId,
  attentionFilter = false,
  boardFilters,
  onMoveTicket,
  onSelectTicket,
  onApproveTicket,
  onAcceptTicket,
}: Props) {
  const filters = boardFilters ?? { query: '', type: 'all', priority: 'all', terminal: 'all' };
  const visibleTickets = tickets.filter((ticket) => matchesBoardFilters(ticket, filters, attentionFilter));

  function getColumnTickets(columnStatus: TicketStatus): Ticket[] {
    if (columnStatus === 'Review') {
      return sortReviewColumnTickets(
        visibleTickets.filter((ticket) => ticket.status === 'Review' || ticket.status === 'Diff review'),
      );
    }
    if (columnStatus === 'In Progress') {
      return visibleTickets.filter((ticket) => ticket.status === 'In Progress' || ticket.status === 'Blocked');
    }
    if (columnStatus === 'Done') {
      return visibleTickets.filter((ticket) => isTerminalTicketStatus(ticket.status));
    }
    return visibleTickets.filter((ticket) => ticket.status === columnStatus);
  }

  return (
    <div className="flex-1 min-h-0 overflow-x-auto overflow-y-hidden px-4 py-4">
      {attentionFilter && visibleTickets.length === 0 && !loading && (
        <div className="mb-3 rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted-foreground text-center">
          Nothing needs your attention right now.
        </div>
      )}
      {!attentionFilter && visibleTickets.length === 0 && !loading && tickets.length > 0 && (
        <div className="mb-3 rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted-foreground text-center">
          No tickets match your search or filters.
        </div>
      )}
      <div className="flex gap-3 h-full min-h-0 items-start">
        {COLUMNS.map((status) => (
          <KanbanColumn
            key={status}
            status={status}
            tickets={loading ? [] : getColumnTickets(status)}
            loading={loading}
            selectedTicketId={selectedTicketId}
            onDropTicket={onMoveTicket}
            onSelectTicket={onSelectTicket}
            onApproveTicket={onApproveTicket}
            onAcceptTicket={onAcceptTicket}
          />
        ))}
      </div>
    </div>
  );
}
