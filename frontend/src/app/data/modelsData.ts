import type { ModelConfig, RoleRoute, ConnectionSettings, AssignedModel } from '../types';

export const INITIAL_MODEL_CONFIGS: ModelConfig[] = [
  {
    id: 'qwen3-coder-next',
    name: 'qwen3-coder-next',
    backend: 'LM Studio (local)',
    quant: '8-bit MLX',
    contextWindow: 32768,
    status: 'Loaded',
    params: {
      temperature: 0.2,
      topP: 0.95,
      maxOutputTokens: 4096,
      contextWindowCap: 32768,
      defaultRetryBudget: 3,
      systemPrompt:
        'You are a precise software engineering agent. Implement the task exactly as specified in the ticket. Write clean, testable code. Do not add unrequested features. Always run the provided test commands before marking done.',
      additionalInstructions:
        'You are a precise software engineering agent. Implement the task exactly as specified in the ticket. Write clean, testable code. Do not add unrequested features. Always run the provided test commands before marking done.',
      fullPromptOverride: '',
      useFullPromptOverride: false,
    },
  },
  {
    id: 'gemma-4-26b-a4b',
    name: 'gemma-4-26b-a4b',
    backend: 'LM Studio (local)',
    quant: '8-bit MLX',
    moeActive: '~4B',
    contextWindow: 16384,
    status: 'Loaded',
    params: {
      temperature: 0.7,
      topP: 0.95,
      maxOutputTokens: 4096,
      contextWindowCap: 16384,
      defaultRetryBudget: 3,
      systemPrompt:
        'You are a fast triage and gatekeeper agent. Summarise agent logs concisely. Check definition-of-done criteria and flag any unmet items. Keep responses short and structured.',
      additionalInstructions:
        'You are a fast triage and gatekeeper agent. Summarise agent logs concisely. Check definition-of-done criteria and flag any unmet items. Keep responses short and structured.',
      fullPromptOverride: '',
      useFullPromptOverride: false,
    },
  },
  {
    id: 'qwen3.6-35b-a3b',
    name: 'qwen3.6-35b-a3b',
    backend: 'LM Studio (local)',
    quant: '8-bit MLX',
    moeActive: '~3B',
    contextWindow: 32768,
    status: 'Available',
    params: {
      temperature: 0.3,
      topP: 0.95,
      maxOutputTokens: 4096,
      contextWindowCap: 32768,
      defaultRetryBudget: 3,
      systemPrompt:
        'You are a careful software engineering agent. Prefer correctness over speed. Write well-commented code with thorough error handling. Always explain non-obvious decisions in code comments.',
      additionalInstructions:
        'You are a careful software engineering agent. Prefer correctness over speed. Write well-commented code with thorough error handling. Always explain non-obvious decisions in code comments.',
      fullPromptOverride: '',
      useFullPromptOverride: false,
    },
  },
  {
    id: 'Claude · Tech Lead',
    name: 'Claude Sonnet (PO)',
    backend: 'Cloud API',
    contextWindow: 200000,
    status: 'Connected',
    params: {
      temperature: 0.7,
      topP: 0.95,
      maxOutputTokens: 8192,
      contextWindowCap: 200000,
      defaultRetryBudget: 2,
      systemPrompt:
        "You are HAAO's Product Owner. Break high-level requirements into sprint-ready tickets with clear acceptance criteria and test commands. Audit completed work rigorously against the definition of done before moving to Review.",
      additionalInstructions:
        "You are HAAO's Product Owner. Break high-level requirements into sprint-ready tickets with clear acceptance criteria and test commands. Audit completed work rigorously against the definition of done before moving to Review.",
      fullPromptOverride: '',
      useFullPromptOverride: false,
    },
  },
];

export const INITIAL_ROLE_ROUTES: RoleRoute[] = [
  {
    id: 'tech_lead',
    role: 'Tech Lead (decompose + technical audit)',
    note: 'high-level reasoning, cloud only',
    model: 'Claude · Tech Lead',
  },
  {
    id: 'dev',
    role: 'Dev — code execution',
    note: 'main coder',
    model: 'qwen3-coder-next',
  },
  {
    id: 'gatekeeper',
    role: 'Gatekeeper (triage / log summary / DoD pre-check)',
    note: 'fast, cheap, local',
    model: 'gemma-4-26b-a4b',
  },
  {
    id: 'escalation',
    role: 'Escalation target (auto)',
    note: 'auto-assigned by orchestrator when local budget exhausted',
    model: 'Claude · Tech Lead',
  },
];

export const INITIAL_CONNECTIONS: ConnectionSettings = {
  lmStudioUrl: 'http://localhost:1234/v1',
};

export const SAMPLE_REPLIES: Record<AssignedModel, string> = {
  'qwen3-coder-next': 'def hash_password(pwd: str) -> bytes: return bcrypt.hashpw(pwd.encode(), bcrypt.gensalt(rounds=12))',
  'gemma-4-26b-a4b': 'Log summary: 1 error (salt missing), 3 info. DoD: 2/4 criteria met. Recommend retry.',
  'qwen3.6-35b-a3b': 'Analysis complete. Recommend extracting auth middleware for cleaner separation of concerns.',
  'Claude · Tech Lead': 'Requirement accepted. Decomposing into 3 tickets: data model, endpoint, integration tests.',
};
