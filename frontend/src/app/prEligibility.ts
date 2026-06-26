import type { Ticket } from './types';

export type PrGateReason =
  | 'dod_not_passed'
  | 'gatekeeper_not_approved'
  | 'not_accepted'
  | 'no_integration';

const TOOLTIP: Record<PrGateReason, string> = {
  dod_not_passed: 'Definition of done must pass before opening a PR.',
  gatekeeper_not_approved: 'Gatekeeper must approve this ticket before opening a PR.',
  not_accepted: 'Ticket must be awaiting acceptance or accepted by the Product Owner.',
  no_integration: 'Connect GitHub or GitLab in Settings → Integrations.',
};

export interface PrEligibility {
  eligible: boolean;
  reason?: PrGateReason;
  tooltip: string;
}

function dodPassed(ticket: Ticket): boolean {
  return ticket.testStatus === 'pass';
}

function gatekeeperApproved(ticket: Ticket): boolean {
  return ticket.technicalAudit?.verdict === 'approved';
}

function acceptanceReached(ticket: Ticket): boolean {
  return ticket.status === 'Awaiting acceptance' || ticket.status === 'Done';
}

export function prEligibility(ticket: Ticket, prIntegrationConfigured: boolean): PrEligibility {
  if (!dodPassed(ticket)) {
    return { eligible: false, reason: 'dod_not_passed', tooltip: TOOLTIP.dod_not_passed };
  }
  if (!gatekeeperApproved(ticket)) {
    return { eligible: false, reason: 'gatekeeper_not_approved', tooltip: TOOLTIP.gatekeeper_not_approved };
  }
  if (!acceptanceReached(ticket)) {
    return { eligible: false, reason: 'not_accepted', tooltip: TOOLTIP.not_accepted };
  }
  if (!prIntegrationConfigured) {
    return { eligible: false, reason: 'no_integration', tooltip: TOOLTIP.no_integration };
  }
  return { eligible: true, tooltip: '' };
}

export function showConnectGithubHint(ticket: Ticket, prIntegrationConfigured: boolean): boolean {
  return ticket.status === 'Done' && !ticket.prUrl && !prIntegrationConfigured;
}
