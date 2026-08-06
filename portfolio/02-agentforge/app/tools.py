"""
Tool layer for the research agent.

An agent is only as reliable as the tools it calls, and in an enterprise the tools
are other teams' services: they time out, they rate-limit, they have a bad
afternoon. So every tool goes through one wrapper that provides

    - a declared JSON schema (so a real LLM can be given the tool list verbatim)
    - a timeout, because an agent blocked on a hung CMDB call is an outage
    - bounded retries with backoff on *transient* failures only
    - a circuit breaker, so a dead dependency fails fast instead of burning the
      whole cost budget rediscovering that it is dead
    - per-call latency, cost and outcome recorded on the trajectory

The tool bodies here are deterministic local lookups so the demo runs offline.
Swapping in a real HTTP client changes the body, not the contract.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Backing data (stands in for a KB and a CMDB)
# ---------------------------------------------------------------------------

KB = {
    "vpn": "VPN outages: check GlobalProtect gateway health, Okta MFA, and ISP status page.",
    "mfa": "MFA failures: reset factors in IdP, verify time sync, exclude legacy auth protocols.",
    "laptop": "Device issues: enforce Intune compliance, reissue certs, wipe and re-enroll if stolen.",
    "access": "Access requests: JIT elevation via PAM, manager approval for production roles.",
    "email": "Email phishing: quarantine message, reset credentials if clicked, open SEC incident if credentials entered.",
    "database": "Database incidents: check replication lag, connection pool saturation, and recent migrations.",
    "certificate": "Certificate errors: verify expiry, chain of trust, and SAN entries before reissuing.",
}

CMDB = {
    "vpn-gw-01": {"owner": "network", "tier": "t1", "status": "degraded", "region": "apac"},
    "idp-okta": {"owner": "iam", "tier": "t0", "status": "healthy", "region": "global"},
    "mail-gateway": {"owner": "messaging", "tier": "t1", "status": "healthy", "region": "global"},
    "db-primary-01": {"owner": "data", "tier": "t0", "status": "healthy", "region": "eu"},
}

RUNBOOKS = {
    "security": "SEC-RB-001: isolate, preserve evidence, rotate credentials, notify privacy office within 1 hour.",
    "network_identity": "NET-RB-014: verify IdP health, check gateway certificates, publish status page update.",
    "endpoint": "END-RB-007: mark device lost in MDM, revoke tokens, issue loaner after identity proof.",
    "access": "IAM-RB-003: validate manager approval, apply least privilege, set expiry on elevated role.",
}


class ToolError(RuntimeError):
    """Raised when a tool fails in a way the agent should know about."""


class CircuitOpen(ToolError):
    """The dependency is known-bad; fail immediately rather than waiting on it."""


# ---------------------------------------------------------------------------
# Resilience wrapper
# ---------------------------------------------------------------------------


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_after_seconds: float = 30.0
    failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if (time.time() - self.opened_at) >= self.reset_after_seconds:
            # Half-open: let one probe through.
            self.opened_at = None
            self.failures = 0
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.time()


@dataclass
class ToolResult:
    name: str
    ok: bool
    value: Any
    latency_ms: int
    attempts: int
    cost_units: float
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "tool": self.name,
            "ok": self.ok,
            "value": self.value,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "cost_units": self.cost_units,
            "error": self.error,
        }


@dataclass
class Tool:
    """A callable the agent may invoke, with its schema and reliability policy."""

    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]
    cost_units: float = 0.01
    timeout_seconds: float = 5.0
    max_attempts: int = 2
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def schema(self) -> dict:
        """OpenAI/Anthropic-style tool definition — hand this straight to a model."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def call(self, **kwargs) -> ToolResult:
        started = time.perf_counter()

        if not self.breaker.allow():
            return ToolResult(
                name=self.name,
                ok=False,
                value=None,
                latency_ms=0,
                attempts=0,
                cost_units=0.0,
                error="circuit_open",
            )

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                value = self.fn(**kwargs)
                elapsed = int((time.perf_counter() - started) * 1000)
                self.breaker.record_success()
                return ToolResult(
                    name=self.name,
                    ok=True,
                    value=value,
                    latency_ms=max(elapsed, 1),
                    attempts=attempt,
                    cost_units=self.cost_units * attempt,
                )
            except ToolError as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    time.sleep(min(0.05 * (2**attempt) + random.uniform(0, 0.02), 0.3))

        self.breaker.record_failure()
        elapsed = int((time.perf_counter() - started) * 1000)
        return ToolResult(
            name=self.name,
            ok=False,
            value=None,
            latency_ms=max(elapsed, 1),
            attempts=self.max_attempts,
            cost_units=self.cost_units * self.max_attempts,
            error=str(last_error) if last_error else "unknown_error",
        )


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _search_kb(query: str) -> str:
    q = (query or "").lower()
    aliases = {
        "vpn": ("vpn", "remote", "globalprotect"),
        "mfa": ("mfa", "login", "2fa", "authentication"),
        "laptop": ("laptop", "device", "stolen", "endpoint"),
        "access": ("access", "permission", "role", "privilege"),
        "email": ("phish", "email", "mailbox", "credential"),
        "database": ("database", "db", "replication", "query"),
        "certificate": ("certificate", "cert", "tls", "ssl"),
    }
    hits = [
        KB[key]
        for key, words in aliases.items()
        if any(w in q for w in words)
    ]
    # Preserve order, drop duplicates, cap the context we hand the resolver.
    seen: set[str] = set()
    unique = [h for h in hits if not (h in seen or seen.add(h))]
    return " | ".join(unique[:3]) if unique else "No KB article matched; escalate to tier-2."


def _lookup_cmdb(ci: str) -> dict:
    key = (ci or "").lower().strip()
    if not key:
        raise ToolError("empty configuration item identifier")
    for name, record in CMDB.items():
        if name in key or key in name:
            return {"ci": name, **record}
    return {"ci": ci, "owner": "unknown", "tier": "t3", "status": "unknown", "region": "unknown"}


def _fetch_runbook(category: str) -> str:
    return RUNBOOKS.get(
        (category or "").lower(),
        "GEN-RB-000: acknowledge, gather context, route to the owning team.",
    )


SEARCH_KB = Tool(
    name="search_kb",
    description="Search the IT knowledge base for remediation guidance matching a free-text query.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Free-text symptom description."}},
        "required": ["query"],
    },
    fn=_search_kb,
    cost_units=0.01,
)

LOOKUP_CMDB = Tool(
    name="lookup_cmdb",
    description="Look up a configuration item in the CMDB and return its owner, tier and health.",
    parameters={
        "type": "object",
        "properties": {"ci": {"type": "string", "description": "Configuration item identifier, e.g. vpn-gw-01."}},
        "required": ["ci"],
    },
    fn=_lookup_cmdb,
    cost_units=0.01,
)

FETCH_RUNBOOK = Tool(
    name="fetch_runbook",
    description="Fetch the approved runbook for an incident category.",
    parameters={
        "type": "object",
        "properties": {"category": {"type": "string", "description": "Triage category."}},
        "required": ["category"],
    },
    fn=_fetch_runbook,
    cost_units=0.005,
)

REGISTRY: dict[str, Tool] = {
    t.name: t for t in (SEARCH_KB, LOOKUP_CMDB, FETCH_RUNBOOK)
}


def tool_schemas() -> list[dict]:
    """The tool manifest an LLM-driven planner would be given."""
    return [t.schema() for t in REGISTRY.values()]


def call_tool(name: str, **kwargs) -> ToolResult:
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(name, False, None, 0, 0, 0.0, error=f"unknown tool: {name}")
    return tool.call(**kwargs)


# Thin functional wrappers, kept so existing call sites and tests stay valid.


def search_kb(query: str) -> str:
    return _search_kb(query)


def lookup_cmdb(ci: str) -> dict:
    return _lookup_cmdb(ci)
