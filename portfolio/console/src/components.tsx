import type { ReactNode } from "react";

/* ---------------------------------------------------------------- primitives */

export function Panel({
  title,
  aside,
  flush,
  children,
}: {
  title: string;
  aside?: ReactNode;
  flush?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header className="panel__head">
        <h2 className="eyebrow">{title}</h2>
        {aside}
      </header>
      <div className={flush ? "panel__body panel__body--flush" : "panel__body"}>{children}</div>
    </section>
  );
}

export type Tone = "neutral" | "pass" | "block" | "signal";

export function Metric({
  label,
  value,
  note,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  note?: string;
  tone?: Tone;
}) {
  return (
    <div className={tone === "neutral" ? "metric" : `metric metric--${tone}`}>
      <span className="metric__label">{label}</span>
      <span className="metric__value">{value}</span>
      {note ? <span className="metric__note">{note}</span> : null}
    </div>
  );
}

export function Tag({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return <span className={tone === "neutral" ? "tag" : `tag tag--${tone}`}>{children}</span>;
}

export function Lamp({
  label,
  port,
  state,
}: {
  label: string;
  port: number;
  state: "up" | "down" | "wait";
}) {
  const reading = { up: "online", down: "offline", wait: "checking" }[state];
  return (
    <div className={`lamp lamp--${state}`}>
      <span className="lamp__dot" aria-hidden="true" />
      <span>{label}</span>
      <span className="lamp__port" aria-label={`${label} ${reading}`}>
        :{port}
      </span>
    </div>
  );
}

/**
 * A horizontal meter with a threshold notch. The notch is the point: a bare
 * progress bar tells you a value, this one tells you whether the value passes.
 */
export function Meter({
  label,
  value,
  max = 1,
  threshold,
  tone = "signal",
  format,
}: {
  label: string;
  value: number;
  max?: number;
  threshold?: number;
  tone?: "signal" | "pass" | "block";
  format?: (v: number) => string;
}) {
  const pct = Math.max(0, Math.min(value / max, 1)) * 100;
  const show = format ? format(value) : value.toFixed(3);
  return (
    <div className="meter">
      <div className="meter__row">
        <span className="mute">{label}</span>
        <span>{show}</span>
      </div>
      <div className="meter__track">
        <div className={`meter__fill meter__fill--${tone}`} style={{ width: `${pct}%` }} />
        {threshold !== undefined ? (
          <span
            className="meter__mark"
            style={{ left: `${Math.min((threshold / max) * 100, 100)}%` }}
            title={`threshold ${threshold}`}
          />
        ) : null}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- stages */

export type StageState = "idle" | "active" | "done" | "blocked";

export type Stage = { name: string; note: string; state: StageState };

/**
 * The pipeline rail. Order is meaningful here — retrieval genuinely happens
 * before the grounding gate, which genuinely happens before generation — so the
 * sequence is doing real work rather than decorating the layout.
 */
export function Stages({ stages }: { stages: Stage[] }) {
  return (
    <ol className="stages" aria-label="Pipeline stages">
      {stages.map((stage) => (
        <li key={stage.name} className={`stage stage--${stage.state}`}>
          <span className="stage__name">{stage.name}</span>
          <span className="stage__note">{stage.note}</span>
        </li>
      ))}
    </ol>
  );
}

/* --------------------------------------------------- trace waterfall (signature) */

export type SpanRow = {
  name: string;
  detail?: string;
  durationMs: number;
  tone?: "signal" | "pass" | "block" | "dim";
  depth?: number;
};

/**
 * The signature element.
 *
 * Every request through this platform leaves a span tree, and that tree is the
 * clearest available answer to "what did the system actually do, and where did
 * the time go". Rendering it on a shared time axis means duration and ordering
 * are read from position alone — no legend, no tooltips required.
 */
export function TraceWaterfall({
  spans,
  traceId,
}: {
  spans: SpanRow[];
  traceId?: string | null;
}) {
  if (!spans.length) {
    return <p className="empty">No spans recorded yet. Run a request to populate the trace.</p>;
  }

  // A root span *contains* its children rather than preceding them, so the axis
  // is the root's duration and children are laid out cumulatively inside it.
  // Summing every span instead would double-count the root and report a total
  // roughly twice the real latency.
  const rootDuration = spans
    .filter((s) => !s.depth)
    .reduce((sum, s) => sum + Math.max(s.durationMs, 0), 0);
  const childDuration = spans
    .filter((s) => s.depth)
    .reduce((sum, s) => sum + Math.max(s.durationMs, 0), 0);
  const total = Math.max(rootDuration, childDuration) || 1;

  let cursor = 0;
  const rows = spans.map((span) => {
    const duration = Math.max(span.durationMs, 0);
    if (!span.depth) {
      // Roots always span the full axis; they are the window, not a segment.
      return { ...span, offset: 0, width: 100 };
    }
    const offset = (cursor / total) * 100;
    const width = (duration / total) * 100;
    cursor += duration;
    return { ...span, offset, width: Math.max(width, 1.2) };
  });

  return (
    <div className="trace">
      <div className="trace__axis">
        <span>0 ms</span>
        <span>{traceId ? `trace ${traceId.slice(0, 12)}…` : "elapsed"}</span>
        <span>{total.toFixed(2)} ms</span>
      </div>
      {rows.map((row, i) => (
        <div className="span" key={`${row.name}-${i}`}>
          <span className="span__name" title={row.detail ?? row.name}>
            {row.depth ? "└ ".padStart(row.depth * 2 + 2, " ") : ""}
            <b>{row.name}</b>
            {row.detail ? ` ${row.detail}` : ""}
          </span>
          <span className="span__track">
            <span
              className={`span__bar span__bar--${row.tone ?? "signal"}`}
              style={{
                left: `${row.offset}%`,
                width: `${row.width}%`,
                animationDelay: `${i * 45}ms`,
              }}
            />
          </span>
          <span className="span__dur">{row.durationMs.toFixed(2)}ms</span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ verdict */

export function Verdict({
  allowed,
  title,
  reason,
  children,
}: {
  allowed: boolean;
  title: string;
  reason?: string | null;
  children?: ReactNode;
}) {
  return (
    <div className={`verdict verdict--${allowed ? "pass" : "block"}`} role="status">
      <span className="verdict__title">{title}</span>
      {reason ? <span className="verdict__reason">{reason}</span> : null}
      {children}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <p className="error">
      <code>error</code> {message}
    </p>
  );
}
