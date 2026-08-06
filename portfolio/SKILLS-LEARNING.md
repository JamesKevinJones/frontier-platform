# Skills learning map — Ace Team Frontier Engineer

Study in this order. Each skill maps to a **project file you can open and run** so learning sticks for the resume *and* the Aug 18 assessment.

## Track A — Assessment first (through Aug 18)

Follow [`assessment-prep/schedule/DAILY.md`](../assessment-prep/schedule/DAILY.md). Portfolio ≤45 min/day via [`PARALLEL.md`](./PARALLEL.md).

| Skill | Learn here | Practice here |
|-------|------------|---------------|
| Cloud / Azure AI Foundry / Vertex / OTel | `assessment-prep/mcq/01-cloud-observability.md` | GuardRailOps `app/telemetry.py` |
| LLMs, RAG, agents, context eng | `assessment-prep/mcq/02-genai-agentic.md` | GroundedCorp + AgentForge |
| Guardrails / Responsible AI | `assessment-prep/mcq/03-governance.md` | GuardRailOps `app/guards.py` |
| Java DSA | `assessment-prep/dsa-java/` | Assessment only |
| React | `assessment-prep/react-practice/` | Any `frontend/` |
| Prompt eng | `assessment-prep/prompting/` | GroundedCorp `app/llm.py` |
| RAG pipeline | `assessment-prep/rag-practice/` | GroundedCorp full stack |

## Track B — Portfolio deep dive (Aug 19+)

### 1. RAG & knowledge engineering → GroundedCorp

1. Read `01-groundedcorp/app/ingest.py` (chunk + metadata).
2. Read `embeddings.py` + `rag.py` (hybrid retrieve, grounding threshold).
3. Run `python -m app.smoke` then `POST /eval/run`.
4. Add your own corpus doc; re-ingest; prove a new golden question.

### 2. Multi-agent systems → AgentForge

1. Sketch the graph in `02-agentforge/app/graph.py`.
2. Trace each agent in `agents.py` and tools in `tools.py`.
3. Run sample tickets; explain trajectory + cost in an interview.
4. Optional stretch: swap mock tools for real HTTP APIs.

### 3. AI Eval & Ops → GuardRailOps

1. Walk `guards.py` rules (injection, PII, toxicity, groundedness).
2. Inspect spans/metrics in `telemetry.py`.
3. Break a golden case; watch the CI gate fail (`ai-quality-gate.yml`).
4. Wire `/validate` in front of GroundedCorp `/ask` as a stretch.

## Interview one-liners

- **GroundedCorp:** “I built citation-first RAG that refuses when grounding is weak, with an offline eval harness.”
- **AgentForge:** “I orchestrated a multi-agent ticket workflow with tool use, policy checks, critic repair loops, and cost/latency traces.”
- **GuardRailOps:** “I packaged guardrails + OTel-style telemetry into a CI quality gate so regressions fail the pipeline.”
