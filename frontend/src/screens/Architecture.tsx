const COLS: { head: string; tag?: string; boxes: { t: string; d: string; kind?: 'ext' | 'hero' }[] }[] = [
  { head: 'Simulated world', tag: 'organizer', boxes: [
    { t: 'BUP Fuel Supply Simulator', d: 'Depots, stations, routes, demand, events. The only source of truth; never modified.', kind: 'ext' },
    { t: 'Write path', d: 'POST /v1/allocations with idempotency keys is the only thing that changes the world.', kind: 'ext' },
  ] },
  { head: 'Integration', tag: 'backend', boxes: [
    { t: 'Simulator client', d: 'Timeouts, retries, circuit breaker, validation, stale detection.' },
    { t: 'State store', d: 'Last good snapshot, fuel in transit, freshness per resource. REST is truth, SSE a hint.' },
  ] },
  { head: 'Intelligence', tag: 'every tick', boxes: [
    { t: 'Detect', d: 'Demand anomalies, route, station and depot changes.' },
    { t: 'Forecast + risk', d: 'Demand bands per station × fuel; time to stockout and P(stockout).' },
    { t: 'Optimize', d: 'LP (HiGHS) with a greedy fallback; containment when prevention is impossible.' },
    { t: 'Decision Twin', d: 'Projects do-nothing, greedy and LP futures network-wide; later checks itself.', kind: 'hero' },
  ] },
  { head: 'Decision', tag: 'human in the loop', boxes: [
    { t: 'Confidence gate', d: 'Six live factors → Autonomous / Supervised / Manual. Guardrails in every mode.', kind: 'hero' },
    { t: 'Review + records', d: 'Approve, modify or reject; every stage audited in Postgres (buffered if down).' },
    { t: 'Allocation writer', d: 'Pre-checks that prevent fuel loss; idempotent posts.' },
  ] },
  { head: 'Operator', tag: 'this app', boxes: [
    { t: 'Operator UI', d: 'Overview, Intelligence, Simulation Lab, System Health.' },
    { t: 'Copilot', d: 'Explains from structured facts only; LLM answers are checked for invented numbers.' },
    { t: 'Observability', d: 'Prometheus metrics, JSON logs, Grafana dashboards, k6 load tests.' },
  ] },
];

const FAILS: [string, string][] = [
  ['Simulator slow or refusing', 'Cached state marked stale, writes held, circuit breaker; health shows degraded, not down.'],
  ['Stale data', 'Manual safe mode: recommendations shown, execution locked.'],
  ['Forecaster down', 'In-process profile predictor; confidence drops; mode falls to Supervised.'],
  ['Optimizer fails', 'Greedy fallback policy keeps decisions coming.'],
  ['LLM down or unfaithful', 'Deterministic template explanations.'],
  ['Database down', 'Decisions keep working; records buffer and flush later.'],
];

export function Architecture() {
  return (
    <>
      <div className="page-head">
        <div><h1>Architecture</h1><p>From the simulated world to an operator decision and back. Each column hands the next one a typed contract.</p></div>
      </div>
      <div className="arch" style={{ marginBottom: 14 }}>
        {COLS.map((c, i) => (
          <div key={c.head} className="col">
            <div className="head"><span>{String(i + 1).padStart(2, '0')} · {c.head}</span>{c.tag && <span>{c.tag}</span>}</div>
            {c.boxes.map((b) => <div key={b.t} className={`box ${b.kind ?? ''}`}><b>{b.t}</b><span className={b.kind === 'hero' ? 'faint' : 'muted'}>{b.d}</span></div>)}
          </div>
        ))}
      </div>
      <div className="mc">
        <section className="card s7">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">One decision</span><h3>The loop</h3></div></div>
          <ol className="small" style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <li>Observe the network every tick (REST snapshot, freshness checked).</li>
            <li>Detect, forecast and score risk for every station × fuel.</li>
            <li>Plan with LP and greedy; the Decision Twin projects each option across the whole network.</li>
            <li>Score confidence and set the mode; the gate decides whether a human must approve.</li>
            <li>The operator approves, modifies or rejects; approved legs are posted with idempotency keys.</li>
            <li>After the horizon, the Twin compares its projection with what actually happened; the error feeds confidence.</li>
          </ol>
        </section>
        <section className="card s5">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Resilience</span><h3>When something fails</h3></div></div>
          <div className="list">{FAILS.map(([k, v]) => <div key={k} className="stack" style={{ gap: 2 }}><b className="small">{k}</b><span className="xsmall muted">{v}</span></div>)}</div>
        </section>
      </div>
    </>
  );
}
