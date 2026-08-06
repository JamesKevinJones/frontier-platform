import { useEffect, useState } from "react";

import { api, type EvalSummary, type GuardEval } from "../api";
import { Metric, Meter, Panel, Tag } from "../components";

/**
 * The overview answers one question: is this platform currently trustworthy?
 * Not "how many requests" — a request count tells an operator nothing about
 * whether the answers coming out of the system are any good.
 */
export default function Overview({ onNavigate }: { onNavigate: (view: string) => void }) {
  const [ragEval, setRagEval] = useState<EvalSummary | null>(null);
  const [guardEval, setGuardEval] = useState<GuardEval | null>(null);
  const [docs, setDocs] = useState<Array<{ doc_id: string; title: string; dept: string; version: string }>>(
    []
  );

  useEffect(() => {
    api.ragEval().then(({ data }) => setRagEval(data));
    api.guardEval().then(({ data }) => setGuardEval(data));
    api.ragDocs().then(({ data }) => setDocs(data));
  }, []);

  const byDept = docs.reduce<Record<string, number>>((acc, d) => {
    acc[d.dept] = (acc[d.dept] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <>
      <Panel
        title="Platform posture"
        aside={
          <span className="tags">
            <Tag tone={ragEval?.gate_passed ? "pass" : "block"}>
              RAG gate {ragEval?.gate_passed ? "pass" : "fail"}
            </Tag>
            <Tag tone={guardEval?.gate_passed ? "pass" : "block"}>
              Guard gate {guardEval?.gate_passed ? "pass" : "fail"}
            </Tag>
          </span>
        }
        flush
      >
        <div className="metrics">
          <Metric
            label="Citation hit rate"
            value={ragEval ? ragEval.citation_hit_rate.toFixed(2) : "—"}
            tone="pass"
            note="answers citing the right doc"
          />
          <Metric
            label="Precision@k"
            value={ragEval ? ragEval.precision_at_k.toFixed(3) : "—"}
            note="citation list purity"
          />
          <Metric label="MRR" value={ragEval ? ragEval.mrr.toFixed(2) : "—"} note="rank of first hit" />
          <Metric
            label="Guard recall"
            value={guardEval ? guardEval.recall.toFixed(2) : "—"}
            tone="pass"
            note="unsafe traffic caught"
          />
          <Metric
            label="False positives"
            value={guardEval ? guardEval.false_positive_rate.toFixed(2) : "—"}
            tone={guardEval && guardEval.false_positive_rate > 0.1 ? "block" : "pass"}
            note="safe traffic wrongly blocked"
          />
          <Metric
            label="p95 latency"
            value={ragEval ? `${ragEval.p95_latency_ms.toFixed(1)}ms` : "—"}
            note="end to end, per question"
          />
        </div>
      </Panel>

      <div className="grid grid--2">
        <Panel title="How the three services fit together">
          <p className="dim" style={{ marginTop: 0 }}>
            One guardrail library, imported by both applications. Not an HTTP hop —
            a governance control on a network call is a control that fails open
            the moment the network does.
          </p>
          <div className="stages" style={{ marginTop: "1.25rem" }}>
            <div className="stage stage--done">
              <span className="stage__name">GroundedCorp</span>
              <span className="stage__note">
                Hybrid retrieval, grounding gate, citations. Refuses rather than guesses.
              </span>
            </div>
            <div className="stage stage--done">
              <span className="stage__name">AgentForge</span>
              <span className="stage__note">
                Five-agent state graph with tool resilience, cost budgets and human escalation.
              </span>
            </div>
            <div className="stage stage--active">
              <span className="stage__name">GuardRailOps</span>
              <span className="stage__note">
                Shared controls, red-team suite, drift detection, CI quality gate.
              </span>
            </div>
          </div>
          <div className="actions">
            <button type="button" className="ghost" onClick={() => onNavigate("retrieval")}>
              Open retrieval
            </button>
            <button type="button" className="ghost" onClick={() => onNavigate("agents")}>
              Open agents
            </button>
            <button type="button" className="ghost" onClick={() => onNavigate("guardrails")}>
              Open guardrails
            </button>
          </div>
        </Panel>

        <Panel title="Quality gates enforced in CI">
          <p className="dim" style={{ marginTop: 0 }}>
            Ordinary CI proves the code runs. These prove the system still behaves.
            A merge that degrades retrieval or loosens a guardrail fails the build.
          </p>
          <div style={{ marginTop: "1rem" }}>
            <Meter
              label="Citation hit rate"
              value={ragEval?.citation_hit_rate ?? 0}
              threshold={0.85}
              tone="pass"
            />
            <Meter label="Precision@k" value={ragEval?.precision_at_k ?? 0} threshold={0.6} tone="pass" />
            <Meter label="Faithfulness" value={ragEval?.avg_faithfulness ?? 0} threshold={0.3} tone="pass" />
            <Meter label="Guard recall" value={guardEval?.recall ?? 0} threshold={0.9} tone="pass" />
            <Meter
              label="Guard false positive rate"
              value={guardEval?.false_positive_rate ?? 0}
              threshold={0.1}
              tone="pass"
            />
          </div>
        </Panel>
      </div>

      <Panel title={`Knowledge base (${docs.length} documents)`} flush>
        <div className="scroll-x">
          <table className="table">
            <thead>
              <tr>
                <th>Document</th>
                <th>Department</th>
                <th>Version</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((doc) => (
                <tr key={doc.doc_id}>
                  <td>{doc.title}</td>
                  <td className="mute">{doc.dept}</td>
                  <td className="mute">v{doc.version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {docs.length ? (
          <div className="tags" style={{ padding: "0.75rem 1rem" }}>
            {Object.entries(byDept).map(([dept, count]) => (
              <Tag key={dept}>
                {dept} · {count}
              </Tag>
            ))}
          </div>
        ) : null}
      </Panel>
    </>
  );
}
