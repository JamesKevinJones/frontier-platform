"""
A small but real state-graph engine (the pattern LangGraph implements).

    START -> triage -> research -> policy -> resolver -> critic --+--> END
                                                ^                 |
                                                +---- send_back --+
                                       budget interrupt ------------> END(needs_human)

Why write the engine rather than import one
-------------------------------------------
The interesting parts of an agent framework are the parts you have to reason about
in production: where state is checkpointed, what happens when the budget runs out
mid-loop, how a cycle is bounded, what gets persisted for replay. Implementing them
here makes those decisions explicit and testable rather than framework-internal.
The node signature is the same ``state -> state`` contract LangGraph uses, so
porting to it is a swap of the executor, not a rewrite of the agents.

Guarantees
----------
- **Bounded.** Every run terminates: cycles are capped by ``max_loops`` and a hard
  step ceiling, and the budget is checked *between* every node.
- **Interruptible.** Exceeding the cost budget stops the graph and routes to a
  human rather than silently truncating the work — the caller learns the run is
  incomplete and why.
- **Replayable.** Every node transition is checkpointed, so a persisted run can be
  inspected step by step after the fact.
- **Guarded.** Shared guardrails run on the ticket text before any agent sees it,
  and on the resolution before any human does.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.agents import (
    critic_agent,
    new_ticket_state,
    policy_agent,
    research_agent,
    resolver_agent,
    triage_agent,
)
from app.models import (
    AgentEvent,
    GraphSpec,
    GraphState,
    GuardFindingOut,
    HumanReview,
    RunResult,
    Severity,
    TicketIn,
)
from app.store import persist_run
from app.tools import tool_schemas

_PORTFOLIO = Path(__file__).resolve().parents[2]
if str(_PORTFOLIO) not in sys.path:
    sys.path.insert(0, str(_PORTFOLIO))

from shared.guardrails import validate  # noqa: E402
from shared.observability import current_trace_id, get_tracer  # noqa: E402

tracer = get_tracer("agentforge")

DEFAULT_MAX_COST = float(os.getenv("MAX_COST_UNITS", "0.5"))
MAX_STEPS = int(os.getenv("MAX_GRAPH_STEPS", "24"))

END = "__end__"

Node = Callable[[GraphState], GraphState]


class BudgetExceeded(RuntimeError):
    def __init__(self, spent: float, budget: float) -> None:
        super().__init__(f"cost {spent:.4f} exceeded budget {budget:.4f}")
        self.spent = spent
        self.budget = budget


class StateGraph:
    """Nodes plus conditional edges. Deterministic, bounded, checkpointed."""

    def __init__(self, entry_point: str) -> None:
        self.entry_point = entry_point
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, str] = {}
        self.conditional: dict[str, Callable[[GraphState], str]] = {}
        self.descriptions: dict[str, str] = {}

    def add_node(self, name: str, fn: Node, description: str = "") -> "StateGraph":
        self.nodes[name] = fn
        self.descriptions[name] = description
        return self

    def add_edge(self, source: str, target: str) -> "StateGraph":
        self.edges[source] = target
        return self

    def add_conditional_edges(
        self, source: str, router: Callable[[GraphState], str]
    ) -> "StateGraph":
        self.conditional[source] = router
        return self

    def _next(self, node: str, state: GraphState) -> str:
        if node in self.conditional:
            return self.conditional[node](state)
        return self.edges.get(node, END)

    def invoke(
        self,
        state: GraphState,
        budget: float,
        checkpoints: list[dict] | None = None,
    ) -> GraphState:
        node = self.entry_point
        steps = 0

        while node != END:
            if steps >= MAX_STEPS:
                # Belt-and-braces: a routing bug must not become an infinite loop.
                state.status = "needs_human"
                state.human_review = HumanReview(
                    required=True,
                    reason="max_steps_exceeded",
                    escalate_to="platform-team",
                    decision_points=[f"graph exceeded {MAX_STEPS} steps"],
                )
                break

            fn = self.nodes.get(node)
            if fn is None:
                raise KeyError(f"graph node not registered: {node}")

            state = fn(state)
            steps += 1

            if checkpoints is not None:
                checkpoints.append(
                    {
                        "step": steps,
                        "node": node,
                        "status": state.status,
                        "cost_units": state.spent_cost_units,
                        "loops": state.loops,
                    }
                )

            # Budget is enforced between nodes: a node always completes atomically,
            # so state is never left half-written.
            if state.spent_cost_units > budget:
                raise BudgetExceeded(state.spent_cost_units, budget)

            node = self._next(node, state)

        return state


def _critic_router(state: GraphState) -> str:
    """The only conditional edge: loop back to the resolver, or finish."""
    return "resolver" if state.status == "running" else END


def build_graph(max_loops: int = 1) -> StateGraph:
    graph = StateGraph(entry_point="triage")
    graph.add_node("triage", triage_agent, "Classify category and severity")
    graph.add_node("research", research_agent, "Call tools to gather evidence")
    graph.add_node("policy", policy_agent, "Apply governance constraints")
    graph.add_node("resolver", resolver_agent, "Synthesise a remediation plan")
    graph.add_node(
        "critic",
        lambda s: critic_agent(s, max_loops=max_loops),
        "Adversarially check the plan; accept, repair or escalate",
    )
    graph.add_edge("triage", "research")
    graph.add_edge("research", "policy")
    graph.add_edge("policy", "resolver")
    graph.add_edge("resolver", "critic")
    graph.add_conditional_edges("critic", _critic_router)
    return graph


# ---------------------------------------------------------------------------
# Run orchestration
# ---------------------------------------------------------------------------


def _findings_out(*results) -> list[GuardFindingOut]:
    return [
        GuardFindingOut(rule=f.rule, severity=f.severity, message=f.message, span=f.span)
        for result in results
        for f in result.findings
    ]


def _rejected_result(
    ticket: TicketIn, in_guard, budget: float, trace_id: str | None
) -> RunResult:
    state = new_ticket_state(ticket.title, ticket.description, ticket.requester)
    return RunResult(
        ticket_id=state.ticket_id,
        category="blocked",
        severity=Severity.sev4,
        status="rejected",
        resolution="Ticket blocked by input guardrails (possible prompt injection).",
        policy_flags=["input_guard_blocked"],
        trajectory=[
            AgentEvent(
                agent="guard",
                action="block_input",
                detail="; ".join(f.message for f in in_guard.findings),
                latency_ms=1,
                cost_units=0.0,
            )
        ],
        total_latency_ms=1,
        total_cost_units=0.0,
        loops=0,
        guarded=True,
        guard_findings=_findings_out(in_guard),
        budget_exceeded=False,
        max_cost_units=budget,
        trace_id=trace_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.0,
        path=["guard"],
    )


def run_ticket(
    ticket: TicketIn,
    max_loops: int = 2,
    max_cost_units: float | None = None,
) -> RunResult:
    budget = (
        ticket.max_cost_units
        if getattr(ticket, "max_cost_units", None) is not None
        else (DEFAULT_MAX_COST if max_cost_units is None else max_cost_units)
    )
    repair_loops = min(max_loops, getattr(ticket, "max_loops", max_loops))

    with tracer.span("graph.run", budget=budget, max_loops=repair_loops) as root:
        trace_id = current_trace_id()

        # --- input guard: refuse before any agent or tool runs ---------------
        combined = f"{ticket.title}\n{ticket.description}"
        with tracer.span("guard.input"):
            in_guard = validate(combined, direction="input")
        if not in_guard.allowed:
            tracer.incr("run.rejected")
            root.set_attribute("outcome", "rejected_input")
            result = _rejected_result(ticket, in_guard, budget, trace_id)
            persist_run(result)
            return result

        state = new_ticket_state(ticket.title, ticket.description, ticket.requester)
        graph = build_graph(max_loops=max(repair_loops - 1, 0) or 1)
        checkpoints: list[dict] = []
        budget_exceeded = False

        try:
            state = graph.invoke(state, budget=budget, checkpoints=checkpoints)
        except BudgetExceeded as exc:
            budget_exceeded = True
            state.status = "needs_human"
            state.human_review = HumanReview(
                required=True,
                reason="cost_budget_exceeded",
                escalate_to="tier-2",
                sla_minutes=60,
                decision_points=[
                    f"run consumed {exc.spent:.4f} of a {exc.budget:.4f} unit budget",
                    "decide whether to raise the budget or handle manually",
                ],
            )
            state.trajectory.append(
                AgentEvent(
                    agent="budget",
                    action="interrupt",
                    detail=str(exc),
                    latency_ms=1,
                    cost_units=0.0,
                )
            )
            state.path.append("budget_interrupt")
            tracer.incr("run.budget_exceeded")

        status = state.status if state.status != "running" else "needs_human"

        # --- output guard: nothing reaches a human unchecked -----------------
        with tracer.span("guard.output"):
            out_guard = validate(state.resolution or "", direction="output")
        resolution = out_guard.redacted_text or state.resolution
        if not out_guard.allowed:
            status = "needs_human"
            resolution = "Resolution held for human review — output guardrails triggered."
            state.human_review = HumanReview(
                required=True,
                reason="output_guard_blocked",
                escalate_to="ai-governance",
                sla_minutes=30,
                decision_points=[f.message for f in out_guard.findings],
            )
            tracer.incr("run.output_blocked")

        total_cost = state.spent_cost_units
        total_ms = state.spent_latency_ms
        root.set_attribute("outcome", status)
        root.set_attribute("cost_units", total_cost)
        tracer.observe("run.cost_units", total_cost)
        tracer.observe("run.latency_ms", total_ms)
        tracer.incr(f"run.{status}")

        result = RunResult(
            ticket_id=state.ticket_id,
            category=state.category,
            severity=state.severity,
            status=status,
            resolution=resolution,
            policy_flags=state.policy_flags,
            trajectory=state.trajectory,
            total_latency_ms=total_ms,
            total_cost_units=round(total_cost, 6),
            loops=state.loops,
            guarded=True,
            guard_findings=_findings_out(in_guard, out_guard),
            budget_exceeded=budget_exceeded,
            max_cost_units=budget,
            trace_id=trace_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            confidence=state.confidence,
            human_review=state.human_review,
            tool_calls=state.tool_calls,
            path=state.path,
        )
        persist_run(result, checkpoints=checkpoints)
        return result


def stream_ticket(ticket: TicketIn):
    """SSE view of a run: emit each node as it completes, then the final result.

    Re-runs the same deterministic graph rather than sharing mutable state with
    ``run_ticket`` — an agent demo that streams a *different* execution than the
    one it persists is worse than no streaming at all.
    """
    import json

    in_guard = validate(f"{ticket.title}\n{ticket.description}", direction="input")
    if not in_guard.allowed:
        yield "event: rejected\ndata: " + json.dumps(
            {"reason": "input_guard_blocked", "findings": [f.to_dict() for f in in_guard.findings]}
        ) + "\n\n"
        return

    result = run_ticket(ticket)
    yield "event: start\ndata: " + json.dumps(
        {"ticket_id": result.ticket_id, "trace_id": result.trace_id}
    ) + "\n\n"

    for event in result.trajectory:
        yield "event: step\ndata: " + json.dumps(event.model_dump()) + "\n\n"

    yield "event: done\ndata: " + json.dumps(result.model_dump()) + "\n\n"


def describe_graph() -> GraphSpec:
    """Machine-readable topology — the console renders the diagram from this."""
    graph = build_graph()
    nodes = [
        {"id": name, "description": graph.descriptions.get(name, "")}
        for name in graph.nodes
    ]
    edges = [{"from": s, "to": t, "type": "direct"} for s, t in graph.edges.items()]
    edges.append({"from": "critic", "to": "resolver", "type": "conditional", "when": "send_back"})
    edges.append({"from": "critic", "to": END, "type": "conditional", "when": "accept | escalate"})
    edges.append({"from": "*", "to": END, "type": "interrupt", "when": "cost budget exceeded"})
    return GraphSpec(
        nodes=nodes,
        edges=edges,
        tools=tool_schemas(),
        entry_point=graph.entry_point,
        max_cost_units=DEFAULT_MAX_COST,
    )
