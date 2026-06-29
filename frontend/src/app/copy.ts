import type { TicketStatus } from './types';

export const CLAUDE_TECH_LEAD = 'Claude · Tech Lead';

export const CLOUD_ONLY_ROUTE_IDS = new Set(['tech_lead', 'escalation']);

export function formatAuditVerdict(verdict: string): string {
  const normalized = verdict.trim().toLowerCase();
  if (normalized === 'approved' || normalized === 'pass' || normalized === 'passed') {
    return 'Passed automated review';
  }
  if (normalized === 'rejected' || normalized === 'fail' || normalized === 'failed') {
    return 'Did not pass automated review';
  }
  return verdict;
}

export function statusDisplayLabel(status: TicketStatus): string {
  switch (status) {
    case 'Diff review':
      return 'Changes need review';
    case 'Awaiting acceptance':
      return 'Needs your acceptance';
    case 'In Progress':
      return 'In progress';
    case 'Abandoned':
      return 'Abandoned';
    case 'Split':
      return 'Superseded (split)';
    default:
      return status;
  }
}

/**
 * Canonical, user-facing vocabulary for the three human-decision states.
 * Use these everywhere (cards, detail panel, columns) so one concept reads the
 * same wherever it appears, instead of ad-hoc phrasings per component.
 */
export const STATE_COPY = {
  gate1: {
    badge: 'Needs approval',
    heading: 'Needs your approval to start',
    body: 'Decomposed by the Tech Lead. Approve to add this ticket to the development queue.',
    columnSublabel: 'Some tickets need your approval',
  },
  diff: {
    badge: 'Review changes',
    heading: 'Changes need your review',
    body: 'Review the proposed changes, then approve to merge or send back for rework.',
    columnSublabel: 'Includes changes to review',
  },
  gate2: {
    badge: 'Needs acceptance',
    heading: 'Needs your acceptance',
    body: 'Technical audit passed. Accept to close this ticket, or send back with feedback.',
    columnSublabel: 'Your final decision',
  },
} as const;

export const MANUAL_MOVE_LABELS: Partial<Record<TicketStatus, string>> = {
  Ready: 'Mark ready to start',
  'In Progress': 'Start work',
};

export function canShowMergeAction(status: TicketStatus): boolean {
  return status === 'Review' || status === 'Awaiting acceptance' || status === 'Done';
}
