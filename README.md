<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./frontend/src/assets/frame.png">
    <source media="(prefers-color-scheme: light)" srcset="./frontend/src/assets/frame-lite.png">
    <img src="./frontend/src/assets/frame-lite.png" alt="HAAO" width="400">
  </picture>
</p>
<p align="center"><b>Hybrid AI-Agile Orchestrator</b></p>
<p align="center">Talk to an agent about what you want built. It files the work, a hybrid AI team delivers it, you watch every step, and it ships as a pull request — you stay the Product Owner.</p>
<p align="center">
  <code>python · fastapi · react · LM Studio · cloud LLMs (Anthropic / OpenAI / …)</code>
</p>
<p align="center">
  <b>New here?</b> &nbsp;
  Non-technical operator → <a href="./OPERATOR_GUIDE.md">Operator's guide</a> &nbsp;·&nbsp;
  Why it's built this way → <a href="./HAAO_technical_article.md">Design article</a>
</p>

---

## Why this exists

Two ways of pointing LLMs at a codebase, two failure modes:

- **Pure cloud agents** (Devin-style): strong reasoning, but expensive at scale, raise privacy concerns, and drift on large projects.
- **Pure local models**: fast, private, cheap — but a small MoE can't hold architecture in its head or make sound high-level calls.

And a third problem with "vibe coding" against any agent: **you can't see what it intends to do, what it's doing now, or what it hasn't finished.** It's a black box.

HAAO answers both. It runs software delivery as a **Scrum process with a hybrid AI workforce** — a cloud model does the thinking that needs depth, local models do the high-frequency implementation, and a human owns direction and sign-off — and it makes the whole thing **legible**: a live board, a run-event stream, and an agent that reports back.

## How you use it

1. **Chat.** Tell the orchestrator agent what you want, in plain language. It restates what it heard and files it as **proposals in the backlog** — it never silently creates tickets.
2. **Approve.** You approve the backlog (Gate 1). Approved work starts automatically.
3. **Watch.** The hybrid team executes; the agent reports `done` / `blocked` back to you, and every step streams to the board and the Activity log.
4. **Accept & ship.** You accept the result (Gate 2); on accept, HAAO opens a **pull request** to your GitHub/GitLab.

## The core idea: roles, assigned where they belong

| Scrum role | Who | Responsibility |
|---|---|---|
| **Product Owner** | **You (human)** | Define requirements, prioritize, **approve the backlog, accept the result**. Accountability and taste stay human. |
| **Tech Lead / Architect** | Cloud model | Decompose requirements into atomic tickets, write machine-verifiable DoD, run the technical audit. |
| **Scrum Master** | The orchestrator (software) | Dispatch, route, enforce WIP, retry, escalate, move tickets — **automated**. |
| **Dev team** | Local LLMs (via LM Studio) — or cloud, your choice | Read context, write code, run tests, report back. |

The non-obvious choice: the **human is the PO, not the Scrum Master**. Process mechanics should be automated; product judgment shouldn't.

## What's in the box

- **Conversational intake.** A chat agent (cloud or local) turns a conversation into backlog proposals and reports progress back — the agent is your single point of contact.
- **The Atomic Ticket is a machine contract.** A [JSON-Schema ticket](./atomic_ticket.schema.json) is the handover format between the cloud Tech Lead and a coder: self-contained (code is **injected**, not referenced) with a **machine-verifiable** Definition of Done.
- **Hybrid cost routing, made visible.** Work stays local by default; a retry budget governs self-correction; only exhausted tickets escalate to cloud. Cost is tracked per ticket with an honest `actual / estimated / unknown` status — no false precision.
- **Bring any model.** Register multiple cloud providers (Anthropic / OpenAI / OpenRouter / …) with encrypted keys, or run everything local. Assign a specific model per role; discover a provider's models from your key.
- **Ships real work.** On accept, opens a branch + PR (least-privilege, idempotent) to GitHub/GitLab — via a personal access token **or** a GitHub/GitLab App installation.
- **Observability built in.** **Activity** (live run-event stream: model calls, diffs, retries, escalations, cost), **Insights** (throughput, cycle time, escalation rate, local-vs-cloud mix, cost dashboard, per-model scorecards), **Inbox** (cross-project needs-you / done / blocked).
- **Two human gates, nothing in between.** Approve the backlog (front), accept the result (tail). Everything else is automatic.

## Security

HAAO executes AI-written code and holds model/git credentials, so safety is first-class:

- **Sandboxed execution** — DoD/test commands run network-disabled with a scoped environment by default; outbound attempts are audited.
- **Encrypted secrets at rest** — cloud keys and integration tokens are AES-GCM encrypted (master secret from `HAAO_SECRET_KEY`).
- **Prompt-injection aware** — repo/attachment content is wrapped as untrusted; the models are told to treat it as data, not instructions.
- **Secret redaction** — keys/tokens are scrubbed from logs, run events, and streams.
- **Optional API auth** — set `HAAO_API_TOKEN` to require a bearer token on every route (HTTP + WebSocket).

## Architecture

```
        You (Product Owner)
   chat / approve │        │ accept → PR
                  ▼        ▲
        ┌────────────────────────────────┐
        │   Orchestrator (Scrum Master)  │  state machine · routing · retry · escalation · run-events
        └─┬────────┬───────────┬─────────┬┘
          │ decompose          │ run tests│ open PR
          │ + audit  │ dispatch │ (sandbox)│
        ┌─▼─────┐ ┌──▼───────┐ ┌▼────────┐ ┌▼──────────────┐
        │ Cloud │ │ Local /  │ │ pytest /│ │ GitHub/GitLab │
        │ Tech  │ │ cloud    │ │ npm test│ │ pull request  │
        │ Lead  │ │ dev team │ └─────────┘ └───────────────┘
        └───────┘ └──────────┘
```

## Getting started

### Docker (recommended)

```bash
cp .env.example .env          # set CLAUDE_API_KEY / model keys as needed
docker compose up --build
```

Open <http://localhost:3001> (API at <http://localhost:8000/health>). On macOS/Windows the backend reaches a local LM Studio via `host.docker.internal`. To use cloud models or PR output you'll set keys in Settings — note that adding encrypted cloud keys requires `HAAO_SECRET_KEY` in `.env` (generate with `openssl rand -base64 32`).

### Local (dev)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn orchestrator.main:app --reload
# frontend: cd frontend && npm install && npm run dev
pytest
```

## Roadmap

- [x] End-to-end loop (decompose → approve → execute + test → audit → accept)
- [x] Conversational agent intake (chat → backlog proposals + progress reports)
- [x] Multi-provider cloud model registry + per-role assignment, local or cloud execution
- [x] PR delivery to GitHub/GitLab on accept (PAT **or** GitHub/GitLab App)
- [x] Observability: Activity / Insights / Inbox
- [x] Security: sandboxed execution, encrypted secrets, prompt-injection wrapping, optional auth
- [x] Trust & recovery: PO Decision Center, richer gates, blocked-ticket recovery, diff-scope guards
- [x] Throughput: parallel worker pool + dependency graph + conflict detection
- [x] Split-plane foundations: client runner ↔ hosted control plane (keys stay client-side) — validated end-to-end over the internet
- [x] Team foundations: workspaces, role-based access, audit log, OIDC SSO
- [ ] Hosted Team service (managed control plane + billing) — foundations done, productionizing
- [ ] Wider benchmark + in-product eval harness expansion

## Design notes

The reasoning behind the architecture — why Scrum roles map this way, why the ticket is a contract, how hybrid routing works — is written up in the [**design article**](./HAAO_technical_article.md). Operating it rather than building it? Start with the [**operator's guide**](./OPERATOR_GUIDE.md).

> How this was built: HAAO is developed with heavy AI assistance (local + cloud agents — it eats its own dog food). The architecture, the role model, the ticket-as-contract design, and the cost-routing strategy are the human's; the implementation is delegated. The judgment isn't.

## Tech stack

Python · FastAPI · SQLite · React · Tailwind · LM Studio (local inference) · cloud LLM APIs.

## License

MIT — see [LICENSE](./LICENSE).
