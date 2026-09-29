import { useEffect, useState } from 'react';
import { api, callStats } from '../api/client';
import { useLive, useNow } from '../api/live';
import { ops, type CopilotInfo } from '../api/ops';
import type { DecisionRecord } from '../api/types';
import { mockDecisions } from '../mocks';
import { Chip, Skeleton, healthTone } from '../components/ui';
import { activity } from '../lib/derive';
import { ago } from '../lib/format';

// What each component falls back to (blueprint section 08), shown while it is healthy.
const FALLBACK: Record<string, string> = {
  'Backend API': 'Serves cached state; never blocks on the simulator.',
  Simulator: 'If it fails: cached state marked stale, writes held, circuit breaker opens.',
  'Operational state': 'If stale: recommend only, nothing executes.',
  'Event stream': 'If the stream drops: REST polling continues, reconnect with backoff.',
  Database: 'If it is down: decisions buffer in memory + file and flush later.',
  'Decision engine': 'If it fails: greedy fallback policy; confidence drops.',
  Explanation: 'If the LLM fails: deterministic template explanations.',
  Forecaster: 'If it fails: in-process profile predictor.',
};
const CRITICAL = ['Backend API', 'Simulator', 'Operational state', 'Decision engine'];

/** Is our own application healthy and resilient? Deliberately calm. */
export function SystemHealth() {
  const { health, snap, source, current, lastOkAt } = useLive();
  const now = useNow();
  const [copilot, setCopilot] = useState<CopilotInfo | null>(null);
  const [records, setRecords] = useState<DecisionRecord[]>([]);
  const tick = snap?.tick;
  useEffect(() => { if (source === 'live') ops.copilotInfo().then(setCopilot).catch(() => setCopilot(null)); }, [source]);
  useEffect(() => {
    if (source === 'mock') { setRecords(mockDecisions); return; }
    if (source === 'live') api.decisions(50).then(setRecords).catch(() => undefined);
  }, [source, tick]);
  if (!health || !snap) return <div className="card"><Skeleton lines={6} /></div>;

  const crit = health.components.filter((c) => CRITICAL.includes(c.name));
  const critOk = crit.every((c) => c.status === 'healthy') && source === 'live' && !snap.freshness?.stale;
  const degraded = health.components.filter((c) => c.status === 'degraded' || c.status === 'down');
  const stats = callStats();
  const feed = activity(snap, current, records, 10);
  const f = snap.freshness;

  return (
    <>
      <div className="page-head">
        <div><h1>System Health</h1><p>Is our own software healthy, and what does it do when something fails?</p></div>
        <Chip tone={healthTone(health.status)}>{health.status}</Chip>
      </div>

      <section className={`card ${critOk ? 'lime' : ''}`} style={{ marginBottom: 14 }}>
        <div className="row" style={{ gap: 14 }}>
          <span className={`pulse ${critOk ? '' : degraded.some((c) => CRITICAL.includes(c.name)) || snap.freshness?.stale ? 'crit' : 'warn'}`} style={{ width: 12, height: 12 }} />
          <div className="stack" style={{ gap: 3 }}>
            <h2>{source !== 'live' ? (source === 'offline' ? 'Backend unreachable' : 'Mock data (no backend)') : critOk ? 'All critical services operational' : 'Running degraded'}</h2>
            <p className="small muted">{critOk
              ? degraded.length ? `${degraded.map((c) => c.name).join(', ')} degraded, with a fallback in place.` : 'Every component reports healthy.'
              : degraded.map((c) => `${c.name}: ${c.detail ?? c.status}`).join(' · ') || 'Waiting for the next health check.'}</p>
          </div>
        </div>
      </section>

      <div className="kpis three">
        <div className="kpi"><span className="kicker">API p95</span><span className="v">{stats.p95 == null ? '—' : `${Math.round(stats.p95)}`} <small>ms</small></span><span className="s">measured by this browser, last {stats.n} calls</span></div>
        <div className="kpi"><span className="kicker">Error rate</span><span className="v">{stats.errorRate == null ? '—' : `${(stats.errorRate * 100).toFixed(1)}`} <small>%</small></span><span className="s">5xx or unreachable, same window</span></div>
        <div className="kpi"><span className="kicker">Last sync</span><span className="v" style={{ fontSize: 26, lineHeight: '32px' }}>{ago(health.snapshot_age_seconds)}</span><span className="s">simulator → backend · backend answered {lastOkAt ? ago((now - lastOkAt) / 1000) : '—'}</span></div>
      </div>

      <div className="mc">
        <section className="card s6">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Components</span><h3>{health.components.length} components</h3></div></div>
          <div className="list">{health.components.map((c) => (
            <div key={c.name} className="row between" style={{ flexWrap: 'nowrap', alignItems: 'flex-start' }}>
              <div className="stack grow" style={{ gap: 2 }}><span style={{ fontWeight: 600 }}>{c.name === 'Simulator' ? 'BUP Simulator' : c.name}</span>
                <span className="xsmall muted">{c.status === 'healthy' ? FALLBACK[c.name] ?? c.detail ?? '' : c.detail ?? FALLBACK[c.name] ?? ''}</span></div>
              <Chip tone={healthTone(c.status)}>{c.status === 'unknown' ? 'n/a' : c.status}</Chip>
            </div>))}</div>
        </section>
        <section className="card s6">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Recent activity</span><h3>What the system did</h3></div><span className="xsmall muted">simulated ticks</span></div>
          {feed.length ? <div className="feed">{feed.map((a, i) => <div key={i}><span className="tk">t{a.tick}</span><span className={`d ${a.tone}`} /><span>{a.text}</span></div>)}</div>
            : <p className="empty">Nothing yet. Run a scenario in the Simulation Lab.</p>}
        </section>
      </div>

      <details className="more card" style={{ marginTop: 14 }}>
        <summary>Details: deployment, data freshness, copilot</summary>
        <div className="mc" style={{ marginTop: 14 }}>
          <div className="s5">
            <dl className="stat">
              <dt>Deployment</dt><dd className="mono">{health.version ?? '—'}</dd>
              <dt>Active policy</dt><dd className="mono">{health.active_policy ?? '—'}</dd>
              <dt>Pacer</dt><dd>{health.pacer_running ? 'stepping' : 'off'}</dd>
              <dt>Simulator circuit</dt><dd>{f?.circuit?.toLowerCase().replace('_', '-') ?? '—'}</dd>
              <dt>Simulator clock</dt><dd>{snap.sim_status.toLowerCase()} · tick {snap.tick}</dd>
              <dt>Copilot</dt><dd>{copilot ? `${copilot.engine}${copilot.llm ? ` · ${copilot.llm}` : ' · templates'}` : '—'}</dd>
              <dt>LangSmith tracing</dt><dd>{copilot ? (copilot.tracing ? `on${copilot.project ? ` (${copilot.project})` : ''}` : 'off') : '—'}</dd>
            </dl>
          </div>
          <div className="s7">
            {f ? <div className="tbl"><table>
              <thead><tr><th>Resource</th><th>State</th><th className="n">Age</th></tr></thead>
              <tbody>{Object.entries(f.resources).map(([k, r]) => <tr key={k}><td className="mono">{k.replace(/_/g, '-')}</td><td><Chip tone={r.stale ? 'crit' : 'ok'}>{r.stale ? 'stale' : 'fresh'}</Chip></td><td className="n">{ago(r.age_seconds)}</td></tr>)}</tbody>
            </table></div> : <p className="empty">No freshness data.</p>}
          </div>
        </div>
        <p className="xsmall muted" style={{ marginTop: 10 }}>Latency percentiles, fallbacks, confidence and Twin error over time are in Grafana (port 3001 in Docker).</p>
      </details>
    </>
  );
}
