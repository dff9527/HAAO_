import type { Ticket } from './types';

export function needsMyAttention(ticket: Ticket): boolean {
  if (ticket.needsApproval && ticket.status === 'Backlog') return true;
  if (ticket.status === 'Diff review') return true;
  if (ticket.status === 'Awaiting acceptance') return true;
  return false;
}

export function countNeedsAttention(tickets: Ticket[]): number {
  return tickets.filter(needsMyAttention).length;
}

export function boardHasActiveWork(tickets: Ticket[]): boolean {
  return tickets.some(
    (ticket) =>
      ['Ready', 'In Progress', 'Diff review', 'Review', 'Blocked'].includes(ticket.status)
      || ticket.testStatus === 'testing',
  );
}

export function sortReviewColumnTickets(tickets: Ticket[]): Ticket[] {
  return [...tickets].sort((left, right) => {
    const leftDiff = left.status === 'Diff review' ? 0 : 1;
    const rightDiff = right.status === 'Diff review' ? 0 : 1;
    if (leftDiff !== rightDiff) return leftDiff - rightDiff;
    return left.id.localeCompare(right.id);
  });
}
