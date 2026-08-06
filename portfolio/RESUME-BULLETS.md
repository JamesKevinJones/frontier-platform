# Resume bullets — Ace Team / Frontier Engineer aligned

Use 2 bullets per project max. Quantify where you can.

## Platform story (lead with this in interviews)

Built a **three-app local AI platform** where GroundedCorp (RAG) and AgentForge (multi-agent) are both governed by a shared GuardRailOps validation layer — same injection/PII/toxicity/groundedness checks, offline-first, CI quality gate.

## GroundedCorp — Enterprise RAG Knowledge Assistant

- Shipped a **production-shaped RAG pipeline** (ingest → chunk → embed → hybrid retrieve → grounded generate) with **citations**, **grounding refusal**, **shared input/output guardrails**, and a **pluggable LLM provider** (mock default; Ollama / OpenAI-compatible via env).
- Built an offline **eval harness** with citation-hit rate, **faithfulness proxy** (answer–evidence overlap), per-question latency, and **SQLite eval history** for regression tracking — FastAPI + React + vector store.

## AgentForge — Multi-Agent Enterprise Workflow

- Orchestrated a **LangGraph-style multi-agent workflow** (triage → research → policy → resolver → critic) with tool calls, critic repair loops, **cost-budget escalation**, and **append-only JSONL run persistence** for replay (`GET /runs`).
- Applied the **same shared guardrails** on ticket input and resolution output; instrumented per-step latency/cost trajectories (AgentOps-style) — FastAPI + React, local-first.

## GuardRailOps — AI Evaluation, Guardrails & CI Quality Gates

- Implemented **defense-in-depth guardrails** as a **reusable shared library** consumed by the other two apps (not only a standalone API): prompt-injection heuristics, PII redaction, toxicity checks, groundedness threshold.
- Added **OTel-style** in-process spans/metrics plus a **Prometheus text endpoint**, golden-set eval with pass-rate gate, and a **GitHub Actions quality gate** that fails on regression.

## Skills line

`Python · FastAPI · React · TypeScript · SQL · RAG · Hybrid retrieval · Pluggable LLM providers · LangGraph-style multi-agent · Guardrails · Responsible AI · Eval harness · Prometheus · CI/CD · Docker · Git`
