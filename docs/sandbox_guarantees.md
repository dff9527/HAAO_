# Sandbox Guarantees

HAAO runs untrusted AI-generated code only inside ticket worktrees. The sandbox tier controls how much isolation the runner can actually enforce on the host.

## Threat Model

The sandbox assumes generated code may try to read files outside its ticket worktree, inspect host secrets, contact the network, consume excessive CPU/memory/time, or observe other tenants' processes. It does not make malicious kernel, Docker daemon, or privileged-host compromise impossible. Hosted Team should still prefer the split-plane runner model: customer code and provider keys stay on the customer-controlled runner.

## Modes

| Mode | Network | Filesystem | Env/secrets | Process/resource isolation | Multi-tenant guarantee |
|---|---|---|---|---|---|
| `none` | Allowed | Host command in configured cwd | Only allowlisted env is passed by HAAO, but no sandbox boundary | Subprocess timeout only | No multi-tenant isolation |
| `auto` | Denied when Docker or `unshare` is available; otherwise loud degradation events | Docker mounts only the worktree; `unshare` does not confine filesystem | Allowlisted env only | Docker/`unshare` network isolation; timeout | Best effort, not a strict multi-tenant boundary |
| `unshare` | Denied with Linux network namespace | No filesystem confinement beyond cwd discipline | Allowlisted env only | Network namespace and timeout | Not sufficient for hostile co-tenants |
| `docker` | Denied with `--network=none` | Only the ticket worktree is bind-mounted at `/workspace` | Allowlisted env only; ambient host env is stripped | Docker PID/filesystem isolation plus timeout | Stronger single-host isolation, dependent on Docker daemon hardening |
| `strict` | Deny by default with `--network=none`; network-looking failures emit `egress_attempt` | Ticket worktree is the only writable project mount; container root is read-only with tmpfs `/tmp` | Allowlisted env only; no ambient credentials | Requires Docker, drops capabilities, sets no-new-privileges, CPU/memory/PID limits, and subprocess timeout | HAAO's hardened multi-tenant tier. If Docker/image support is unavailable, HAAO emits loud `egress_attempt` and `error` run events and does not claim the strict guarantee |

## Degradation

`strict` never silently weakens its guarantee. If the runtime cannot provide Docker with the configured local image, the run emits sandbox `egress_attempt` and `error` events with `reason: sandbox_strict_unavailable`. Self-host installations can keep lighter modes such as `auto` or `none`, but Activity will show when isolation was unavailable.

## Egress Audit

Network egress from test commands is denied in restricted modes. If command output indicates a blocked outbound attempt, HAAO emits `egress_attempt` with `reason: network_blocked_by_sandbox`. Attachment or derived-attachment content sent to a cloud model emits `attachment_egress` with the attachment id, provider, model, requirement/ticket/chat reference fields, and timestamp.
