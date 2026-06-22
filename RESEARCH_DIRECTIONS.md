# SYNAPSE — Competitive Landscape & Novel Research Directions

**Updated:** 2026-06-19 · companion to [`PROJECT_PLAN.md`](PROJECT_PLAN.md)

This document positions SYNAPSE against the closest production system in its space (**Composio**)
and lays out the research bets that make SYNAPSE *novel and defensible* — including a paper
through-line for IEEE TENCON / NeurIPS Tool-Use workshop. Every direction here is **measurable on
the existing evaluation harness**; measurability is the moat.

---

## 1. Related work: Composio (grounded analysis)

Read from the actual repo (`github.com/ComposioHQ/composio`, Python+TS SDK) and its docs.

**What it is.** A curated catalog of pre-built **toolkits** (Gmail, Slack, …) with managed auth,
exposed to agents as **native tools** (per-framework) or a **hosted MCP URL**. The unifying
abstraction is the **session** (user + tools + auth + MCP + state); the former "Tool Router"
graduated into it.

**The convergence — they validate SYNAPSE's premise, almost verbatim:**
> *"Instead of loading hundreds of tool definitions into context, a session gives your agent
> **meta tools** that discover, authenticate, and execute tools at runtime."*
> *"A 5-server [MCP] setup can consume **~55K tokens** before the conversation starts."*

A funded incumbent re-architected around the exact problem SYNAPSE studies. That is strong
external validation — and a citable motivation for the paper.

**The divergence — opposite solution:**

| | Composio | SYNAPSE |
|---|---|---|
| When | **Runtime** — `COMPOSIO_SEARCH_TOOLS` finds tools on demand | **Build-time** — spec compiled into a small semantic surface |
| How | Filter/search a catalog; load schemas lazily | Semantically **merge** N endpoints → M tools + discover workflows |
| Structure | Flat catalog per toolkit | Typed **Capability Graph** (entities, OWNS/PRECEDES, workflows) |
| Evidence | Asserted; no public ablation | **Measured** (2×2 ablation harness + verification score) |
| Model | Breadth: pre-built connectors, hosted | Depth: optimize **any** OpenAPI spec, self-hostable |

Based on the SDK + docs reviewed, Composio exposes tools **~1:1 with API endpoints** and manages
them via search/filter/modifiers. We found **no semantic endpoint-merging, no capability graph,
and no discovered multi-step workflows**. That whitespace is SYNAPSE's moat.

**What to adopt from them (engineering, not research):**
- `json-schema-to-zod` — a dedicated, battle-tested converter; replace SYNAPSE's hand-rolled Zod.
- **Provider/adapter pattern** (OpenAI/Anthropic/LangChain/CrewAI/Vercel/LlamaIndex/…) — validates
  the multi-target synthesizer; consider emitting framework-native tool defs, not only MCP.
- **Tool modifiers** (schema / before- / after-execution hooks) — clean model for the curation layer.
- **Session + auth model** (Auth Config → Connected Account `ca_` → Connect Link → managed OAuth +
  refresh + multi-account) — a complete reference design for Phase 10, atop the existing AES-256
  `auth_credentials_encrypted` bones.
- **Workbench** (session-scoped sandbox so large tool responses don't bloat context) and
  **multi-execute** (parallel calls) — orthogonal to compression; good harness/synthesizer features.

---

## 2. Positioning (north star)

> **SYNAPSE is the science and system of optimal agent tool-surface design — build-time,
> measurable, and capability-graph-grounded.**

Don't try to out-platform a funded incumbent (Phase 10 is the weakest place to fight). Win on the
**science** Composio structurally lacks: semantic compression, the capability graph, workflow
discovery, and — above all — a **benchmark that measures it**.

---

## 3. Four novel research bets

Each: the idea, why it's novel (vs Composio + literature), what it builds on, and the measurable
result. Ordered by recommended execution sequence; they compound.

### Bet ③ — Capability Graph as an *active reasoning structure* *(do first: cheap, fixes a measured bug)*
Use the graph's `OWNS`/`PRECEDES` edges to **validate and repair agent plans**. The precondition
"an entity must be created before it can be read/updated/deleted" is already encoded in the graph —
so a plan that references an un-created entity is detectably wrong.
- **Why novel:** treats the API as a typed graph to *guide the agent*. Composio has a flat catalog
  with no structure to reason over; runtime search can't enforce ordering.
- **Builds on:** the Neo4j Capability Graph (CG = V,E,L,P,W), workflow nodes, the verifier.
- **Directly fixes:** the `404 /pets/pets_1` **id-fabrication** failure that cost 3/8 tasks in the
  validation run (agent invented an ID instead of chaining from a create).
- **Measurable result:** lower selection-error / id-fabrication rate vs naive; a graph-grounded
  guardrail that beats the baseline. Strong before/after demo.

### Bet ② — Merge-recoverability guarantee + compression↔correctness Pareto frontier *(rigor)*
Formalize **"no capability is lost under compression"** as a measurable (ideally provable)
property; sweep merge aggressiveness (`compression.py` `_JACCARD_THRESHOLD`,
`cluster_selection_epsilon`) and plot the frontier; replace the hard-coded `0.4` with the
*measured* optimum.
- **Why novel:** a **correctness guarantee for tool compression**. Composio never merges, so never
  needs this. Clean formalism + a single compelling figure — ideal for a research venue.
- **Builds on:** the verification layer (the seed — it round-trips every tool), `compression.py`.
- **Measurable result:** a Pareto plot (compression ratio vs agent-recoverable capability), a
  principled merge criterion, and a per-API synthesis-correctness score.

### Bet ① — Usage-driven (task-aware) compression: a closed loop *(headline novelty)*
Mine eval transcripts (`llm_call_log` + recorded runs) for tool co-occurrence, call sequences, and
failure modes; feed that back as a re-clustering / workflow signal behind a new `PipelineConfig`
flag; re-synthesize; re-measure.
- **Why novel:** nobody **learns the tool surface from agent behavior**. Composio's surface is
  static-per-toolkit + runtime-filtered — it never *redesigns itself* from usage. This is "tool
  surfaces that adapt to how agents actually use the API."
- **Builds on:** Phase 7, the transcript recorder, the 2×2 harness, Bet ②'s metric to evaluate it.
- **Measurable result:** usage-aware vs structural-only ablation — a measured win (or a documented
  null). **This is the paper's punchline.**

### Bet ④ — Compression as a *preprocessor for runtime routing* *(engages Composio head-on)*
Measure agent success/tokens when **searching/selecting over 92 raw endpoints vs 17 SYNAPSE-
compressed tools** (a Composio-style meta-tool/search setup over each surface).
- **Why novel:** reframes build-time vs runtime as build-time **for** runtime. If compression
  improves a runtime-discovery agent, SYNAPSE *strengthens the dominant paradigm* rather than
  competing with it.
- **Builds on:** the harness conditions; a thin "search-then-execute" agent variant.
- **Measurable result:** a "we improve the incumbent approach" result — strong paper + product story.

---

## 4. The paper through-line

> *Tool-set design is a measurable, optimizable axis of agent performance — and a learned,
> capability-graph-grounded tool surface beats both naive endpoints and runtime search.*

A thesis Composio cannot claim (no measurement, no graph, no learning) and the SYNAPSE harness can
actually prove. Mapping to sections:
- **Motivation:** the ~55K-token tool-surface tax; production systems (Composio) re-architected around it.
- **Method:** Capability Graph → semantic compression → workflow discovery → synthesized MCP server.
- **Contributions:** (i) the compression↔correctness frontier + recoverability guarantee [②];
  (ii) usage-driven, self-improving compression [①]; (iii) graph-grounded plan validity [③];
  (iv) the ablation **benchmark** itself (Phase 8) — a community artifact Composio lacks.
- **Related work:** Composio (runtime selection) vs SYNAPSE (build-time optimization) — complementary,
  shown by [④].

---

## 5. Prioritized execution (FYP-timeline aware)

| Order | Bet | Why first | Rough effort |
|------:|-----|-----------|--------------|
| 1 | ③ graph-grounded plan repair | cheapest; fixes a failure we already have data on; killer demo | small |
| 2 | ② recoverability + Pareto | paper rigor + one clean figure; extends the existing verifier | small–med |
| 3 | ① usage-driven loop | the novel headline; needs ②'s metric to evaluate | medium |
| 4 | ④ compression-for-routing | nearly free once harness conditions are wired | small |

Foundation already in place: funded OpenAI key, the live 2×2 harness, the verification layer, and a
first validation result (harness proven; thesis not yet — success tied at 62%, ~8% token win). These
bets are the path from "machine works" to "thesis proven."

---

## 6. Engineering adoptions (parallel, low-risk)
- Swap hand-rolled Zod for a `json-schema-to-zod`-style converter in the TS template.
- Add framework-native synthesis targets behind the existing multi-target synthesizer.
- Generalize the curation layer along Composio's schema/before/after **modifier** model.
- Reuse Composio's **session + auth** shape as the Phase 10 reference design.
