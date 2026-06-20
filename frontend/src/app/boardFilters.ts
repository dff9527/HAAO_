import { needsMyAttention } from './ticketAttention';
import type { Ticket } from './types';

export type BoardTypeFilter = Ticket['type'] | 'all';
export type BoardPriorityFilter = Ticket['priority'] | 'all';

export interface BoardFilters {
  query: string;
  type: BoardTypeFilter;
  priority: BoardPriorityFilter;
}

export const EMPTY_BOARD_FILTERS: BoardFilters = {
  query: '',
  type: 'all',
  priority: 'all',
};

export function matchesBoardFilters(
  ticket: Ticket,
  filters: BoardFilters,
  attentionOnly: boolean,
): boolean {
  if (attentionOnly && !needsMyAttention(ticket)) return false;
  if (filters.type !== 'all' && ticket.type !== filters.type) return false;
  if (filters.priority !== 'all' && ticket.priority !== filters.priority) return false;

  const query = filters.query.trim().toLowerCase();
  if (!query) return true;

  return (
    ticket.id.toLowerCase().includes(query)
    || ticket.title.toLowerCase().includes(query)
    || ticket.contextFiles.some((file) => file.path.toLowerCase().includes(query))
  );
}

export function hasActiveBoardFilters(filters: BoardFilters): boolean {
  return Boolean(filters.query.trim()) || filters.type !== 'all' || filters.priority !== 'all';
}
