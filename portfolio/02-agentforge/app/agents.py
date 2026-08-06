"""
Specialist agents. Each is a pure function ``GraphState -> GraphState``.

Purity is the design choice that makes the rest of the system possible: a node
that only reads and returns state can be unit-tested in isolation, replayed from
a persisted trajectory, and reordered in the graph without hidden coupling. All
side effects — telemetry, persistence, budget enforcement — live in the engine.

The pod is deliberately specialised rather than one do-everything agent:

    triage    classify and set severity, because severity drives every later policy
    research  gather evidence via tools, never decide anything
    policy    apply governance constraints, independent of the proposed fix
    resolver  synthesise a plan from evidence + constraints
    critic    adversarially check the plan against severity and policy, and either
              accept, send back for one repair loop, or escalate to a human

The critic exists because a single-pass agent has no mechanism to catch its own
omission. Separating "propose" from "check" is what makes the repair loop possible.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

from app.models import AgentEvent, GraphState, HumanReview, Severity, ToolCall
from app.tools import call_tool

_PORTFOLIO = Path(__file__).resolve().parents[2]
if str(_PORTFOLIO) not in sys.path:
    sys.path.insert(0, str(_PORTFOLIO))

from shared.observability import get_tracer  # noqa: E402

tracer = get_tracer("agentforge")


def _event(
    agent: str,
    action: str,
    detail: str,
    ms: int,
    cost: float = 0.01,
    tool_calls: list[ToolCall] | None = None,
    span_id: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        agent=agent,
        action=action,
        detail=detail,
        latency_ms=max(ms, 1),
        cost_units=cost,
        tool_calls=tool_calls or [],
        span_id=span_id,
    )


def _record(state: GraphState, result) -> ToolCall:
    """Fold a ToolResult into both the state's tool ledger and telemetry."""
    call = ToolCall(
        tool=result.name,
        ok=result.ok,
        latency_ms=result.latency_ms,
        attempts=result.attempts,
        cost_units=result.cost_units,
        error=result.error,
        summary=str(result.value)[:180] if result.ok else (result.error or ""),
    )
    state.tool_calls.append(call)
    tracer.incr(f"tool.{result.name}.{'ok' if result.ok else 'error'}")
    tracer.observe(f"tool.{result.name}.latency_ms", result.latency_ms)
    return call


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

# Ordered most-severe first: the first match wins, so "customer data breach"
# cannot be downgraded to a generic access request by a later rule.
TRIAGE_RULES: list[tuple[str, Severity, tuple[str, ...]]] = [
    ("security", Severity.sev1, ("breach", "ransomware", "customer data", "exfil", "data leak")),
    ("security", Severity.sev2, ("phish", "credential", "compromised", "malware", "unauthorized")),
    ("network_identity", Severity.sev2, ("vpn", "outage", "down", "cannot login", "mfa", "sso")),
    ("endpoint", Severity.sev2, ("laptop", "device", "stolen", "lost")),
    ("data", Severity.sev2, ("database", "replication", "corruption")),
    ("access", Severity.sev3, ("access", "permission", "role", "privilege")),
]


def triage_agent(state: GraphState) -> GraphState:
    with tracer.span("agent.triage") as span:
        t0 = time.perf_counter()
        text = f"{state.title} {state.description}".lower()

        category, severity = "general", Severity.sev4
        matched: tuple[str, ...] = ()
        for cat, sev, keywords in TRIAGE_RULES:
            hits = tuple(w for w in keywords if w in text)
            if hits:
                category, severity, matched = cat, sev, hits
                break

        state.category = category
        state.severity = severity
        # Confidence in the classification feeds the critic's escalation decision.
        state.confidence = round(min(0.4 + 0.2 * len(matched), 0.95), 3)
        span.set_attribute("category", category)
        span.set_attribute("severity", severity.value)
        span.set_attribute("signals", list(matched))
        tracer.incr(f"triage.{category}")

        ms = int((time.perf_counter() - t0) * 1000)
        state.trajectory.append(
            _event(
                "triage",
                "classify",
                f"category={category} severity={severity.value} signals={list(matched) or 'none'}",
                ms,
                0.02,
                span_id=span.span_id,
            )
        )
        state.path.append("triage")
    return state


def research_agent(state: GraphState) -> GraphState:
    """Gather evidence. Chooses which tools to call from the triage category —
    this is the 'planner' step a real deployment would hand to an LLM."""
    with tracer.span("agent.research") as span:
        t0 = time.perf_counter()
        text = f"{state.title} {state.description}".lower()
        notes: list[str] = []
        calls: list[ToolCall] = []

        kb = call_tool("search_kb", query=f"{state.title} {state.description} {state.category}")
        calls.append(_record(state, kb))
        if kb.ok:
            notes.append(f"KB: {kb.value}")

        runbook = call_tool("fetch_runbook", category=state.category)
        calls.append(_record(state, runbook))
        if runbook.ok:
            notes.append(f"Runbook: {runbook.value}")

        # Only touch the CMDB for symptoms that name infrastructure, and look up
        # each configuration item once — "MFA fails on login" must not bill two
        # identical calls to the identity provider.
        wanted: list[str] = []
        for keyword, ci in (
            ("vpn", "vpn-gw-01"),
            ("mfa", "idp-okta"),
            ("login", "idp-okta"),
            ("sso", "idp-okta"),
            ("phish", "mail-gateway"),
            ("database", "db-primary-01"),
        ):
            if keyword in text and ci not in wanted:
                wanted.append(ci)

        for ci in wanted:
            result = call_tool("lookup_cmdb", ci=ci)
            calls.append(_record(state, result))
            if result.ok:
                notes.append(f"CMDB {ci}: {result.value}")

        failed = [c for c in calls if not c.ok]
        if failed:
            # Degraded evidence is a fact the critic must weigh, not a crash.
            state.confidence = round(state.confidence * 0.7, 3)
            notes.append(f"NOTE: {len(failed)} tool call(s) failed: {[c.tool for c in failed]}")

        state.research_notes = notes
        span.set_attribute("notes", len(notes))
        span.set_attribute("tool_calls", len(calls))
        span.set_attribute("tool_failures", len(failed))

        ms = int((time.perf_counter() - t0) * 1000)
        state.trajectory.append(
            _event(
                "research",
                "gather_evidence",
                f"{len(notes)} notes from {len(calls)} tool calls ({len(failed)} failed)",
                ms,
                round(sum(c.cost_units for c in calls), 6),
                tool_calls=calls,
                span_id=span.span_id,
            )
        )
        state.path.append("research")
    return state


def policy_agent(state: GraphState) -> GraphState:
    """Apply governance constraints. Independent of the proposed remediation, so a
    policy breach cannot be argued away by a persuasive resolution."""
    with tracer.span("agent.policy") as span:
        t0 = time.perf_counter()
        flags: list[str] = []
        text = f"{state.title} {state.description}".lower()

        if state.severity in (Severity.sev1, Severity.sev2):
            flags.append("require_incident_channel")
        if "prod" in text or "production" in text:
            flags.append("change_freeze_check")
        if "customer" in text or "pii" in text or "personal data" in text:
            flags.append("privacy_legal_notify")
        if "wipe" in text or "stolen" in text or "lost" in text:
            flags.append("device_wipe_approval")
        if state.severity == Severity.sev1:
            flags.append("executive_notification")
        if "payment" in text or "card" in text:
            flags.append("pci_scope_review")
        if not flags:
            flags.append("standard_sla")

        state.policy_flags = flags
        span.set_attribute("flags", flags)

        ms = int((time.perf_counter() - t0) * 1000)
        state.trajectory.append(
            _event("policy", "apply_constraints", ", ".join(flags), ms, 0.015, span_id=span.span_id)
        )
        state.path.append("policy")
    return state


RESOLUTION_PLAYBOOK = {
    "security": "Open a security incident bridge, preserve evidence, rotate affected credentials, and notify the privacy office.",
    "network_identity": "Validate IdP and VPN gateway health, confirm certificate validity, and publish an ETA to #it-incidents.",
    "endpoint": "Mark the device lost or stolen in MDM, revoke its tokens and certificates, and issue a loaner after identity proof.",
    "data": "Check replication lag and recent migrations, take a consistent snapshot before any repair, and engage the data on-call.",
    "access": "Verify manager approval, grant least-privilege access through PAM with an expiry, and record the recertification date.",
}


def resolver_agent(state: GraphState) -> GraphState:
    with tracer.span("agent.resolver", loop=state.loops) as span:
        t0 = time.perf_counter()
        steps = [
            f"Acknowledge the ticket as {state.severity.value} / {state.category} and notify the requester.",
            *state.research_notes[:3],
            f"Honour policy flags: {', '.join(state.policy_flags)}.",
            RESOLUTION_PLAYBOOK.get(
                state.category, "Route to the tier-2 queue with the research pack attached."
            ),
        ]
        if "privacy_legal_notify" in state.policy_flags:
            steps.append("Notify privacy and legal counsel; log the notification timestamp.")
        if "executive_notification" in state.policy_flags:
            steps.append("Send an executive notification within 30 minutes of declaration.")
        if state.loops > 0 and state.critique:
            steps.append(f"Address critic feedback: {state.critique}")

        state.resolution = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
        span.set_attribute("steps", len(steps))

        ms = int((time.perf_counter() - t0) * 1000)
        state.trajectory.append(
            _event(
                "resolver",
                "propose" if state.loops == 0 else "revise",
                f"{len(steps)}-step plan ({len(state.resolution)} chars)",
                ms,
                0.03,
                span_id=span.span_id,
            )
        )
        state.path.append("resolver")
    return state


def critic_agent(state: GraphState, max_loops: int = 1) -> GraphState:
    """Adversarial check. Returns one of three routing decisions on ``state.status``."""
    with tracer.span("agent.critic", loop=state.loops) as span:
        t0 = time.perf_counter()
        resolution = state.resolution.lower()
        issues: list[str] = []

        if state.severity == Severity.sev1 and "incident bridge" not in resolution:
            issues.append("SEV-1 resolution must open a security incident bridge")
        if "privacy_legal_notify" in state.policy_flags and not any(
            w in resolution for w in ("privacy", "legal")
        ):
            issues.append("privacy_legal_notify flag set but no notification step present")
        if "executive_notification" in state.policy_flags and "executive" not in resolution:
            issues.append("executive_notification flag set but no executive step present")
        if not state.research_notes:
            issues.append("no research evidence gathered")
        if state.confidence < 0.45:
            issues.append(f"triage confidence {state.confidence} below 0.45")

        span.set_attribute("issues", issues)

        if issues and state.loops < max_loops:
            # One repair loop. Unbounded self-correction is how agents burn budget.
            state.critique = "; ".join(issues)
            state.loops += 1
            state.status = "running"
            action, detail = "send_back", state.critique
        elif issues:
            state.status = "needs_human"
            state.human_review = HumanReview(
                required=True,
                reason="critic_unresolved_issues",
                escalate_to="incident-manager" if state.severity == Severity.sev1 else "tier-2",
                sla_minutes=15 if state.severity == Severity.sev1 else 60,
                decision_points=issues,
            )
            action, detail = "escalate_human", "; ".join(issues)
            tracer.incr("critic.escalated")
        else:
            state.status = "resolved"
            state.confidence = round(min(state.confidence + 0.1, 0.99), 3)
            action, detail = "accept", "resolution satisfies severity and policy checks"
            tracer.incr("critic.accepted")

        ms = int((time.perf_counter() - t0) * 1000)
        state.trajectory.append(
            _event("critic", action, detail, ms, 0.02, span_id=span.span_id)
        )
        state.path.append("critic")
    return state


def new_ticket_state(title: str, description: str, requester: str) -> GraphState:
    return GraphState(
        ticket_id=str(uuid.uuid4())[:8],
        title=title,
        description=description,
        requester=requester,
    )
