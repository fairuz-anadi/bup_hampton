import { useState } from 'react';
import { api, ApiError, operatorKey } from '../api/client';
import { useLive } from '../api/live';
import { Card, Chip, FactorBars, ModeChip, Skeleton, confTone, sevTone, toneColor } from '../components/ui';
import { Explanation, Futures, GateSummary, ImpactLine, Review } from '../components/Decision';
import { NetworkMap } from '../components/NetworkMap';
import { StateMachine } from '../components/StateMachine';
import { Scoreboard } from '../components/Scoreboard';
import { fuelName, hours, litres, placeName, recConstraints, recLegs, recPolicy } from '../lib/format';
import { go } from '../lib/router';

export function RecommendationScreen() {
  const { snap, current, currentError, source } = useLive();
  if (!snap || (!current && !currentError)) return <div className="card"><Skeleton lines={6} /></div>;
  const rec = current?.recommendation;
  if (!rec) {
    return (
      <div className="stack">
        <div className="card"><h2>No recommendation</h2>
          <p className="muted" style={{ marginTop: 8 }}>{currentError ?? `Decision engine: ${current?.error ?? 'unavailable'}`}. The system keeps monitoring; operators can still act from the Network view.</p></div>
        {current && <Confidence />}
      </div>
    );
  }
  const gate = current!.gate;
  const constraints = recConstraints(rec);
  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}>
          <span className="kicker">Recommendation · tick {rec.tick}{source === 'mock' && ' · mock data'}</span>
          <h2>{rec.mode === 'containment' ? 'Contain the shortage' : 'Prevent the shortage'} <span className="faint mono" style={{ fontSize: 13, fontWeight: 500 }}>{rec.id}</span></h2>
        </div>
        <div className="row">
          {Object.entries({ policy: recPolicy(rec), ...rec.versions }).filter(([k], i, a) => a.findIndex(([x]) => x === k) === i).map(([k, v]) => <span key={k} className="chip idle plain mono">{k} {v}</span>)}
        </div>
      </div>
      {rec.built_on_stale_data && <p className="note crit"><b>Built on stale data.</b> Shown for reference; execution is locked until the data is fresh.</p>}
      {rec.fallback_used.length > 0 && <p className="note warn">Running on fallback: {rec.fallback_used.join(', ')}. Quality is reduced, not stopped.</p>}

      <div className="mc">
        <Card className="s7" q="What we would send" title="Plan" right={<ImpactLineSmall />}>
          <Review rec={rec} gate={gate} stage={current!.record_stage} />
        </Card>
        <div className="s5 stack" style={{ gap: 14 }}>
          <Card q="Human review" title="Gate">
            {current!.record_stage && current!.record_stage !== 'gated'
              ? <p className={`note ${current!.record_stage === 'rejected' ? 'crit' : 'ok'}`}>Decided: <b>{current!.record_stage}</b>. The full record is in <a href="#/history">History</a>.</p>
              : gate ? <GateSummary gate={gate} /> : <p className="empty">No gate result.</p>}
          </Card>
          <Confidence />
        </div>

        <Card className="s7" q="Why" title="Explanation">
          <Explanation decisionId={rec.id} />
        </Card>
        <Card className="s5" q="Where" title="Route on the map">
          <NetworkMap snap={snap} highlight={recLegs(rec).map((l) => l.route_id)} onStation={(id) => go(`/station/${id}`)} />
        </Card>

        <Card className="s12" q="Decision Twin" title="Three projected futures" right={<span className="tag twin">Projected, not outcomes</span>}>
          <div className="stack" style={{ gap: 12 }}>
            <ImpactLine rec={rec} />
            <Futures rec={rec} snap={snap} />
          </div>
        </Card>

        <div className="s12"><Scoreboard /></div>

        <Card className="s4" q="Signals" title="What triggered it">
          {rec.signals.length ? (
            <div className="list">{rec.signals.map((s, i) => <div key={i} className="row"><Chip tone={sevTone(s.severity)}>{s.kind.replace(/_/g, ' ')}</Chip><span className="small grow">{s.message}</span></div>)}</div>
          ) : <p className="empty">No signals.</p>}
        </Card>
        <Card className="s4" q="Risk" title="Stations at risk">
          {rec.risks.length ? (
            <div className="tbl"><table>
              <thead><tr><th>Station</th><th className="n">Stockout in</th><th className="n">P</th></tr></thead>
              <tbody>{[...rec.risks].sort((a, b) => b.p_stockout - a.p_stockout).slice(0, 8).map((r) => (
                <tr key={r.station_id + r.fuel_type}><td>{placeName(snap, r.station_id)} {fuelName(r.fuel_type).toLowerCase()}{!r.has_backup_route && <div className="xsmall" style={{ color: 'var(--warn)' }}>single route</div>}</td>
                  <td className="n">{hours(r.hours_to_stockout)}</td><td className="n">{r.p_stockout.toFixed(2)}</td></tr>))}</tbody>
            </table></div>
          ) : <p className="empty">No station at risk inside the horizon.</p>}
        </Card>
        <Card className="s4" q="Constraints" title="What limited the plan">
          {constraints.length ? <ul className="small" style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 5 }}>{constraints.map((c, i) => <li key={i}>{c}</li>)}</ul> : <p className="empty">No binding constraints reported.</p>}
          {rec.alternatives.length > 0 && <><p className="kicker" style={{ marginTop: 14 }}>Alternatives</p><ul className="small muted" style={{ margin: '6px 0 0', paddingLeft: 18 }}>{rec.alternatives.map((a, i) => <li key={i}>{a}</li>)}</ul></>}
        </Card>
      </div>
    </div>
  );
}

function ImpactLineSmall() {
  const { current } = useLive();
  const legs = current?.recommendation ? recLegs(current.recommendation) : [];
  return <span className="xsmall faint">{legs.length} leg(s) · {litres(legs.reduce((s, l) => s + l.quantity, 0))}</span>;
}

/** Decision confidence and autonomy mode (blueprint section 06). */
export function Confidence() {
  const { current, source, refresh } = useLive();
  const [msg, setMsg] = useState<string | null>(null);
  const a = current?.autonomy;
  if (!a) return null;
  const act = async (fn: () => Promise<unknown>) => {
    setMsg(null);
    try { await fn(); refresh(); } catch (e) { setMsg(e instanceof ApiError ? (e.status === 401 ? 'Operator key missing or wrong (set it in the plan panel).' : e.message) : 'failed'); }
  };
  return (
    <Card q="Confidence" title="Who is allowed to act" right={<ModeChip mode={a.mode} />}>
      <div className="stack" style={{ gap: 12 }}>
        <div className="row" style={{ alignItems: 'baseline', gap: 10 }}>
          <span className="big" style={{ color: toneColor(confTone(a.confidence)) }}>{a.confidence.toFixed(2)}</span>
          <span className="xsmall muted">Autonomous ≥ {a.thresholds.autonomous_min.toFixed(2)} · Manual &lt; {a.thresholds.manual_below.toFixed(2)}{a.target_mode && a.target_mode !== a.mode ? ` · heading to ${a.target_mode.toLowerCase()}` : ''}</span>
        </div>
        <StateMachine a={a} />
        <FactorBars factors={a.factors} />
        <p className="xsmall muted">{a.autopilot === false ? 'Autopilot off: even Autonomous decisions wait for a human.'
          : a.mode === 'AUTONOMOUS' ? 'Autopilot on: decisions the gate clears run automatically inside guardrails.'
          : 'Autopilot runs only in Autonomous mode; for now every plan waits for a human.'}</p>
        <div className="row">
          <button className="btn sm" disabled={source !== 'live' || !operatorKey.get() || a.mode === 'AUTONOMOUS'} onClick={() => act(api.rearm)} title="Needs confidence ≥ 0.80, fresh data and no crisis">Re-arm Autonomous</button>
          <button className="btn sm" disabled={source !== 'live' || !operatorKey.get() || a.mode === 'MANUAL'} onClick={() => act(() => api.setMode('MANUAL'))}>Switch to Manual</button>
          {!a.armed && a.mode !== 'AUTONOMOUS' && <span className="xsmall faint">Autonomous not armed</span>}
        </div>
        {msg && <p className="note warn">{msg}</p>}
        {a.log.length > 0 && <div className="log">{a.log.slice(0, 8).map((l, i) => <div key={i}><b>t{l.tick ?? '—'}</b>{l.message}</div>)}</div>}
      </div>
    </Card>
  );
}
