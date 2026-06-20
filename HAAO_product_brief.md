# HAAO — Hybrid AI-Agile Orchestrator

**One-line:** A governance layer for AI software agents — Claude decomposes a
plain-language requirement into atomic, testable tickets; cheaper local models
execute the code; two human gates keep a person in control of what ships.

**Author:** Jerry · solo design → build → ship (PM + full-stack)
**Stage:** Working end-to-end prototype; controlled pilot complete.

---

## Problem

AI coding agents (Devin, Cursor agents, Copilot Workspace) are good at *writing*
code but weak on the things teams actually need to trust them: **scoping work,
controlling cost, keeping humans in the loop, and keeping code private.** They
tend to be one big opaque autopilot — you approve a giant diff at the end, or you
don't. There's no agile structure, no per-step human checkpoint, and every token
runs through an expensive frontier model.

## Insight / Why now

Frontier models are best used sparingly for **high-leverage reasoning**
(decomposition, technical audit), not for grinding out every line. Local
open-weight coders are now good enough to do the bulk *execution* cheaply and
privately. The missing piece is an **orchestration + governance layer** that
routes the right work to the right model and inserts human judgment where it
matters — framed in a mental model teams already trust: the agile ticket.

## Target users (ICP) & jobs-to-be-done

- **Primary:** small eng teams / tech leads who want AI throughput **without
  surrendering control**, and who have **code-privacy or cost constraints**
  (regulated, on-prem-leaning, or budget-sensitive).
- **JTBD:** "Turn a requirement into shippable, reviewed change sets — let AI do
  the volume, but let me approve scope before work starts and quality before it
  merges, and don't blow my model budget."

## Solution

A board where one requirement flows: **decompose → Gate 1 (approve scope) →
auto-dispatch → local execution + tests → technical audit → Gate 2 (accept) →
done.**

- **Hybrid routing:** Claude (Tech Lead) decomposes & audits; local models
  (LM Studio / OpenAI-compatible) execute. Cost tracked per requirement.
- **Two human gates:** Gate 1 = approve/edit/exclude proposed tickets before any
  code runs; Gate 2 = accept or send back with feedback (rework loop).
- **Atomic, self-contained tickets:** each ticket carries scope, context files,
  and a machine-verifiable Definition of Done (test command).
- **Resilience:** retry budget → auto-escalation to the cloud model when local
  attempts are exhausted; multi-project support with per-project cost.

**Positioning:** not "another coding agent" — the **control plane** that makes
agents safe and affordable to adopt.

## Differentiation

| | HAAO | Coding agents (Devin / Cursor / Copilot WS) |
|---|---|---|
| Human control | **Two explicit gates** (scope + acceptance) | Single end-of-run diff approval |
| Cost model | **Hybrid: frontier for reasoning, local for execution** | Frontier model for everything |
| Privacy | **Local execution option** (code can stay in-house) | Cloud-only |
| Mental model | **Agile tickets + DoD** | Freeform autopilot |
| Trust posture | Audit verdict + rework loop, reversible by design | Mostly opaque |

## Metrics

- **North-star:** *one-shot rate* — % of decomposed tickets the local model gets
  right on the first attempt (high one-shot rate = good granularity + context →
  low human toil + low cost).
- **Supporting:** local-finish rate, escalation rate (cloud spend), human
  interventions per ticket, cloud cost per requirement.
- **Pilot (controlled, seeded sandbox, n=8):** one-shot 100%, escalation 0%.
  *Honest caveat:* small n on curated tasks — an early positive signal, not a
  benchmark. **Next step:** validate on real repos at larger n with harder,
  failure-oriented tasks.

## Roadmap

- **Now (done):** end-to-end pipeline, two gates, hybrid routing, retry/escalation,
  multi-project, per-requirement cost tracking.
- **Next:** credible validation on real repos + metrics dashboard as the hero
  surface; richer Gate 1 editing (target files); auth + multi-tenant for shared use.
- **Later:** parallel per-project workers; policy/guardrail config (what agents may
  touch); pluggable model providers; team analytics (throughput, cost, intervention).

## Risks & open questions

- One-shot rate is task/repo-dependent — needs real-world validation before any
  claim holds.
- Executing model-written code is powerful and dangerous; safe deployment requires
  sandbox isolation + access control (handled as an explicit gate, not an
  afterthought).
- Where's the durable wedge vs. incumbents adding "approval steps" — likely the
  **hybrid cost/privacy** angle plus the governance UX.

---

*Live demo available on request (access-gated). Built solo as product owner: the
product definition, UX, architecture, role model, ticket-as-contract design, and
cost-routing strategy are mine. Implementation (React frontend, FastAPI backend,
orchestration code) was delegated to AI coding agents — the judgment is mine, the
code is AI-written.*
