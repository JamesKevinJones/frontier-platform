/**
 * Typed client for the three platform services.
 *
 * Deployment reality: the console is a static bundle and can be hosted anywhere,
 * but the FastAPI services run wherever the operator runs them. So every call
 * tries the live API first and falls back to bundled fixtures when nothing
 * answers — a published demo stays explorable instead of rendering a wall of
 * network errors, and the UI labels the difference rather than pretending.
 */

import { DEMO } from "./demo";

export type Health = { status: string; service: string; version?: string } & Record<
  string,
  unknown
>;

export type GuardFinding = {
  rule: string;
  severity: string;
  message: string;
  span?: string | null;
};

export type Citation = {
  doc_id: string;
  title: string;
  dept: string;
  excerpt: string;
  score: number;
  rerank_score: number;
  bm25: number;
  dense: number;
  truncated: boolean;
};

export type RetrievalDebug = {
  rewritten_query: string;
  expansions: string[];
  coverage: number;
  top_rerank: number;
  candidates_considered: number;
  stages: Record<string, unknown>;
};

export type AskResponse = {
  answer: string;
  grounded: boolean;
  grounding_score: number;
  citations: Citation[];
  refused: boolean;
  refusal_reason?: string | null;
  guarded: boolean;
  guard_findings: GuardFinding[];
  provider?: string | null;
  trace_id?: string | null;
  latency_ms: number;
  cache: string;
  usage: {
    model: string;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    cost_usd: number;
    cached: boolean;
  };
  retrieval: RetrievalDebug;
  structured?: {
    answer: string;
    confidence: number;
    sources: string[];
    parsed: boolean;
  } | null;
};

export type ToolCall = {
  tool: string;
  ok: boolean;
  latency_ms: number;
  attempts: number;
  cost_units: number;
  error?: string | null;
  summary: string;
};

export type AgentEvent = {
  agent: string;
  action: string;
  detail: string;
  latency_ms: number;
  cost_units: number;
  span_id?: string | null;
  tool_calls: ToolCall[];
};

export type RunResult = {
  ticket_id: string;
  category: string;
  severity: string;
  status: string;
  resolution: string;
  policy_flags: string[];
  trajectory: AgentEvent[];
  total_latency_ms: number;
  total_cost_units: number;
  loops: number;
  guarded: boolean;
  guard_findings: GuardFinding[];
  budget_exceeded: boolean;
  max_cost_units?: number | null;
  trace_id?: string | null;
  confidence: number;
  human_review: {
    required: boolean;
    reason?: string | null;
    escalate_to?: string | null;
    sla_minutes?: number | null;
    decision_points: string[];
  };
  tool_calls: ToolCall[];
  path: string[];
};

export type ValidateResponse = {
  allowed: boolean;
  blocked: boolean;
  redacted_text: string;
  groundedness: number | null;
  risk_score: number;
  latency_ms: number;
  findings: GuardFinding[];
  trace_id?: string | null;
};

export type GuardEval = {
  n: number;
  pass_rate: number;
  recall: number;
  precision: number;
  false_positive_rate: number;
  false_negative_rate: number;
  block_rate: number;
  avg_latency_ms: number;
  confusion_matrix: {
    true_positive: number;
    false_positive: number;
    true_negative: number;
    false_negative: number;
  };
  gate_passed: boolean;
  gate_failures: string[];
  thresholds: Record<string, number | string>;
  details: Array<{
    id: string;
    category: string;
    ok: boolean;
    expected_allow: boolean;
    allowed: boolean;
    rules_triggered: string[];
    risk_score: number;
  }>;
};

export type EvalSummary = {
  n: number;
  avg_grounding: number;
  refuse_rate: number;
  citation_hit_rate: number;
  avg_faithfulness: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  precision_at_k: number;
  recall_at_k: number;
  mrr: number;
  gate_passed: boolean;
  gate_failures: string[];
  run_id?: number | null;
  details: Array<Record<string, unknown>>;
};

export type Span = {
  name: string;
  service: string;
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  duration_ms: number;
  status: string;
  attributes: Record<string, unknown>;
};

export type Snapshot = {
  service: string;
  counters: Record<string, number>;
  gauges: Record<string, number>;
  histograms: Record<string, { count: number; avg: number; p50: number; p95: number; p99: number }>;
  recent_spans: Span[];
};

export const SERVICES = {
  rag: { base: "/api/rag", label: "GroundedCorp", role: "Retrieval", port: 8001 },
  agents: { base: "/api/agents", label: "AgentForge", role: "Orchestration", port: 8002 },
  guard: { base: "/api/guard", label: "GuardRailOps", role: "Governance", port: 8003 },
} as const;

export type ServiceKey = keyof typeof SERVICES;

/** True once any request has fallen back to fixtures. Drives the DEMO badge. */
let demoMode = false;
const listeners = new Set<(v: boolean) => void>();

export function isDemoMode() {
  return demoMode;
}

export function onDemoModeChange(fn: (v: boolean) => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function setDemoMode(v: boolean) {
  if (demoMode === v) return;
  demoMode = v;
  listeners.forEach((fn) => fn(v));
}

const TIMEOUT_MS = 6000;

async function request<T>(
  service: ServiceKey,
  path: string,
  init?: RequestInit
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${SERVICES[service].base}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/** Call the live service; on any transport failure, serve the fixture instead. */
async function withFallback<T>(
  live: () => Promise<T>,
  fixture: () => T
): Promise<{ data: T; live: boolean }> {
  try {
    const data = await live();
    return { data, live: true };
  } catch {
    setDemoMode(true);
    return { data: fixture(), live: false };
  }
}

export const api = {
  health: (service: ServiceKey) =>
    withFallback<Health>(
      () => request<Health>(service, "/health"),
      () => DEMO.health[service]
    ),

  ask: (question: string, dept: string | null, structured: boolean) =>
    withFallback<AskResponse>(
      () =>
        request<AskResponse>("rag", "/ask", {
          method: "POST",
          body: JSON.stringify({ question, dept, structured, use_cache: true }),
        }),
      () => DEMO.ask(question, dept)
    ),

  ragEval: () =>
    withFallback<EvalSummary>(
      () => request<EvalSummary>("rag", "/eval/run", { method: "POST" }),
      () => DEMO.ragEval
    ),

  ragDocs: () =>
    withFallback<Array<{ doc_id: string; title: string; dept: string; version: string }>>(
      () => request("rag", "/docs/list"),
      () => DEMO.docs
    ),

  runTicket: (title: string, description: string) =>
    withFallback<RunResult>(
      () =>
        request<RunResult>("agents", "/run", {
          method: "POST",
          body: JSON.stringify({ title, description, requester: "console@example.com" }),
        }),
      () => DEMO.run(title, description)
    ),

  agentSamples: () =>
    withFallback<Array<{ title: string; description: string; note?: string }>>(
      () => request("agents", "/samples"),
      () => DEMO.samples
    ),

  validate: (text: string, direction: string, groundingScore: number | null) =>
    withFallback<ValidateResponse>(
      () =>
        request<ValidateResponse>("guard", "/validate", {
          method: "POST",
          body: JSON.stringify({ text, direction, grounding_score: groundingScore }),
        }),
      () => DEMO.validate(text, direction, groundingScore)
    ),

  guardEval: () =>
    withFallback<GuardEval>(
      () => request<GuardEval>("guard", "/eval/run", { method: "POST" }),
      () => DEMO.guardEval
    ),

  guardRules: () =>
    withFallback<Record<string, Record<string, unknown>>>(
      () => request("guard", "/rules"),
      () => DEMO.rules
    ),

  metrics: (service: ServiceKey) =>
    withFallback<Snapshot>(
      () => request<Snapshot>(service, "/observability/metrics"),
      () => DEMO.metrics[service]
    ),
};
