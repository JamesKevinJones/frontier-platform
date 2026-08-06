# GuardRailOps — the governance plane

Ships `shared/guardrails`, the library **imported** by GroundedCorp and AgentForge —
not called over HTTP. A governance control behind a network hop is a control that
fails open the moment the network does.

Also owns evaluation, red teaming, drift detection and the CI quality gate.

## Controls

| Rule | Severity | Action | Direction |
|---|---|---|---|
| `prompt_injection` | high | block | **input only** |
| `secret` | critical | block + redact | both |
| `pii` | medium | **redact + allow** | both |
| `toxicity` | medium | flag + allow | both |
| `groundedness` | high | block | output |

Three decisions worth defending:

- **Injection rules are input-only.** An answer that *discusses* prompt injection is
  not an attack. Scoping the rule by direction is what keeps the false positive rate
  at zero on the red-team suite.
- **PII redacts but does not block.** Blocking every email address makes the
  assistant useless. Redacting keeps it usable and safe.
- **Checksums before belief.** Credit-card candidates are Luhn-validated and Aadhaar
  numbers Verhoeff-validated, so invoice and order numbers do not get shredded. A
  guardrail that cries wolf gets switched off.

Severity → action is defined in [`policy.json`](../shared/guardrails/policy.json),
so changing what blocks is a reviewable config change rather than a code change.

## Scored as a confusion matrix

A suite that only tests attacks can be passed perfectly by blocking everything, so
the golden set is balanced and both directions are gated:

|  | Guardrail fired | Guardrail allowed |
|---|---:|---:|
| **Actually unsafe** | 11 caught | 0 missed |
| **Actually safe** | 0 wrongly blocked | 12 passed |

| Metric | Result | Gate |
|---|---:|---:|
| Pass rate | 1.00 | ≥ 0.90 |
| Recall | 1.00 | ≥ 0.90 |
| Precision | 1.00 | ≥ 0.80 |
| False positive rate | 0.00 | ≤ 0.10 |

`max_false_positive_rate` is the threshold nobody adds until a guardrail has annoyed
a business unit into disabling it.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/validate` | Run the full chain over one piece of text |
| `POST` | `/validate/batch` | Bulk validation, for a nightly content scan |
| `GET` | `/rules` | Every control, its severity and its action |
| `GET` | `/policy` · `POST` `/policy/reload` | The active policy as auditable data |
| `POST` | `/eval/run` | Golden + red-team suite with confusion matrix |
| `POST` | `/eval/baseline` | Freeze current metrics as the drift baseline |
| `GET` | `/eval/drift` | Compare the latest run against that baseline |
| `GET` | `/metrics/prometheus` | Prometheus exposition |

## Run

```bash
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m app.smoke      # injection, PII, secrets, grounding, false-positive probe
python -m app.gate       # the CI gate + drift check
python -m pytest -q      # 32 tests

python -m uvicorn app.main:app --reload --port 8003
```
