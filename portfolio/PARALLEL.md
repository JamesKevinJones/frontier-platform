# Parallel track — portfolio hooks into assessment days

Add these **after** the core assessment tasks in `assessment-prep/schedule/DAILY.md`. Cap at **45 minutes**. Skip entirely if behind on assessment.

| Day | Assessment focus (existing) | Portfolio add-on (≤45 min) |
|-----|----------------------------|---------------------------|
| 1 | Cloud/observability MCQ | Skim `portfolio/README.md` + skill matrix |
| 2 | GenAI/agentic MCQ | Run GroundedCorp smoke: `python -m app.smoke` |
| 3 | Governance MCQ | Run GuardRailOps unit tests; read guardrail types |
| 4–6 | Java DSA deep | P1: add 1–2 sample policy docs to `data/corpus` |
| 7–9 | React + prompting | P1 frontend: hit `/ask` from React page |
| 10–11 | RAG practice | Map assessment RAG ↔ GroundedCorp pipeline files |
| 12–14 | Mock #1 + repair | P3: confirm GitHub Actions workflow YAML parses |
| 15–18 | Mock #2 + exam | **No portfolio** |
| 19+ | — | Finish AgentForge graph, polish demos, publish GitHub |

## Learning order (skills → project feature)

1. Embeddings + chunking → GroundedCorp ingest  
2. Hybrid retrieval + citations → GroundedCorp ask  
3. Groundedness / refusal → GroundedCorp + GuardRailOps  
4. Agent loop / tools → AgentForge  
5. Traces / cost metrics / CI gates → GuardRailOps + AgentForge telemetry  
