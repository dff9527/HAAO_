# HAAO Runner Protocol v1

Status: frozen foundation for Wave 6.

## Boundary

The hosted control plane coordinates work only. It stores tickets, job leases,
run events, and terminal summaries. It never executes code and never stores model
or provider keys. A customer-side runner owns repository access, provider keys,
test execution, and PR credentials.

## Authentication

Each runner has one token scoped to a workspace.

- Issue: `POST /api/runner/register`
- Revoke: `POST /api/runner/revoke/{runner_id}`
- Use: `Authorization: Bearer <runner-token>` or `X-HAAO-Runner-Token`

Tokens are stored server-side as SHA-256 hashes. Raw tokens are returned only on
issue.

## State Machine

`queued -> leased -> terminal`

Expired `leased` jobs are reclaimable by another active runner for the same
workspace.

## Messages

Register request:

```json
{"workspace_id":"default","label":"mac-mini-runner"}
```

Register response:

```json
{"runner":{"id":"runner-...","workspace_id":"default","label":"mac-mini-runner"},"token":"hrun_..."}
```

Heartbeat:

```http
POST /api/runner/heartbeat
Authorization: Bearer hrun_...
```

Lease request:

```json
{"ttl_sec":300}
```

Lease response:

```json
{"job":{"id":"job-...","workspace_id":"default","status":"leased","payload":{}}}
```

Run event stream:

```json
{
  "events": [
    {
      "project_id": "default",
      "ticket_id": "T-001",
      "run_id": "RUN-abc",
      "event_type": "dod_check",
      "payload": {"command": "pytest -q", "status": "pass"}
    }
  ]
}
```

Terminal result:

```json
{"status":"terminal","result":{"outcome":"success","ticket_id":"T-001"}}
```

## Compatibility

The event payload reuses the existing append-only `run_events` contract. Runner
jobs are control-plane leases only; execution details stay in the client runner.
