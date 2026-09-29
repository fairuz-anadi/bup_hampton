import { useEffect, useState } from 'react';
import { useLive, useNow } from '../api/live';
import { ops, type CopilotInfo } from '../api/ops';
import { Card, Chip, Skeleton, healthTone } from '../components/ui';
import { ago } from '../lib/format';

/** System Health (brief §15): component status, data freshness, circuit breaker, where data comes from. */
export function HealthScreen() {
  const { health, snap, source, lastOkAt } = useLive();
  const now = useNow();
  const [copilot, setCopilot] = useState<CopilotInfo | null>(null);
  useEffect(() => { if (source === 'live') ops.copilotInfo().then(setCopilot).catch(() => setCopilot(null)); }, [source]);
  if (!health || !snap) return <div className="card"><Skeleton lines={6} /></div>;
  const f = snap.freshness;
  return (
    <div className="mc">
      <Card className="s4" q="Overall" title="System status" right={<Chip tone={healthTone(health.status)}>{health.status}</Chip>}>
        <div className="stack" style={{ gap: 10 }}>
          <div className="row between small"><span className="muted">Data source</span><Chip tone={source === 'live' ? 'ok' : source === 'offline' ? 'crit' : 'idle'}>{source === 'live' ? 'live backend' : source === 'offline' ? 'backend unreachable' : 'mock data'}</Chip></div>
          <div className="row between small"><span className="muted">Last backend answer</span><span className="num">{lastOkAt ? ago((now - lastOkAt) / 1000) : '—'}</span></div>
          <div className="row between small"><span className="muted">Simulator circuit</span><Chip tone={f?.circuit === 'CLOSED' ? 'ok' : f?.circuit === 'HALF_OPEN' ? 'warn' : 'crit'}>{f?.circuit?.toLowerCase().replace('_', '-') ?? '—'}</Chip></div>
          <div className="row between small"><span className="muted">Snapshot</span><span className="num">tick {health.tick ?? snap.tick} · {ago(health.snapshot_age_seconds)}</span></div>
          <div className="row between small"><span className="muted">Deployment</span><span className="mono xsmall">{health.version ?? '—'}</span></div>
          <div className="row between small"><span className="muted">Active policy</span><span className="mono xsmall">{health.active_policy ?? '—'}</span></div>
          <div className="row between small"><span className="muted">Pacer</span><Chip tone={health.pacer_running ? 'act' : 'idle'}>{health.pacer_running ? 'stepping' : 'off'}</Chip></div>
          {copilot && <div className="row between small"><span className="muted">Copilot</span><span className="xsmall">{copilot.engine}{copilot.llm ? ` · ${copilot.llm}` : ' · templates'}{copilot.tracing ? ` · LangSmith${copilot.project ? ` (${copilot.project})` : ''}` : ''}</span></div>}
          <div className="row between small"><span className="muted">Simulator clock</span><Chip tone={snap.sim_status === 'RUNNING' ? 'ok' : 'idle'}>{snap.sim_status.toLowerCase()}</Chip></div>
          {f?.stale && <p className="note crit"><b>Data is stale.</b> {f.reasons.join('; ')}. Recommendations are recommend-only until it is fresh.</p>}
        </div>
      </Card>
      <Card className="s8" q="Components" title="Every dependency, and what happens when it fails">
        <div className="tbl"><table>
          <thead><tr><th>Component</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>{health.components.map((c) => (
            <tr key={c.name}><td>{c.name}</td><td><Chip tone={healthTone(c.status)}>{c.status}</Chip></td><td className="small muted">{c.detail ?? FALLBACK[c.name] ?? '—'}</td></tr>
          ))}</tbody>
        </table></div>
      </Card>
      <Card className="s12" q="Freshness" title="Per resource, from the last simulator sync">
        {f ? (
          <div className="tbl"><table>
            <thead><tr><th>Resource</th><th>State</th><th className="n">Age</th><th>Last error</th></tr></thead>
            <tbody>{Object.entries(f.resources).map(([k, r]) => (
              <tr key={k}><td className="mono">{k.replace(/_/g, '-')}</td><td><Chip tone={r.stale ? 'crit' : 'ok'}>{r.stale ? 'stale' : 'fresh'}</Chip></td>
                <td className="n">{ago(r.age_seconds)}</td><td className="small muted">{r.last_error ?? '—'}</td></tr>
            ))}</tbody>
          </table></div>
        ) : <p className="empty">No freshness data.</p>}
      </Card>
    </div>
  );
}

// Shown when a component is healthy: what the system does if it fails (blueprint section 08).
const FALLBACK: Record<string, string> = {
  Simulator: 'If it fails: cached state marked stale, writes held, circuit breaker opens.',
  'Operational state': 'If stale: recommend only, nothing auto-executes.',
  'Event stream': 'If SSE drops: REST polling continues; reconnect with backoff.',
  'Decision engine': 'If it fails: greedy fallback policy; confidence drops.',
  Explanation: 'If the LLM fails: deterministic template explanations.',
};
