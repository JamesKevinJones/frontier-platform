# AgentForge — multi-agent ticket resolution

Five specialist agents on an explicit state graph — the pattern LangGraph implements,
written out so the production-relevant decisions are visible and testable rather
than framework-internal.

```
START → triage → research → policy → resolver → critic ─┬→ END
                              ▲                          │
                              └────── send_back ─────────┘
                    budget interrupt ────────────────────→ END (needs_human)
```

## Why five agents rather than one

| Node | Job | Why it is separate |
|---|---|---|
| `triage` | Classify category and severity | Severity drives every later policy decision |
| `research` | Call tools, gather evidence | Gathers facts, decides nothing |
| `policy` | Apply governance constraints | Independent of the fix, so a breach can't be argued away by a persuasive plan |
| `resolver` | Synthesise a remediation plan | The only node that proposes |
| `critic` | Adversarially check the plan | A single-pass agent has no mechanism to catch its own omission |

## Guarantees

- **Bounded.** Cycles capped by `max_loops`, plus a hard `MAX_GRAPH_STEPS` ceiling.
  Every run terminates.
- **Interruptible.** The cost budget is checked *between* nodes, so a node always
  completes atomically and state is never left half-written. Exceeding it stops the
  graph and escalates to a human with named decision points.
- **Resilient.** Tools have timeouts, retry only transient failures with backoff and
  jitter, and trip a circuit breaker so a dead dependency fails fast rather than
  burning the budget rediscovering that it is dead.
- **Replayable.** Append-only JSONL per ticket plus per-node checkpoints. The
  interesting failures are runs that looped, revised and then looked fine — an
  overwritten record hides exactly what you need to debug.
- **Guarded.** Shared guardrails run on the ticket before any agent sees it and on
  the resolution before any human does.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/run` | Execute the graph over a ticket |
| `POST` | `/run/stream` | SSE: one message per completed node |
| `GET` | `/graph` | Machine-readable topology; the console draws the diagram from this |
| `GET` | `/tools` | Tool manifest in OpenAI/Anthropic function-calling format |
| `GET` | `/runs/{id}/replay` | Every persisted attempt plus checkpoints |
| `GET` | `/runs/stats` | Fleet view: resolved rate, human rate, average cost |

## Run

```bash
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m app.smoke      # happy path, SEV-1, injection block, budget interrupt
python -m pytest -q      # 19 tests

python -m uvicorn app.main:app --reload --port 8002
```

`MAX_COST_UNITS` (default `0.5`) sets the per-run budget; `MAX_GRAPH_STEPS`
(default `24`) is the termination ceiling.
