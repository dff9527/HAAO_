import type { Page, Route } from '@playwright/test';

const WORKER_STATUS = {
  running: false,
  interval_sec: 5,
  max_cycles_per_tick: 1,
  max_workers: 1,
  allow_dirty_workspace: false,
  last_started_at: null,
  last_run_at: null,
  last_error: '',
  last_skipped_reason: '',
  project_id: null,
};

const IDENTITY_CONTEXT = {
  context: {
    identity_configured: false,
    actor_id: 'implicit-owner',
    workspace_id: 'default',
    role: 'owner',
    implicit_owner: true,
    permissions: ['read', 'mutate', 'admin'],
  },
};

function fulfillJson(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

export async function fulfillHealthyApiRoute(route: Route) {
  const pathname = new URL(route.request().url()).pathname;

  if (pathname.includes('/tickets/graph')) {
    return fulfillJson(route, { nodes: [], edges: [] });
  }
  if (pathname.endsWith('/tickets') || pathname.includes('/tickets?')) {
    return fulfillJson(route, { tickets: [] });
  }
  if (pathname.includes('/chat/messages')) {
    return fulfillJson(route, { messages: [] });
  }
  if (pathname.includes('/requirements')) {
    return fulfillJson(route, { requirements: [] });
  }
  if (pathname.includes('/identity/context')) {
    return fulfillJson(route, IDENTITY_CONTEXT);
  }
  if (pathname.includes('/auth/oidc/login')) {
    return fulfillJson(route, { authorization_url: 'https://sso.example/login' });
  }
  if (pathname.includes('/auth/oidc')) {
    return fulfillJson(route, { provider: null });
  }
  if (pathname.includes('/orchestrator/worker/status')) {
    return fulfillJson(route, WORKER_STATUS);
  }
  if (pathname.includes('/notifications')) {
    return fulfillJson(route, { notifications: [], unread_count: { total: 0, by_project: {} } });
  }
  if (pathname.includes('/decisions')) {
    return fulfillJson(route, { counts: {}, groups: [] });
  }
  if (pathname.endsWith('/projects')) {
    return fulfillJson(route, {
      projects: [{
        id: 'default',
        name: 'HAAO',
        path: '/tmp/haao',
        default_branch: 'main',
      }],
    });
  }
  if (pathname.includes('/config/cloud-models')) {
    return fulfillJson(route, { models: [] });
  }
  if (pathname.includes('/config/cloud-execution')) {
    return fulfillJson(route, { allow_cloud_execution_model: false });
  }
  if (pathname.includes('/config/chat-reasoner')) {
    return fulfillJson(route, { mode: 'cloud' });
  }
  if (pathname.includes('/config/claude-model')) {
    return fulfillJson(route, { model: 'claude-sonnet-4-6' });
  }
  if (pathname.includes('/config/cloud-reasoner')) {
    return fulfillJson(route, { model_id: 'claude-sonnet-4-6', provider: 'anthropic', providers: [] });
  }
  if (pathname.includes('/config/role-routing')) {
    return fulfillJson(route, { routing: { routes: [] } });
  }
  if (pathname.includes('/config/notifications')) {
    return fulfillJson(route, { webhook_url: '' });
  }
  if (pathname.includes('/config/integrations')) {
    return fulfillJson(route, { integrations: [] });
  }
  if (pathname.includes('/models/local/endpoints')) {
    return fulfillJson(route, { endpoints: [] });
  }
  if (pathname.includes('/models/local/available')) {
    return fulfillJson(route, { models: [], endpoints: [] });
  }

  return fulfillJson(route, {});
}

export async function mockHealthyApi(page: Page) {
  await page.route('**/api/**', fulfillHealthyApiRoute);
}

export async function mockTicketsAuthChallenge(
  page: Page,
  challenge: { status: 401; reason: 'api_token_required' | 'login_required'; detail: string },
  validToken = 'valid-token',
) {
  let ticketsAuthorized = false;

  await page.route('**/api/**', async (route: Route) => {
    const pathname = new URL(route.request().url()).pathname;
    const isTicketsList = pathname.endsWith('/tickets') || pathname.includes('/tickets?');
    const isTicketsGraph = pathname.includes('/tickets/graph');

    if (isTicketsList || isTicketsGraph) {
      const auth = route.request().headers().authorization ?? '';
      if (!ticketsAuthorized) {
        if (challenge.reason === 'api_token_required' && auth === `Bearer ${validToken}`) {
          ticketsAuthorized = true;
        } else {
          return fulfillJson(route, { detail: challenge.detail, reason: challenge.reason }, challenge.status);
        }
      }

      if (isTicketsGraph) {
        return fulfillJson(route, { nodes: [], edges: [] });
      }
      return fulfillJson(route, { tickets: [] });
    }

    return fulfillHealthyApiRoute(route);
  });
}
