from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    sev1 = "SEV-1"
    sev2 = "SEV-2"
    sev3 = "SEV-3"
    sev4 = "SEV-4"


RunStatus = Literal["running", "resolved", "needs_human", "rejected", "failed"]


class TicketIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=5000)
    requester: str = "employee@example.com"
    max_cost_units: float | None = Field(
        default=None, description="Override the per-run cost budget."
    )
    max_loops: int = Field(default=2, ge=0, le=5)


class ToolCall(BaseModel):
    tool: str
    ok: bool
    latency_ms: int
    attempts: int
    cost_units: float
    error: str | None = None
    summary: str = ""


class AgentEvent(BaseModel):
    agent: str
    action: str
    detail: str
    latency_ms: int
    cost_units: float = 0.0
    span_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class GuardFindingOut(BaseModel):
    rule: str
    severity: str
    message: str
    span: str | None = None


class HumanReview(BaseModel):
    """Why a run stopped for a person, and what that person needs to decide."""

    required: bool = False
    reason: str | None = None
    escalate_to: str | None = None
    sla_minutes: int | None = None
    decision_points: list[str] = Field(default_factory=list)


class RunResult(BaseModel):
    ticket_id: str
    category: str
    severity: Severity
    status: RunStatus
    resolution: str
    policy_flags: list[str]
    trajectory: list[AgentEvent]
    total_latency_ms: int
    total_cost_units: float
    loops: int
    guarded: bool = True
    guard_findings: list[GuardFindingOut] = Field(default_factory=list)
    budget_exceeded: bool = False
    max_cost_units: float | None = None
    trace_id: str | None = None
    created_at: str | None = None
    confidence: float = 0.0
    human_review: HumanReview = Field(default_factory=HumanReview)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    path: list[str] = Field(default_factory=list)


class GraphState(BaseModel):
    ticket_id: str
    title: str
    description: str
    requester: str
    category: str = ""
    severity: Severity = Severity.sev3
    research_notes: list[str] = Field(default_factory=list)
    policy_flags: list[str] = Field(default_factory=list)
    resolution: str = ""
    critique: str = ""
    status: RunStatus = "running"
    trajectory: list[AgentEvent] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    loops: int = 0
    confidence: float = 0.0
    path: list[str] = Field(default_factory=list)
    human_review: HumanReview = Field(default_factory=HumanReview)
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def spent_cost_units(self) -> float:
        return round(sum(e.cost_units for e in self.trajectory), 6)

    @property
    def spent_latency_ms(self) -> int:
        return sum(e.latency_ms for e in self.trajectory)


class GraphSpec(BaseModel):
    nodes: list[dict]
    edges: list[dict]
    tools: list[dict]
    entry_point: str
    max_cost_units: float
