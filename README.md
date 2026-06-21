<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./frontend/src/assets/frame.png">
    <source media="(prefers-color-scheme: light)" srcset="./frontend/src/assets/frame-lite.png">
    <img src="./frontend/src/assets/frame-lite.png" alt="HAAO" width="400">
  </picture>
</p>
<p align="center"><b>Hybrid AI-Agile Orchestrator</b></p>
<p align="center">Run AI coding agents like a Scrum team — you stay the Product Owner, a cloud model is the Tech Lead, and local LLMs do the work.</p>
<p align="center">
  <b>▶ Live demo:</b> <a href="https://haao-demo.pages.dev">haao-demo.pages.dev</a>
  &nbsp;·&nbsp; <i>interactive simulation — no live model calls</i>
</p>
<p align="center">
  <code>status: working end-to-end prototype · controlled pilot complete</code> ·
  <code>python · fastapi · react · LM Studio · Claude</code>
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
- **Pure local models**: fast, private, cheap — but a 3–4B-active MoE can't hold architecture in its head or make sound high-level calls.

HAAO splits the difference. It treats software delivery as a **Scrum process with a hybrid AI workforce**: a cloud model handles the thinking that needs depth, local models handle the high-frequency implementation, and a human owns the direction and the sign-off. The orchestrator runs the process so the human doesn't have to babysit a ticket queue.

## The core idea: roles, assigned where they belong

| Scrum role | Who | Responsibility |
|---|---|---|
| **Product Owner** | **You (human)** | Define requirements, prioritize, **approve the backlog, accept the result**. Accountability and taste stay human. |
| **Tech Lead / Architect** | Cloud model (Claude) | Decompose requirements into atomic tickets, write machine-verifiable DoD, run technical audit. |
| **Scrum Master** | The orchestrator (software) | Dispatch, route, enforce WIP, retry, escalate, move tickets — **automated**. |
| **Dev team** | Local LLMs (via LM Studio) | Read context, write code, run tests, report back. |

The non-obvious design choice: the **human is the PO, not the Scrum Master**. Process mechanics (the Scrum Master's job) are exactly what should be automated; product judgment (the PO's job) is exactly what shouldn't. Putting the human on the board's manual controls would trap them in the lowest-leverage work.

## What's actually novel here

- **The Atomic Ticket is a machine contract.** A [JSON-Schema-defined ticket](./atomic_ticket.schema.json) is the handover format between the cloud Tech Lead and a local coder. It is self-contained (relevant code is **injected**, not referenced by filename), and its Definition of Done is **machine-verifiable** (test commands, static checks) so completion isn't a matter of opinion.
- **Hybrid cost routing.** Work stays local by default. A retry budget governs self-correction; only when local attempts are exhausted does the ticket escalate to the cloud. Cheap, machine checks gate the expensive cloud audit — so you don't pay a frontier model to read every diff.
- **Two human gates, nothing in between.** The PO approves a decomposed backlog (front) and accepts finished work (tail). Everything else is automatic.
- **Transparency by default.** Every agent action streams to a Kanban board with a live log. Unlike a black-box autonomous agent, you can watch what each model is doing and why.

## Architecture

```
        You (Product Owner)
   write prompt │           │ approve / accept
                ▼           ▲
        ┌────────────────────────────────┐
        │   Orchestrator (Scrum Master)  │  state machine · routing · retry · escalation
        └───┬───────────┬───────────┬────┘
            │ decompose │ dispatch  │ run tests
            │ + audit   │           │
        ┌───▼────┐  ┌───▼────────┐  ┌▼───────────────┐
        │ Claude │  │ Local LLMs │  │ pytest/npm test│
        │ (Tech  │  │ (LM Studio)│  │ (validation)   │
        │  Lead) │  │  dev team  │  └────────────────┘
        └────────┘  └────────────┘
```

## The loop

1. **Prompt** — the PO writes a requirement; the Tech Lead decomposes it into atomic tickets; the PO reviews and approves (Gate 1).
2. **Execute** — the orchestrator dispatches each ticket to its assigned local model, which writes code and runs the ticket's tests.
3. **Self-correct** — on failure, the worker retries within budget; if exhausted, it escalates to the Tech Lead.
4. **Audit** — the Tech Lead checks the diff against the DoD (technical audit, automatic).
5. **Accept** — the PO accepts or rejects with feedback (Gate 2).

## Status & honest scope

Working end-to-end prototype, built to validate **one hypothesis**: *can a local coder model one-shot the atomic tickets a cloud model produces?* The full loop runs (decompose → Gate 1 → execute + test → audit → Gate 2 → done), and a **controlled pilot** has been completed on a seeded sandbox. *Honest scope:* the pilot is small-n on curated tasks — an early positive signal, **not a benchmark**. The prototype is deliberately single-project, single-worker, single-file-plus-one-test tickets; scaling out a model fleet or multi-project support is intentionally deferred until the one-shot number holds on real repos.

## Roadmap

- [x] End-to-end loop on one project, with a measured one-shot success rate (controlled pilot, small-n)
- [ ] Hybrid cost benchmark: local vs. cloud, cost per accepted ticket, on consumer hardware
- [x] Prompt/requirement composer with decomposition preview
- [ ] Ticket dependency graph + parallel workers
- [ ] Conflict handling for concurrent edits

## Getting started

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `CLAUDE_API_KEY` when Claude Tech Lead decomposition / technical audit is enabled, and point `LMSTUDIO_BASE_URL` at your local LM Studio server.

Run and health-check:

```bash
uvicorn orchestrator.main:app --reload
curl http://127.0.0.1:8000/health
pytest
```

## Design notes

The reasoning behind the architecture — why Scrum roles map this way, why the ticket is a contract, and how the hybrid routing works — is written up here: [**design article**](./HAAO_technical_article.md). If you're operating HAAO rather than building it (PM / non-engineering background), start with the plain-language [**operator's guide**](./OPERATOR_GUIDE.md).

> A note on how this was built: HAAO is developed with heavy AI assistance (local + cloud agents — it eats its own dog food). The parts that are mine are the architecture, the role model, the ticket-as-contract design, and the cost-routing strategy. The implementation is delegated; the judgment isn't.

## Tech stack

Python · FastAPI · SQLite · React · Tailwind · LM Studio (local inference) · Claude API (cloud).

## License

MIT — see [LICENSE](./LICENSE).
