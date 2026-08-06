"""
Defense-in-depth guardrails shared by every app on the platform.

Layers, in the order they run:

    1. prompt injection      (input direction only)   -> high    -> block
    2. secret / credential   (both directions)        -> high    -> block
    3. PII                   (both directions)        -> medium  -> redact, allow
    4. toxicity              (both directions)        -> medium  -> allow, flag
    5. groundedness          (output direction)       -> high    -> block

Design notes
------------
- **Policy driven.** Thresholds and severity->action mapping live in
  ``policy.json`` and can be overridden per-environment without a code change.
  This is what makes it a governance control rather than a hardcoded if-statement.
- **Precision over recall on PII.** Credit-card candidates are Luhn-validated and
  Aadhaar numbers are Verhoeff-validated, so invoice numbers and order ids do not
  get shredded. A guardrail that cries wolf gets switched off in production.
- **Explainable.** Every finding carries the rule, severity, a human-readable
  message and the offending span, so a reviewer can audit *why* something was
  blocked. Auditability is a Responsible-AI requirement, not a nicety.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

POLICY_PATH = Path(__file__).with_name("policy.json")

# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------

# Matched case-insensitively against the original text. Each allows a few filler
# words between the verb and its object, because "ignore all previous instructions"
# and "ignore the instructions above" are the same attack and an exact-phrase list
# only ever catches the variant you happened to think of.
INJECTION_PATTERNS = [
    r"\bignore\s+(?:\w+\s+){0,3}instructions\b",
    r"\bdisregard\s+(?:\w+\s+){0,3}(?:instructions|rules|policy|guidelines|the above|above)\b",
    r"\boverride\s+(?:\w+\s+){0,2}(?:instructions|rules|policy|system)\b",
    r"\bsystem prompt\b",
    r"\bjailbreak\b",
    r"\bdo not follow\s+(?:your|the)\s+(?:rules|policy|guidelines)\b",
    r"\bdeveloper mode\b",
    r"\bdan mode\b",
    r"\byou are now\b",
    r"\bpretend\s+(?:you are|to be)\b(?!\s+a helpful)",
    r"\breveal\s+(?:your|the)\s+(?:\w+\s+){0,2}(?:instructions|prompt|rules|configuration)\b",
    r"\b(?:print|output|show|repeat)\s+(?:your|the)\s+(?:\w+\s+){0,2}(?:system prompt|instructions|initial message)\b",
    r"</?(?:system|assistant)>",
    r"\bbegin (?:system|admin) override\b",
    r"\bact as\s+(?:an?\s+)?(?:unrestricted|unfiltered|uncensored)\b",
]

# Credential material must never be echoed back by an LLM, in either direction.
SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{24,}\b"),
    "generic_password": re.compile(
        r"\b(?:password|passwd|secret|api[_-]?key)\s*[:=]\s*\S{6,}", re.IGNORECASE
    ),
}

PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # Separators are required rather than optional: making them optional lets the
    # engine backtrack into a partial match and redact half a number, leaving the
    # rest of the digits in the clear — worse than not detecting it at all.
    "phone": re.compile(
        r"\b(?:\+?\d{1,3}[-.\s])?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}\b"
        r"|\b(?:\+91[-.\s]?)?[6-9]\d{9}\b"
    ),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # India-specific identifiers: the enterprise context this platform targets.
    "pan": re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    "aadhaar": re.compile(r"\b[2-9]\d{3}[-\s]?\d{4}[-\s]?\d{4}\b"),
    "ifsc": re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}

# Rules whose matches need a checksum before we believe them.
_CHECKSUM_RULES = {"credit_card", "aadhaar"}

TOXIC_TERMS = {
    "idiot",
    "stupid",
    "hate you",
    "kill yourself",
    "moron",
    "shut up",
    "worthless",
}

# Documentation and test fixtures that must not trip the PII detector.
PII_ALLOWLIST = {
    "example.com",
    "example.org",
    "example.net",
    "0.0.0.0",
    "127.0.0.1",
    "255.255.255.255",
}


# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------


def luhn_ok(digits: str) -> bool:
    """Luhn mod-10 check — keeps order ids and phone numbers out of the card bucket."""
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    checksum, parity = 0, len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_ok(digits: str) -> bool:
    """Verhoeff checksum — the algorithm UIDAI uses for Aadhaar numbers."""
    nums = [int(c) for c in digits if c.isdigit()]
    if len(nums) != 12:
        return False
    c = 0
    for i, n in enumerate(reversed(nums)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][n]]
    return c == 0


def _checksum_ok(rule: str, value: str) -> bool:
    if rule == "credit_card":
        return luhn_ok(value)
    if rule == "aadhaar":
        return verhoeff_ok(value)
    return True


def _allowlisted(value: str) -> bool:
    low = value.lower()
    return any(safe in low for safe in PII_ALLOWLIST)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardPolicy:
    """Runtime governance policy. Loaded from JSON, overridable by environment."""

    grounding_threshold: float = 0.35
    blocking_severities: tuple[str, ...] = ("high", "critical")
    redact_pii: bool = True
    check_secrets: bool = True
    check_toxicity: bool = True
    severity_weights: dict = field(
        default_factory=lambda: {"low": 1.0, "medium": 3.0, "high": 8.0, "critical": 13.0}
    )

    @staticmethod
    @lru_cache(maxsize=1)
    def load() -> "GuardPolicy":
        raw: dict = {}
        if POLICY_PATH.exists():
            try:
                raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
        return GuardPolicy(
            grounding_threshold=float(
                os.getenv("GUARD_GROUNDING_THRESHOLD", raw.get("grounding_threshold", 0.35))
            ),
            blocking_severities=tuple(raw.get("blocking_severities", ["high", "critical"])),
            redact_pii=_env_bool("GUARD_REDACT_PII", raw.get("redact_pii", True)),
            check_secrets=_env_bool("GUARD_CHECK_SECRETS", raw.get("check_secrets", True)),
            check_toxicity=_env_bool("GUARD_CHECK_TOXICITY", raw.get("check_toxicity", True)),
            severity_weights=raw.get(
                "severity_weights",
                {"low": 1.0, "medium": 3.0, "high": 8.0, "critical": 13.0},
            ),
        )


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return bool(default)
    return val.strip().lower() in {"1", "true", "yes", "on"}


def active_policy() -> GuardPolicy:
    return GuardPolicy.load()


def reload_policy() -> GuardPolicy:
    """Drop the cached policy — used by tests and by hot config reloads."""
    GuardPolicy.load.cache_clear()
    return GuardPolicy.load()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class GuardFinding:
    rule: str
    severity: str
    message: str
    span: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GuardResult:
    allowed: bool
    findings: list[GuardFinding] = field(default_factory=list)
    redacted_text: str = ""
    groundedness: float | None = None
    risk_score: float = 0.0
    latency_ms: float = 0.0
    policy_version: str = "1.0"

    @property
    def blocked(self) -> bool:
        return not self.allowed

    def rules_triggered(self) -> list[str]:
        return sorted({f.rule for f in self.findings})

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "blocked": self.blocked,
            "redacted_text": self.redacted_text,
            "groundedness": self.groundedness,
            "risk_score": self.risk_score,
            "latency_ms": self.latency_ms,
            "policy_version": self.policy_version,
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


_COMPILED_INJECTION = [(p, re.compile(p, re.IGNORECASE)) for p in INJECTION_PATTERNS]


def detect_injection(text: str) -> list[GuardFinding]:
    """Search the original text case-insensitively so match offsets stay valid for
    the reported span — lower-casing first silently breaks any uppercase pattern."""
    findings: list[GuardFinding] = []
    for pattern, cre in _COMPILED_INJECTION:
        m = cre.search(text)
        if m:
            findings.append(
                GuardFinding(
                    "prompt_injection",
                    "high",
                    f"Matched pattern: {pattern}",
                    text[max(m.start() - 10, 0) : m.end() + 30],
                )
            )
    return findings


def detect_secrets(text: str) -> tuple[str, list[GuardFinding]]:
    """Credential material is redacted *and* blocks — leaking a key is not recoverable."""
    findings: list[GuardFinding] = []
    out = text
    for name, cre in SECRET_PATTERNS.items():
        for m in cre.finditer(text):
            findings.append(
                GuardFinding(
                    "secret",
                    "critical",
                    f"Detected {name} credential material",
                    m.group(0)[:12] + "…",
                )
            )
        out = cre.sub(f"[REDACTED_{name.upper()}]", out)
    return out, findings


def redact_pii(text: str) -> tuple[str, list[GuardFinding]]:
    """Redact validated PII spans, leaving allowlisted and checksum-failing
    candidates untouched.

    Each detector runs against the *current* working string rather than the
    original: once the email pass has substituted a placeholder, every offset
    downstream of it has moved, and splicing original-text offsets into the
    rewritten string corrupts the output.
    """
    findings: list[GuardFinding] = []
    out = text
    for name, cre in PII_PATTERNS.items():
        matches = [
            m
            for m in cre.finditer(out)
            if _checksum_ok(name, m.group(0)) and not _allowlisted(m.group(0))
        ]
        if not matches:
            continue
        for m in matches:
            findings.append(GuardFinding("pii", "medium", f"Detected {name}", m.group(0)))
        # Right-to-left, so earlier spans keep their offsets within this pass.
        for m in sorted(matches, key=lambda x: x.start(), reverse=True):
            out = out[: m.start()] + f"[REDACTED_{name.upper()}]" + out[m.end() :]
    return out, findings


def detect_toxicity(text: str) -> list[GuardFinding]:
    low = text.lower()
    return [
        GuardFinding("toxicity", "medium", f"Toxic term: {t}", t)
        for t in TOXIC_TERMS
        if t in low
    ]


def check_groundedness(
    score: float | None, threshold: float = 0.35
) -> list[GuardFinding]:
    if score is None:
        return []
    if score < threshold:
        return [
            GuardFinding(
                "groundedness",
                "high",
                f"Grounding score {score:.3f} < threshold {threshold}",
            )
        ]
    return []


def risk_score(findings: list[GuardFinding], policy: GuardPolicy) -> float:
    """0-100 aggregate risk, for dashboards and drift tracking."""
    if not findings:
        return 0.0
    weights = policy.severity_weights
    raw = sum(weights.get(f.severity, 1.0) for f in findings)
    return round(min(raw / 20.0, 1.0) * 100, 2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def validate(
    text: str,
    *,
    direction: str = "output",
    grounding_score: float | None = None,
    grounding_threshold: float | None = None,
    policy: GuardPolicy | None = None,
) -> GuardResult:
    """Run the full guardrail chain over one piece of text.

    ``direction`` selects the input-only rules (prompt injection is meaningless on
    model output, and running it there produces false positives on any answer that
    quotes a user's question).
    """
    t0 = time.perf_counter()
    pol = policy or active_policy()
    threshold = (
        pol.grounding_threshold if grounding_threshold is None else grounding_threshold
    )

    findings: list[GuardFinding] = []
    working = text

    if direction == "input":
        findings.extend(detect_injection(working))

    if pol.check_secrets:
        working, secret_findings = detect_secrets(working)
        findings.extend(secret_findings)

    if pol.redact_pii:
        working, pii_findings = redact_pii(working)
        findings.extend(pii_findings)

    if pol.check_toxicity:
        findings.extend(detect_toxicity(working))

    findings.extend(check_groundedness(grounding_score, threshold))

    # PII and toxicity redact/flag but do not block; injection, secrets and weak
    # grounding do. The mapping is policy, not code.
    allowed = not any(f.severity in pol.blocking_severities for f in findings)

    return GuardResult(
        allowed=allowed,
        findings=findings,
        redacted_text=working,
        groundedness=grounding_score,
        risk_score=risk_score(findings, pol),
        latency_ms=round((time.perf_counter() - t0) * 1000, 3),
    )
