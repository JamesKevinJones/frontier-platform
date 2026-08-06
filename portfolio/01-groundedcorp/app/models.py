from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    dept: str | None = None
    structured: bool = Field(
        default=False, description="Return a typed JSON envelope instead of prose."
    )
    use_cache: bool = True


class Citation(BaseModel):
    doc_id: str
    title: str
    dept: str
    excerpt: str
    score: float
    rerank_score: float = 0.0
    bm25: float = 0.0
    dense: float = 0.0
    truncated: bool = False


class GuardFindingOut(BaseModel):
    rule: str
    severity: str
    message: str
    span: str | None = None


class UsageOut(BaseModel):
    model: str = "mock"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False


class RetrievalDebug(BaseModel):
    """Everything needed to explain *why* an answer looks the way it does."""

    rewritten_query: str = ""
    expansions: list[str] = Field(default_factory=list)
    coverage: float = 0.0
    top_rerank: float = 0.0
    candidates_considered: int = 0
    stages: dict = Field(default_factory=dict)


class StructuredAnswer(BaseModel):
    answer: str
    confidence: float = 0.0
    sources: list[str] = Field(default_factory=list)
    parsed: bool = False


class AskResponse(BaseModel):
    answer: str
    grounded: bool
    grounding_score: float
    citations: list[Citation]
    refused: bool
    refusal_reason: str | None = None
    guarded: bool = True
    guard_findings: list[GuardFindingOut] = Field(default_factory=list)
    provider: str | None = None
    trace_id: str | None = None
    latency_ms: float = 0.0
    cache: str = "miss"
    usage: UsageOut = Field(default_factory=UsageOut)
    retrieval: RetrievalDebug = Field(default_factory=RetrievalDebug)
    structured: StructuredAnswer | None = None


class DocMeta(BaseModel):
    doc_id: str
    title: str
    dept: str
    doc_type: str
    version: str
    path: str


class EvalSummary(BaseModel):
    n: int
    avg_grounding: float
    refuse_rate: float
    citation_hit_rate: float
    avg_faithfulness: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    avg_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    mrr: float = 0.0
    run_id: int | None = None
    gate_passed: bool = True
    gate_failures: list[str] = Field(default_factory=list)
    details: list[dict] = Field(default_factory=list)


class DriftReport(BaseModel):
    """Regression signal across eval runs — the LLMOps view."""

    baseline_run_id: int | None = None
    latest_run_id: int | None = None
    drifted: bool = False
    alerts: list[dict] = Field(default_factory=list)
    deltas: dict = Field(default_factory=dict)
    history: list[dict] = Field(default_factory=list)
