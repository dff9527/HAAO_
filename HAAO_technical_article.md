# Managing your AI dev team: why I gave them a Scrum board instead of a chat box

*Draft — design notes behind HAAO (Hybrid AI-Agile Orchestrator)*

## The bottleneck moved

A year ago the hard part of using LLMs for code was getting one good answer. That's mostly solved. The hard part now is the opposite: you can spawn ten agents, run a local coder model for free, call a frontier model for the tricky bits — and the bottleneck becomes *coordination*. How do you decide what each model works on, when to trust a cheap local model versus an expensive cloud one, and how to keep the whole thing from drifting off course on a large project?

Most tools answer this with a chat box and an autonomous loop. You type a goal, the agent goes off and does... something, and you find out how it went later. That's fine for a throwaway script. It's a bad fit for real software, where you want to *steer* and you want to *see*.

HAAO is my attempt at a different answer: **treat the AI workforce as a Scrum team**, and — this is the part most people get backwards — assign the roles where they actually belong.

## The two failure modes I was trying to escape

**Pure cloud agents** reason well but are expensive at scale, raise privacy questions, and drift over long horizons because every step burns context and money.

**Pure local models** are fast, private, and free. But the ones you can run on a laptop are MoE models with only 3–4B *active* parameters. They're great at small, well-scoped edits and useless at "design the auth system." Asking them to decompose a feature or audit a diff for semantic correctness is asking the wrong organ to do the job.

The obvious move is hybrid: local for the high-frequency implementation, cloud for the depth. The non-obvious part is *where the human stands*.

## Roles, assigned correctly

In Scrum, the Product Owner owns *what and why* — priorities, value, acceptance. The Scrum Master owns *process* — unblocking, routing, keeping flow. They are different jobs, and one of them is far more automatable than the other.

So here's the mapping HAAO uses:

- **Product Owner — the human.** You write requirements, set priorities, approve the backlog, and accept the result. This is the part that requires taste and accountability, and it's the part you least want to hand to a model.
- **Tech Lead / Architect — the cloud model.** It decomposes requirements into atomic tickets, writes the Definition of Done, and runs technical audits. Note this is *not* the Product Owner; it's an engineer with good judgment, not the person who decides what's worth building.
- **Scrum Master — the orchestrator itself.** Dispatching, routing, enforcing WIP, retrying, escalating, moving tickets. This is mechanical, and so it's software, not a person.
- **Dev team — local LLMs.** They read context, write code, run tests, report back.

I want to dwell on the choice that took me longest to see: **the human should be the PO, not the Scrum Master.** My first instinct — and the first instinct of almost everyone I've described this to — was "I'll be the Scrum Master, managing the board and assigning work to models." That's a trap. The Scrum Master's work is exactly the work that should be automated: it's rules, routing, and flow control. If you make the human do it, you've automated away the interesting judgment and kept the human busy dragging cards. Design it the other way: automate the Scrum Master, and let the human climb toward pure product ownership.

## The Atomic Ticket is a contract, not a comment

If a cloud model is going to hand work to a local model, the handover format matters more than either model. In HAAO that format is the **Atomic Ticket**, defined by a JSON Schema. Three properties make it work:

**It's machine-readable.** The local model parses it without having to guess intent. No "you know what I mean."

**It's self-contained.** Because the executor is a small-active-param model, you cannot assume it remembers the architecture or will go find the right file. So the ticket *injects* the relevant code directly — file snapshots, related signatures — rather than naming a path and hoping. This is the unglamorous heart of "context-aware": it's just a disciplined RAG step that fills the ticket before dispatch.

**Its Definition of Done is verifiable.** The DoD is a set of commands — `pytest ...`, a type check, a lint — with expected outcomes. "Done" is a test result, not an opinion. This is what lets the system run without a human in the loop for every step, and it's what keeps the expensive audit cheap (more on that next).

A ticket also carries a **retry budget** and an **escalation target**, which is where the cost story lives.

## Hybrid cost routing: keep it local until it can't be

The naive hybrid design — "cloud plans, local executes, cloud reviews" — quietly puts the cloud back in the hot path. If the cloud model has to read every diff to audit it, you're paying frontier prices on every ticket; you've moved the cost, not removed it.

HAAO gates the cloud behind cheap checks:

1. A local model executes the ticket and runs the DoD tests locally. **Free.**
2. On failure, it self-corrects and retries — up to the ticket's retry budget. Still free.
3. Only when the budget is exhausted does the ticket escalate to the cloud Tech Lead.
4. The technical audit runs against the *diff and the DoD*, not the whole repo — and it only runs once the machine checks already pass.

The decision of "local vs. cloud" stops being a vibe and becomes a policy you can tune and measure: retry budget, escalation threshold, and what the cloud is allowed to see. The metric that matters is *cost per accepted ticket*, and the lever is *how much you can keep below the escalation line*.

## Make it glass, not a black box

Every action streams to a Kanban board with a live agent log. You can watch a ticket move `ready → in_progress → testing`, see the local model rewrite a function, see pytest fail with "salt missing," see it retry. The two places a human actually touches the flow are deliberate gates: approving the decomposed backlog at the front, accepting finished work at the end. Everything between is automatic but visible.

This is a product opinion as much as a technical one. I don't want an agent that disappears for twenty minutes and returns a PR. I want a factory floor I can walk.

## First real result: the bottleneck was the output format, not the model

I built a small harness to measure the one-shot rate end to end with real models — Claude decomposing, a local `qwen3-coder-next` (8-bit MLX on a laptop) executing, pytest gating. The first numbers looked terrible: across ten trials of a trivial "fix this email validator" ticket, the local model's first-attempt success rate was **0/10**.

It would have been easy to conclude the local model just isn't good enough. But the harness captured *why* each trial failed, and the answer was the same every time: `git apply: corrupt patch`. The model was solving the task fine — it just couldn't emit a byte-perfect unified diff. The failure was plumbing, not capability.

So I changed one thing: instead of asking the local model for a diff to `git apply`, I asked it for the **full updated file** and wrote it directly. Same model, same task, same conditions, ten trials:

| Output format | One-shot rate |
|---|---|
| Unified diff + `git apply` | **0/10** (every failure a corrupt patch) |
| Whole-file rewrite | **10/10** |

That's the most useful thing I've learned building this. Small local models are unreliable diff generators but competent file authors. If you're handing real edits to a local coder, the interface you give it — diff versus whole file — matters more than a few billion parameters. (The honest caveat: this is one task many times, not many tasks; and whole-file rewrites don't scale to large files, so the production version needs a size threshold. But the mechanism is clear.)

The broader point for the architecture: the hybrid model held up. The local worker finished the task locally, the cloud was never escalated to, and the fix was a cheap change to the *handoff format* — exactly the kind of lever you can only find by measuring instead of assuming.

## What I'm not sure about yet

I'm trying to be honest about the open questions, because they're the whole ballgame:

- **One-shot success rate, at breadth.** The whole-file result above is one task measured many times. The open question is whether it holds across many *different* tickets and bigger files. If the rate sags on harder tasks, the answer isn't a better orchestrator — it's smaller tickets and tighter context injection.
- **Granularity has no closed form.** Too coarse and the local model flails; too fine and the human (or the Tech Lead) drowns in ticket-routing overhead. There's a "stop decomposing here" criterion I don't have a clean formula for yet.
- **Concurrency is where the real engineering is.** The happy path is easy to draw. Ticket dependencies, two tickets editing the same file, and human interruption mid-run are the hard parts. The MVP sidesteps them with a single serial worker on purpose.

## Where this goes

The next milestone isn't features — it's a number. Run the loop end to end on one real project and measure the one-shot rate and the cost per accepted ticket on consumer hardware. If those look good, the rest (parallel workers, dependency graphs, multi-project) is worth building. If they don't, the architecture taught me something cheap.

*Since writing this, the loop grew up around its edges.* The front door is now a **conversation**: you talk to an orchestrator agent, it restates what it heard and files the work as backlog proposals, and it reports `done` / `blocked` back — the Scrum Master, made conversational. The tail now **ships**: accepted work opens a pull request to GitHub/GitLab. The "glass factory floor" got real instruments — an Activity stream of every run event, an Insights dashboard (throughput, escalation rate, local-vs-cloud cost with an honest `actual / estimated / unknown` status), and a cross-project Inbox. You can register multiple cloud providers and assign any model per role, or stay fully local. And because it runs AI-written code and holds keys, safety became first-class: sandboxed (network-disabled) execution, AES-GCM-encrypted secrets, prompt-injection-aware context, log redaction. The deployment shape that falls out of all this is **split-plane** — host the control plane, keep execution and keys on the user's side — the cheapest way to ship it and the one that never runs your code on someone else's box. None of that changed the thesis; it just made it usable.

If you're thinking about how to manage AI coding agents rather than just prompt them, I'd love to compare notes.

---

*HAAO is open source and developed in the open, with heavy AI assistance — it builds itself through the same loop it implements. The architecture, role model, and ticket-as-contract design are the human contribution; the implementation is delegated.*
