"""GuardRailOps API — validation, evaluation, telemetry and CI quality gates."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.eval_runner import (
    compare_to_baseline,
    load_baseline,
    run_eval,
    save_baseline,
)
from app.guards import validate
from app.telemetry import telemetry

_PORTFOLIO = Path(__file__).resolve().parents[2]
if str(_PORTFOLIO) not in sys.path:
    sys.path.insert(0, str(_PORTFOLIO))

from shared.guardrails import active_policy, reload_policy  # noqa: E402
from shared.observability import current_trace_id, install_observability  # noqa: E402

app = FastAPI(
    title="GuardRailOps",
    version="2.0.0",
    description=(
        "The governance plane for the platform. Ships the shared guardrail library "
        "consumed by GroundedCorp and AgentForge, plus evaluation, drift detection, "
        "OpenTelemetry-shaped traces and a CI quality gate."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id", "traceparent"],
)
install_observability(app, telemetry)


class ValidateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    direction: str = Field(default="output", pattern="^(input|output)$")
    grounding_score: float | None = None


class BatchValidateRequest(BaseModel):
    items: list[ValidateRequest] = Field(min_length=1, max_length=100)


@app.get("/health", tags=["ops"])
def health():
    policy = active_policy()
    return {
        "status": "ok",
        "service": "guardrailops",
        "version": app.version,
        "policy": {
            "grounding_threshold": policy.grounding_threshold,
            "blocking_severities": list(policy.blocking_severities),
            "redact_pii": policy.redact_pii,
            "check_secrets": policy.check_secrets,
            "check_toxicity": policy.check_toxicity,
        },
    }


@app.post("/validate", tags=["guardrails"])
def validate_endpoint(body: ValidateRequest):
    with telemetry.span("http.validate", direction=body.direction) as span:
        result = validate(
            body.text,
            direction=body.direction,
            grounding_score=body.grounding_score,
        )
        span.set_attribute("allowed", result.allowed)
        span.set_attribute("rules", result.rules_triggered())
        telemetry.incr("validate.calls")
        telemetry.incr("validate.blocked" if not result.allowed else "validate.allowed")
        telemetry.observe("validate.findings", len(result.findings))
        telemetry.observe("validate.risk_score", result.risk_score)
        return {**result.to_dict(), "trace_id": current_trace_id()}


@app.post("/validate/batch", tags=["guardrails"])
def validate_batch(body: BatchValidateRequest):
    """Bulk validation — the shape a nightly content-scan job would call."""
    with telemetry.span("http.validate.batch", n=len(body.items)):
        results = [
            validate(
                item.text, direction=item.direction, grounding_score=item.grounding_score
            ).to_dict()
            for item in body.items
        ]
    blocked = sum(1 for r in results if not r["allowed"])
    return {
        "n": len(results),
        "blocked": blocked,
        "block_rate": round(blocked / len(results), 4),
        "results": results,
    }


@app.get("/policy", tags=["governance"])
def get_policy():
    """The active governance policy, as data. Auditable and diffable."""
    policy = active_policy()
    return {
        "grounding_threshold": policy.grounding_threshold,
        "blocking_severities": list(policy.blocking_severities),
        "redact_pii": policy.redact_pii,
        "check_secrets": policy.check_secrets,
        "check_toxicity": policy.check_toxicity,
        "severity_weights": policy.severity_weights,
    }


@app.post("/policy/reload", tags=["governance"])
def policy_reload():
    policy = reload_policy()
    telemetry.incr("policy.reloads")
    return {"reloaded": True, "grounding_threshold": policy.grounding_threshold}


@app.get("/rules", tags=["governance"])
def rules():
    """Every rule the platform enforces, with its severity and action."""
    from shared.guardrails.core import (
        INJECTION_PATTERNS,
        PII_PATTERNS,
        SECRET_PATTERNS,
        TOXIC_TERMS,
    )

    return {
        "prompt_injection": {
            "severity": "high",
            "action": "block",
            "applies_to": "input",
            "pattern_count": len(INJECTION_PATTERNS),
        },
        "secret": {
            "severity": "critical",
            "action": "block + redact",
            "applies_to": "input, output",
            "detectors": sorted(SECRET_PATTERNS),
        },
        "pii": {
            "severity": "medium",
            "action": "redact + allow",
            "applies_to": "input, output",
            "detectors": sorted(PII_PATTERNS),
            "checksum_validated": ["credit_card (Luhn)", "aadhaar (Verhoeff)"],
        },
        "toxicity": {
            "severity": "medium",
            "action": "flag + allow",
            "applies_to": "input, output",
            "term_count": len(TOXIC_TERMS),
        },
        "groundedness": {
            "severity": "high",
            "action": "block",
            "applies_to": "output",
            "threshold": active_policy().grounding_threshold,
        },
    }


@app.post("/eval/run", tags=["evaluation"])
def eval_run(include_redteam: bool = True):
    return run_eval(include_redteam=include_redteam)


@app.get("/eval/baseline", tags=["evaluation"])
def eval_baseline():
    return {"baseline": load_baseline()}


@app.post("/eval/baseline", tags=["evaluation"])
def eval_baseline_save():
    return {"baseline": save_baseline(run_eval())}


@app.get("/eval/drift", tags=["evaluation"])
def eval_drift():
    return compare_to_baseline()


@app.get("/metrics", tags=["ops"])
def metrics():
    return telemetry.snapshot()


@app.get("/metrics/prometheus", tags=["ops"])
def metrics_prometheus():
    return Response(
        content=telemetry.prometheus_text(), media_type="text/plain; version=0.0.4"
    )
