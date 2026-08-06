# Frontier Platform — services

Three services and one shared spine. See the [root README](../README.md) for the
full walkthrough, metrics and architecture.

```
shared/                 imported by every service
├─ guardrails/          injection · secrets · PII · toxicity · grounding + policy.json
├─ observability/       W3C trace context, Prometheus exposition, ASGI middleware
└─ llm/                 providers · retries · cost model · two-tier cache

01-groundedcorp/        RAG — retrieval.py is the context-engineering core
02-agentforge/          agents — graph.py is the state machine
03-guardrailops/        governance — eval_runner.py is the gate
console/                React + TypeScript operator console
tests/                  shared-layer contract tests
```

## Run everything

```bash
docker compose up --build
```

| | |
|---|---|
| Console | http://localhost:5173 |
| GroundedCorp | http://localhost:8001/docs |
| AgentForge | http://localhost:8002/docs |
| GuardRailOps | http://localhost:8003/docs |

All services default to a deterministic mock provider, so the stack runs and the
evaluation suites pass with no API key and no network.

## Run the gates locally

```bash
python -m pytest tests -q                              # shared contracts
cd 01-groundedcorp && python -m app.gate               # RAG quality gate
cd ../03-guardrailops && python -m app.gate            # guardrail + drift gate
```

## Why `shared/` exists

Both applications import the same `validate()`. That is the point of the platform:
one place where governance is defined, one place to audit, one place to change.
GroundedCorp calls it on every question and every answer; AgentForge calls it on
every ticket and every resolution; GuardRailOps evaluates it and gates the build on
the result.
