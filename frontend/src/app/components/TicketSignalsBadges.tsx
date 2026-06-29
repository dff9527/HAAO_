import { Cloud, ShieldAlert } from 'lucide-react';
import type { TicketSignals } from '../api/types';
import { DOD_STRENGTH_STYLES, RISK_STYLES, type RiskLevel } from '../trustUtils';

interface Props {
  signals?: TicketSignals | null;
  compact?: boolean;
}

export function TicketSignalsBadges({ signals, compact = false }: Props) {
  if (!signals) return null;
  const risk = (signals.risk?.level ?? 'low') as RiskLevel;
  const dod = signals.dod_strength?.level ?? 'medium';
  const flags = signals.cloud_privacy_flags ?? [];

  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${compact ? '' : 'mt-1.5'}`}>
      <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium capitalize ${RISK_STYLES[risk]}`}>
        {risk} risk
      </span>
      <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium capitalize ${DOD_STRENGTH_STYLES[dod] ?? DOD_STRENGTH_STYLES.medium}`}>
        DoD {dod}
      </span>
      {flags.slice(0, compact ? 1 : 3).map((flag) => (
        <span
          key={flag.id}
          title={flag.message}
          className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded border border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
        >
          {flag.id.includes('cloud') ? <Cloud size={9} /> : <ShieldAlert size={9} />}
          {compact ? flag.id.replace(/_/g, ' ') : flag.message}
        </span>
      ))}
    </div>
  );
}
