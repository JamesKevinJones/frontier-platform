---
doc_id: eng-ai-usage
title: Generative AI Acceptable Use
dept: engineering
doc_type: policy
version: 1.1
---

Engineers may use approved AI coding assistants for productivity on non-restricted codebases.
Customer PII, regulated data, and unreleased product secrets must not be pasted into public LLM tools.
AI-generated code must be reviewed by a human before merge; unit tests are required for non-trivial changes.
RAG systems grounding on enterprise documents must cite sources and refuse when retrieval confidence is low.
Prompt injection defenses and output filters are mandatory for customer-facing AI features.
Model and prompt versions used in production must be recorded for auditability and rollback.
