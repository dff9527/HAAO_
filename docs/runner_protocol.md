# HAAO Runner Protocol v1.1

Status: frozen Team split-plane contract.

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
issue. Every runner-token endpoint validates the token and workspace scope.
`401` means revoked or invalid; runners must stop and clear local state. To
recover from lost local state, register a new runner and revoke the old one from
the control plane.

## State Machine

`queued -> leased -> terminal`

Expired `leased` jobs are reclaimable by another active runner for the same
workspace. Clean runner shutdown should release a non-terminal lease:

`POST /api/runner/jobs/{job_id}/release`

Release returns the job to `queued` and clears lease ownership.

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

```http
POST /api/runner/events
Authorization: Bearer hrun_...
```

```json
{
  "job_id": "job-...",
  "events": [
    {
      "requirement_id": "REQ-001",
      "ticket_id": "T-001",
      "run_id": "RUN-abc",
      "event_type": "dod_check",
      "model_id": "qwen3-coder-next",
      "payload": {"command": "pytest -q", "status": "pass"}
    }
  ]
}
```

The control plane derives `project_id`/workspace from the authenticated runner
and active lease; runner-supplied `project_id` is ignored. `job_id` must be
actively leased by the caller.

Terminal result, canonical path:

```http
POST /api/runner/jobs/{job_id}/complete
Authorization: Bearer hrun_...
```

```json
{"status":"terminal","result":{"outcome":"success","ticket_id":"T-001"}}
```

Compatibility alias:

```http
POST /api/runner/complete
Authorization: Bearer hrun_...
```

```json
{"job_id":"job-...","status":"terminal","result":{"outcome":"success","ticket_id":"T-001"}}
```

## Compatibility

The event payload reuses the existing append-only `run_events` contract. Runner
jobs are control-plane leases only; execution details stay in the client runner.

v1.1 freezes the previously implicit `events` and `complete` payload shapes:
event batches are job-bound, completion is job-bound, and release is the clean
shutdown path for a non-terminal lease.
