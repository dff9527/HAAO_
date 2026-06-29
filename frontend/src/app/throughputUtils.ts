import type { WorkerSlotStatus, TicketGraphEdge, TicketGraphNode, TicketGraphPayload } from './api/types';
import type { AutoWorkerStatus } from './api/client';
import type { TicketGraphEdge, TicketGraphNode, TicketGraphPayload } from './api/types';
import type { Ticket, TicketLease } from './types';

export const MOCK_WORKER_STATUS: AutoWorkerStatus = {
  running: true,
  interval_sec: 5,
  max_cycles_per_tick: 10,
  max_workers: 3,
  allow_dirty_workspace: false,
  last_started_at: new Date(Date.now() - 600_000).toISOString(),
  last_run_at: new Date(Date.now() - 8_000).toISOString(),
  last_error: '',
  last_skipped_reason: '',
  project_id: 'default',
  worker_statuses: [
    { worker_id: 'worker-1', running: true, last_run_at: new Date(Date.now() - 8_000).toISOString(), last_error: '', last_skipped_reason: '' },
    { worker_id: 'worker-2', running: true, last_run_at: new Date(Date.now() - 12_000).toISOString(), last_error: '', last_skipped_reason: '' },
    { worker_id: 'worker-3', running: true, last_run_at: new Date(Date.now() - 30_000).toISOString(), last_error: '', last_skipped_reason: 'target_file_conflict' },
  ],
};

export function workerDisplayNumber(workerId: string): string {
  const match = workerId.match(/(\d+)\s*$/);
  return match ? match[1] : workerId.replace(/^worker-?/i, '');
}

export function leaseByWorkerId(tickets: Ticket[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const ticket of tickets) {
    const workerId = ticket.lease?.workerId;
    if (workerId) map.set(workerId, ticket.id);
  }
  return map;
}

export function enrichWorkerSlots(status: AutoWorkerStatus, tickets: Ticket[]): WorkerSlotStatus[] {
  const leases = leaseByWorkerId(tickets);
  const slots = status.worker_statuses ?? [];
  if (slots.length > 0) {
    return slots.map((slot) => ({
      ...slot,
      ticket_id: leases.get(slot.worker_id) ?? slot.ticket_id ?? null,
    }));
  }
  const max = status.max_workers ?? 1;
  return Array.from({ length: max }, (_, index) => {
    const worker_id = `worker-${index + 1}`;
    return {
      worker_id,
      running: status.running,
      last_run_at: status.last_run_at,
      last_error: status.last_error,
      last_skipped_reason: status.last_skipped_reason,
      ticket_id: leases.get(worker_id) ?? null,
    };
  });
}

export function activeWorkerCount(slots: WorkerSlotStatus[]): number {
  return slots.filter((slot) => slot.ticket_id).length;
}

export function deriveReadyBlocked(graph: Pick<TicketGraphPayload, 'nodes'>): { ready: string[]; blocked: string[] } {
  const ready: string[] = [];
  const blocked: string[] = [];
  for (const node of graph.nodes) {
    if (node.ready_state === 'ready') ready.push(node.id);
    else if (node.ready_state === 'waiting_dependencies' || node.ready_state === 'conflict') blocked.push(node.id);
  }
  return { ready, blocked };
}

export function formatConflictNote(
  ticketId: string,
  node: TicketGraphNode | undefined,
  nodesById: Record<string, TicketGraphNode>,
  ticketTitles: Record<string, string>,
): string | undefined {
  if (!node || node.ready_state !== 'conflict') return undefined;
  for (const other of Object.values(nodesById)) {
    if (other.id === ticketId || !other.leased) continue;
    const overlap = node.target_files.some((file) => other.target_files.includes(file));
    if (overlap) {
      return `Waiting — overlaps files with ${other.id}${ticketTitles[other.id] ? ` (${ticketTitles[other.id]})` : ''}`;
    }
  }
  return 'Waiting — file overlap with another running ticket';
}

export function formatConflictEventMessage(payload: Record<string, unknown>): string {
  const ids = Array.isArray(payload.conflicting_ticket_ids)
    ? payload.conflicting_ticket_ids.filter((item): item is string => typeof item === 'string')
    : [];
  const kind = typeof payload.kind === 'string' ? payload.kind : '';
  if (kind === 'merge' || String(payload.detail ?? '').toLowerCase().includes('merge')) {
    return 'Merge conflict — needs attention';
  }
  if (ids.length > 0) {
    return `Waiting — overlaps files with ${ids.map((id) => `#${id}`).join(', ')}`;
  }
  const detail = typeof payload.detail === 'string' ? payload.detail : '';
  return detail || 'Blocked by file overlap with another ticket';
}

export function parseTicketLease(raw: unknown): TicketLease | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const record = raw as Record<string, unknown>;
  const workerId = record.worker_id;
  if (typeof workerId !== 'string' || !workerId) return undefined;
  return {
    workerId,
    expiresAt: typeof record.expires_at === 'string' ? record.expires_at : undefined,
    heartbeatAt: typeof record.heartbeat_at === 'string' ? record.heartbeat_at : undefined,
  };
}

export function mergeGraphIntoTickets(tickets: Ticket[], graph: TicketGraphPayload): Ticket[] {
  const nodesById = Object.fromEntries(graph.nodes.map((node) => [node.id, node]));
  const titles = Object.fromEntries(tickets.map((ticket) => [ticket.id, ticket.title]));
  const { ready, blocked } = graph.ready?.length || graph.blocked?.length
    ? { ready: graph.ready ?? [], blocked: graph.blocked ?? [] }
    : deriveReadyBlocked(graph);

  return tickets.map((ticket) => {
    const node = nodesById[ticket.id];
    if (!node) return ticket;
    const dependsOn = node.depends_on?.length ? node.depends_on : ticket.dependsOn;
    const lease = node.leased && node.lease ? parseTicketLease(node.lease) : ticket.lease;
    const conflictNote = formatConflictNote(ticket.id, node, nodesById, titles);
    return {
      ...ticket,
      dependsOn,
      lease,
      readyState: node.ready_state,
      conflictNote: conflictNote ?? ticket.conflictNote,
      graphReady: ready.includes(ticket.id),
      graphBlocked: blocked.includes(ticket.id),
    };
  });
}

export function buildMockGraph(tickets: Ticket[]): TicketGraphPayload {
  const nodes: TicketGraphNode[] = tickets.map((ticket) => ({
    id: ticket.id,
    status: ticket.status.toLowerCase().replace(/ /g, '_'),
    depends_on: ticket.dependsOn ?? [],
    target_files: ticket.contextFiles.map((file) => file.path),
    ready_state: ticket.readyState ?? (ticket.status === 'Ready' ? 'ready' : 'not_ready'),
    leased: Boolean(ticket.lease),
    lease: ticket.lease
      ? {
          worker_id: ticket.lease.workerId,
          expires_at: ticket.lease.expiresAt,
          heartbeat_at: ticket.lease.heartbeatAt,
        }
      : null,
  }));
  const edges: TicketGraphEdge[] = [];
  for (const node of nodes) {
    for (const source of node.depends_on) {
      edges.push({ source, target: node.id, kind: 'depends_on' });
    }
  }
  const derived = deriveReadyBlocked({ nodes });
  return {
    project_id: 'default',
    nodes,
    edges,
    ready: derived.ready,
    blocked: derived.blocked,
  };
}
