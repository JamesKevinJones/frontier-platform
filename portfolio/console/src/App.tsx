import { useEffect, useState } from "react";

import { api, isDemoMode, onDemoModeChange, SERVICES, type ServiceKey } from "./api";
import { Lamp, Tag } from "./components";
import Agents from "./views/Agents";
import Guardrails from "./views/Guardrails";
import Overview from "./views/Overview";
import Retrieval from "./views/Retrieval";

const VIEWS = [
  {
    id: "overview",
    label: "Overview",
    title: "Frontier Console",
    lede: "A governed AI platform in three services: grounded retrieval, a multi-agent workflow, and the guardrail library both of them import. Every number on this page comes from an evaluation suite that runs in CI.",
  },
  {
    id: "retrieval",
    label: "Retrieval",
    title: "Grounded retrieval",
    lede: "Hybrid BM25 and dense search fused with reciprocal rank fusion, reranked for phrase proximity, diversified with MMR, then gated. When the corpus cannot support an answer, the service refuses instead of improvising.",
  },
  {
    id: "agents",
    label: "Agents",
    title: "Multi-agent workflow",
    lede: "Five specialist agents on an explicit state graph. Tools retry and trip a circuit breaker, the critic can send work back exactly once, and exceeding the cost budget stops the run and escalates to a person.",
  },
  {
    id: "guardrails",
    label: "Guardrails",
    title: "Guardrails and evaluation",
    lede: "Injection, credential, PII, toxicity and grounding controls, scored as a confusion matrix. The gate enforces both directions: catch the attacks, and leave legitimate traffic alone.",
  },
] as const;

type ViewId = (typeof VIEWS)[number]["id"];

type LampState = "up" | "down" | "wait";

export default function App() {
  const [view, setView] = useState<ViewId>("overview");
  const [lamps, setLamps] = useState<Record<ServiceKey, LampState>>({
    rag: "wait",
    agents: "wait",
    guard: "wait",
  });
  const [demo, setDemo] = useState(isDemoMode());

  useEffect(() => onDemoModeChange(setDemo), []);

  useEffect(() => {
    let cancelled = false;
    (Object.keys(SERVICES) as ServiceKey[]).forEach((key) => {
      api.health(key).then(({ live }) => {
        if (!cancelled) {
          setLamps((prev) => ({ ...prev, [key]: live ? "up" : "down" }));
        }
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const active = VIEWS.find((v) => v.id === view) ?? VIEWS[0];

  return (
    <div className="app">
      <nav className="rail" aria-label="Console">
        <div className="rail__mark">
          <p className="eyebrow">Frontier</p>
          <strong>Console</strong>
          <span className="mute" style={{ fontSize: 11 }}>
            governed AI platform
          </span>
        </div>

        <div className="rail__group rail__group--nav">
          <p className="rail__label">Views</p>
          {VIEWS.map((item) => (
            <button
              key={item.id}
              type="button"
              className="navlink"
              aria-current={view === item.id}
              onClick={() => setView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="rail__group">
          <p className="rail__label">Services</p>
          {(Object.keys(SERVICES) as ServiceKey[]).map((key) => (
            <Lamp
              key={key}
              label={SERVICES[key].label}
              port={SERVICES[key].port}
              state={lamps[key]}
            />
          ))}
        </div>

        <div className="rail__group" style={{ marginTop: "auto" }}>
          {demo ? (
            <>
              <Tag tone="signal">Demo data</Tag>
              <p className="mute" style={{ fontSize: 11, margin: 0 }}>
                No service is answering, so recorded responses are shown. Start the
                stack with <code>docker compose up</code> to drive the real thing.
              </p>
            </>
          ) : (
            <Tag tone="pass">Live</Tag>
          )}
        </div>
      </nav>

      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">{active.label}</p>
            <h1>{active.title}</h1>
            <p className="topbar__lede">{active.lede}</p>
          </div>
        </header>

        <div className="view">
          {view === "overview" ? <Overview onNavigate={(v) => setView(v as ViewId)} /> : null}
          {view === "retrieval" ? <Retrieval /> : null}
          {view === "agents" ? <Agents /> : null}
          {view === "guardrails" ? <Guardrails /> : null}
        </div>
      </main>
    </div>
  );
}
