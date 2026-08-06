import { FormEvent, useEffect, useState } from "react";

import { api, type RunResult } from "../api";
import {
  ErrorNote,
  Metric,
  Panel,
  Stages,
  Tag,
  TraceWaterfall,
  Verdict,
  type SpanRow,
  type Stage,
} from "../components";

const NODES = ["triage", "research", "policy", "resolver", "critic"];

function toStages(result: RunResult | null): Stage[] {
  if (!result) {
    return NODES.map((name) => ({ name, note: "idle", state: "idle" as const }));
  }
  if (result.status === "rejected") {
    return [
      { name: "guard", note: "input blocked", state: "blocked" },
      ...NODES.map((name) => ({ name, note: "never ran", state: "idle" as const })),
    ];
  }
  const visited = new Set(result.path);
  return NODES.map((name) => {
    const events = result.trajectory.filter((e) => e.agent === name);
    const last = events[events.length - 1];
    return {
      name,
      note: last ? `${last.action} · ${last.latency_ms}ms` : visited.has(name) ? "ran" : "skipped",
      state: visited.has(name) ? ("done" as const) : ("idle" as const),
    };
  });
}

function toSpans(result: RunResult | null): SpanRow[] {
  if (!result) return [];
  const spans: SpanRow[] = [
    {
      name: "graph.run",
      detail: result.path.join(" → "),
      durationMs: Math.max(result.total_latency_ms, 0.5),
      tone: "dim",
    },
  ];
  result.trajectory.forEach((event) => {
    spans.push({
      name: `agent.${event.agent}`,
      detail: event.action,
      durationMs: Math.max(event.latency_ms, 0.5),
      tone:
        event.agent === "guard" || event.agent === "budget"
          ? "block"
          : event.action === "accept"
            ? "pass"
            : "signal",
      depth: 1,
    });
    event.tool_calls.forEach((call) => {
      spans.push({
        name: `tool.${call.tool}`,
        detail: call.ok ? `${call.attempts} attempt${call.attempts === 1 ? "" : "s"}` : call.error ?? "failed",
        durationMs: Math.max(call.latency_ms, 0.3),
        tone: call.ok ? "pass" : "block",
        depth: 2,
      });
    });
  });
  return spans;
}

export default function Agents() {
  const [samples, setSamples] = useState<Array<{ title: string; description: string; note?: string }>>(
    []
  );
  const [title, setTitle] = useState("VPN outage for APAC users");
  const [description, setDescription] = useState(
    "Multiple users cannot login to VPN since 09:00. MFA prompts loop."
  );
  const [result, setResult] = useState<RunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.agentSamples().then(({ data }) => setSamples(data));
  }, []);

  async function onRun(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.runTicket(title, description);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run failed");
    } finally {
      setBusy(false);
    }
  }

  const budgetUsed = result?.max_cost_units
    ? Math.min(result.total_cost_units / result.max_cost_units, 1)
    : 0;

  return (
    <>
      <Panel title="Submit a ticket" aside={<span className="mute">AgentForge · :8002</span>}>
        <div className="tags" style={{ marginBottom: "1rem" }}>
          {samples.map((sample) => (
            <button
              key={sample.title}
              type="button"
              className="chip"
              // The tooltip must not become the accessible name — a screen reader
              // should hear the ticket, then the hint, not the hint alone.
              aria-label={sample.note ? `${sample.title} — ${sample.note}` : sample.title}
              title={sample.note}
              onClick={() => {
                setTitle(sample.title);
                setDescription(sample.description);
              }}
            >
              {sample.title}
            </button>
          ))}
        </div>

        <form onSubmit={onRun}>
          <label className="field">
            <span>Title</span>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} required />
          </label>
          <label className="field">
            <span>Description</span>
            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              required
            />
          </label>
          <div className="actions">
            <button type="submit" disabled={busy}>
              {busy ? "Running graph…" : "Run workflow"}
            </button>
            <button type="button" className="ghost" onClick={() => setResult(null)} disabled={!result}>
              Clear
            </button>
          </div>
        </form>
      </Panel>

      {error ? <ErrorNote message={error} /> : null}

      <Panel title="Graph path" aside={<span className="mute">triage → research → policy → resolver → critic</span>}>
        <Stages stages={toStages(result)} />
      </Panel>

      {result ? (
        <>
          <Verdict
            allowed={result.status === "resolved"}
            title={result.status.replace("_", " ")}
            reason={
              result.human_review.required
                ? `${result.human_review.reason} → ${result.human_review.escalate_to} (SLA ${result.human_review.sla_minutes}m)`
                : `${result.severity} · ${result.category}`
            }
          >
            <span className="tags" style={{ marginLeft: "auto" }}>
              <Tag tone="signal">{result.severity}</Tag>
              {result.budget_exceeded ? <Tag tone="block">budget exceeded</Tag> : null}
              {result.loops > 0 ? <Tag>{result.loops} repair loop</Tag> : null}
            </span>
          </Verdict>

          <div className="panel">
            <div className="metrics">
              <Metric
                label="Cost"
                value={result.total_cost_units.toFixed(3)}
                note={`budget ${result.max_cost_units ?? "—"} · ${Math.round(budgetUsed * 100)}% used`}
                tone={result.budget_exceeded ? "block" : "neutral"}
              />
              <Metric label="Latency" value={`${result.total_latency_ms}ms`} />
              <Metric label="Confidence" value={result.confidence.toFixed(2)} tone="pass" />
              <Metric label="Tool calls" value={result.tool_calls.length} />
              <Metric label="Repair loops" value={result.loops} />
              <Metric label="Ticket" value={result.ticket_id} note={result.trace_id?.slice(0, 12)} />
            </div>
          </div>

          <div className="grid grid--2">
            <Panel title="Resolution">
              <pre className="answer">{result.resolution}</pre>
            </Panel>

            <Panel title="Policy constraints">
              <div className="tags">
                {result.policy_flags.map((flag) => (
                  <Tag key={flag} tone={flag === "standard_sla" ? "neutral" : "signal"}>
                    {flag}
                  </Tag>
                ))}
              </div>
              {result.human_review.required ? (
                <>
                  <p className="eyebrow" style={{ marginTop: "1.5rem" }}>
                    Human decision points
                  </p>
                  <ul className="dim" style={{ paddingLeft: "1.1rem", margin: "0.5rem 0 0" }}>
                    {result.human_review.decision_points.map((point) => (
                      <li key={point}>{point}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="mute" style={{ fontSize: 11, marginBottom: 0, marginTop: "1rem" }}>
                  Critic accepted the plan without escalation.
                </p>
              )}
            </Panel>
          </div>

          <Panel title="Trace" aside={<span className="mute">agents and tool calls</span>} flush>
            <TraceWaterfall spans={toSpans(result)} traceId={result.trace_id} />
          </Panel>

          <Panel title="Trajectory" flush>
            <ol className="log">
              {result.trajectory.map((event, i) => (
                <li className="log__item" key={`${event.agent}-${i}`}>
                  <span className="log__idx" />
                  <div>
                    <div className="log__head">
                      <span className="log__agent">{event.agent}</span>
                      <span className="log__action">{event.action}</span>
                      <span className="log__meta">
                        {event.latency_ms}ms · {event.cost_units.toFixed(3)} units
                      </span>
                    </div>
                    <p className="log__detail">{event.detail}</p>
                    {event.tool_calls.length ? (
                      <div className="tags" style={{ marginTop: "0.5rem" }}>
                        {event.tool_calls.map((call, j) => (
                          <Tag key={`${call.tool}-${j}`} tone={call.ok ? "pass" : "block"}>
                            {call.tool} {call.ok ? "ok" : call.error}
                          </Tag>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          </Panel>
        </>
      ) : null}
    </>
  );
}
