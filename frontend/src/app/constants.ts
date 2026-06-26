import type { TicketStatus, TicketType, Priority } from './types';

export const DEFAULT_LOCAL_MODELS = [
  'qwen3-coder-next',
  'gemma-4-26b-a4b',
  'qwen3.6-35b-a3b',
];

export const MODEL_META: Record<string, {
  label: string;
  pillClass: string;
  accent: string;
}> = {
  'qwen3-coder-next': {
    label: 'coder-next',
    pillClass: 'bg-blue-50 text-blue-700 border border-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:border-blue-800',
    accent: '#2563EB',
  },
  'gemma-4-26b-a4b': {
    label: 'gemma (gatekeeper)',
    pillClass: 'bg-violet-50 text-violet-700 border border-violet-200 dark:bg-violet-950 dark:text-violet-300 dark:border-violet-800',
    accent: '#7C3AED',
  },
  'qwen3.6-35b-a3b': {
    label: 'qwen3.6',
    pillClass: 'bg-teal-50 text-teal-700 border border-teal-200 dark:bg-teal-950 dark:text-teal-300 dark:border-teal-800',
    accent: '#0D9488',
  },
  'Claude · Tech Lead': {
    label: 'Cloud · Tech Lead',
    pillClass: 'bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800',
    accent: '#D97706',
  },
};

// Cloud reasoner ids are provider-qualified ("openai:gpt-4o"). Show the provider
// dynamically so the Tech Lead pill reflects whichever cloud model is in use.
const CLOUD_PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Claude',
  openai: 'OpenAI',
  google: 'Gemini',
  gemini: 'Gemini',
  openrouter: 'OpenRouter',
  together: 'Together',
  fireworks: 'Fireworks',
};

const CLOUD_PILL = 'bg-amber-50 text-amber-700 border border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800';

function cloudReasonerMeta(model: string) {
  const idx = model.indexOf(':');
  if (idx <= 0) return null;
  const provider = CLOUD_PROVIDER_LABELS[model.slice(0, idx).toLowerCase()];
  if (!provider) return null;
  return { label: `${provider} · Tech Lead`, pillClass: CLOUD_PILL, accent: '#D97706' };
}

export const CLOUD_PROVIDER_PREFIXES = new Set([
  'anthropic',
  'openai',
  'google',
  'gemini',
  'openrouter',
  'together',
  'fireworks',
]);

export function isCloudModel(model: string): boolean {
  if (model === 'Claude · Tech Lead' || model === 'claude-tech-lead') {
    return true;
  }
  const idx = model.indexOf(':');
  if (idx <= 0) {
    return false;
  }
  return CLOUD_PROVIDER_PREFIXES.has(model.slice(0, idx).toLowerCase());
}

export function formatCloudCost(usd?: number): string | null {
  if (usd === undefined || usd <= 0) {
    return null;
  }
  return `$${usd.toFixed(4)}`;
}

export function getModelMeta(model: string) {
  // Exact match first (covers the legacy "Claude · Tech Lead" id).
  const direct = MODEL_META[model];
  if (direct) return direct;
  // Provider-qualified cloud reasoner -> dynamic "<Provider> · Tech Lead".
  const cloud = cloudReasonerMeta(model);
  if (cloud) return cloud;
  // Local ids often arrive vendor-prefixed (e.g. "qwen/qwen3-coder-next") for the
  // same model as the bare id. Strip the prefix and re-check so label/pill stay
  // consistent regardless of which form was stored.
  const bare = model.includes('/') ? model.slice(model.lastIndexOf('/') + 1) : model;
  return MODEL_META[bare] ?? {
    label: bare,
    pillClass: 'bg-zinc-50 text-zinc-700 border border-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:border-zinc-700',
    accent: '#71717A',
  };
}

export const STATUS_CLASSES: Record<TicketStatus, string> = {
  Backlog: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
  Ready: 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300',
  'In Progress': 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300',
  'Diff review': 'bg-cyan-50 text-cyan-700 dark:bg-cyan-950 dark:text-cyan-300',
  Review: 'bg-violet-50 text-violet-700 dark:bg-violet-950 dark:text-violet-300',
  'Awaiting acceptance': 'bg-orange-50 text-orange-700 dark:bg-orange-950 dark:text-orange-300',
  Done: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
  Blocked: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
};

export const TYPE_CLASSES: Record<TicketType, string> = {
  feature: 'bg-blue-50 text-blue-600 dark:bg-blue-950 dark:text-blue-400',
  bugfix: 'bg-red-50 text-red-600 dark:bg-red-950 dark:text-red-400',
  refactor: 'bg-yellow-50 text-yellow-700 dark:bg-yellow-950 dark:text-yellow-400',
  test: 'bg-violet-50 text-violet-600 dark:bg-violet-950 dark:text-violet-400',
  chore: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300',
};

export const PRIORITY_CLASSES: Record<Priority, string> = {
  high: 'text-red-500',
  medium: 'text-amber-500',
  low: 'text-zinc-400',
};

export const COLUMNS: TicketStatus[] = [
  'Backlog',
  'Ready',
  'In Progress',
  'Review',
  'Awaiting acceptance',
  'Done',
];

export const MANUAL_STATUS_TRANSITIONS: Record<TicketStatus, TicketStatus[]> = {
  Backlog: ['Ready'],
  Ready: ['In Progress'],
  'In Progress': [],
  'Diff review': [],
  Review: [],
  'Awaiting acceptance': [],
  Done: [],
  Blocked: [],
};

export function canManuallyMoveTicket(from: TicketStatus, to: TicketStatus): boolean {
  return MANUAL_STATUS_TRANSITIONS[from]?.includes(to) ?? false;
}

export const MANUAL_DROP_HINT = 'You can only move Backlog → Ready and Ready → In Progress.';
