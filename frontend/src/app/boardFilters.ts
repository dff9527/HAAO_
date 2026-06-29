import { needsMyAttention } from './ticketAttention';
import { isTerminalTicketStatus } from './constants';
import type { Ticket } from './types';

export type BoardTypeFilter = Ticket['type'] | 'all';
export type BoardPriorityFilter = Ticket['priority'] | 'all';
export type BoardTerminalFilter = 'all' | 'active' | 'done' | 'abandoned' | 'split';

export interface BoardFilters {
  query: string;
  type: BoardTypeFilter;
  priority: BoardPriorityFilter;
  terminal: BoardTerminalFilter;
}

export const EMPTY_BOARD_FILTERS: BoardFilters = {
  query: '',
  type: 'all',
  priority: 'all',
  terminal: 'all',
};

export function matchesBoardFilters(
  ticket: Ticket,
  filters: BoardFilters,
  attentionOnly: boolean,
): boolean {
  if (attentionOnly && !needsMyAttention(ticket)) return false;
  if (filters.type !== 'all' && ticket.type !== filters.type) return false;
  if (filters.priority !== 'all' && ticket.priority !== filters.priority) return false;

  if (filters.terminal === 'active' && isTerminalTicketStatus(ticket.status)) return false;
  if (filters.terminal === 'done' && ticket.status !== 'Done') return false;
  if (filters.terminal === 'abandoned' && ticket.status !== 'Abandoned') return false;
  if (filters.terminal === 'split' && ticket.status !== 'Split') return false;

  const query = filters.query.trim().toLowerCase();
  if (!query) return true;

  return (
    ticket.id.toLowerCase().includes(query)
    || ticket.title.toLowerCase().includes(query)
    || ticket.contextFiles.some((file) => file.path.toLowerCase().includes(query))
  );
}

export function hasActiveBoardFilters(filters: BoardFilters): boolean {
  return Boolean(filters.query.trim())
    || filters.type !== 'all'
    || filters.priority !== 'all'
    || filters.terminal !== 'all';
}
