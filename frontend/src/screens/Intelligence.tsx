import { useEffect, useMemo, useState } from 'react';
import { api, ApiError, operatorKey } from '../api/client';
import { useLive } from '../api/live';
import type { Autonomy, NetworkSnapshot, Recommendation, TwinFuture } from '../api/types';
import { Explanation, Futures, GateSummary, Review } from '../components/Decision';
import { Scoreboard } from '../components/Scoreboard';
import { StateMachine } from '../components/StateMachine';
import { Chip, FactorBars, ModeChip, Skeleton, confTone, toneColor } from '../components/ui';
import { constraintChecks, demandEvidence, focus, type DemandEvidence, type DemandObs } from '../lib/derive';
import { chosenFuture, fuelName, futureLabel, hours, humanize, litres, noopFuture, pct, placeName, recFutures, recLegs, recPolicy } from '../lib/format';
import { go } from '../lib/router';
import { modeLine } from './Overview';

type StepKey = 'detect' | 'predict' | 'decide' | 'simulate' | 'human';
type StepState = 'done' | 'wait' | 'off';

/** The hero page: every piece of intelligence behind the current recommendation, as input → finding → output. */
export function Intelligence() {
  const { snap, current, currentError, source } = useLive();
  const [step, setStep] = useState<StepKey>('detect');
  const [showConf, setShowConf] = useState(false);
  const [simulated, setSimulated] = useState<string | null>(null);
  const [obs, setObs] = useState<DemandObs[] | null>(null);
  const rec = current?.recommendation ?? null;
  const legs = useMemo(() => (rec ? recLegs(rec) : []), [rec]);
  const f = snap ? focus(snap, rec, legs) : null;
  const tick = snap?.tick;

  useEffect(() => {
    if (!f || source !== 'live') { setObs(null); return; }
    api.demandHistory(f.station_id, 800).then(setObs).catch(() => setObs(null));
  }, [f?.station_id, source, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!snap || (!current && !currentError)) return <div className="card"><Skeleton lines={6} /></div>;
  const a = current?.autonomy;
  const ev = f && obs ? demandEvidence(obs, f.fuel) : null;

  if (!rec) {
    return (
      <>
        <Head a={a} onConf={() => setShowConf(!showConf)} />
        {showConf && a && <ConfidencePanel a={a} />}
        <div className="card"><h3>No recommendation right now</h3>
          <p className="muted" style={{ marginTop: 8 }}>{currentError ?? `Decision engine: ${current?.error ?? 'unavailable'}`}. The system keeps monitoring; nothing is sent without a recommendation.</p></div>
      </>
    );
  }

  const futures = recFutures(rec);
  const record = current!.record_stage;
  const st: Record<StepKey, [StepState, string]> = {
    detect: ev?.anomaly && f ? ['done', `Abnormal ${fuelName(f.fuel).toLowerCase()} demand at ${placeName(snap, f.station_id)} (${ev.changePct >= 0 ? '+' : ''}${Math.round(ev.changePct * 100)}%)`]
      : rec.signals.length ? ['done', short(humanize(snap, rec.signals[0].message))] : ['off', 'Nothing abnormal'],
    predict: f?.risk?.hours_to_stockout != null ? ['done', `${placeName(snap, f.station_id)} ${fuelName(f.fuel).toLowerCase()}: shortage in ${hours(f.risk.hours_to_stockout)}`] : ['off', 'No stockout projected'],
    decide: legs.length ? ['done', legs.length === 1 ? `Send ${litres(legs[0].quantity)} ${fuelName(legs[0].fuel_type).toLowerCase()}` : `${legs.length} shipments · ${litres(legs.reduce((s, l) => s + l.quantity, 0))}`] : ['off', 'No action needed'],
    simulate: futures.length ? (simulated === rec.id ? ['done', impactShort(rec)] : ['wait', 'Ready to simulate']) : ['off', 'Twin unavailable'],
    human: !legs.length ? ['off', 'Nothing to approve']
      : record && record !== 'gated' ? ['done', record === 'rejected' ? 'Rejected' : (current!.gate?.auto_execute ? 'Auto-executed' : 'Approved & sent')]
      : current!.gate?.requires_human ? ['wait', 'Awaiting operator approval'] : ['wait', 'Cleared for autopilot'],
  };
  const order: [StepKey, string][] = [['detect', 'Detect'], ['predict', 'Predict'], ['decide', 'Decide'], ['simulate', 'Simulate'], ['human', 'Approve']];
  const idx = order.findIndex(([k]) => k === step);

  return (
    <>
      <Head a={a} onConf={() => setShowConf(!showConf)} tick={rec.tick} id={rec.id} />
      {showConf && a && <ConfidencePanel a={a} />}

      <div className="pipeline" role="tablist" aria-label="Decision pipeline">
        {order.map(([k, label], i) => {
          const [state, text] = st[k];
          return (
            <button key={k} role="tab" aria-selected={step === k} onClick={() => setStep(k)}>
              <div className="row"><span className={`n ${state}`}>{state === 'done' ? '✓' : state === 'wait' ? '●' : i + 1}</span><span className="kicker">0{i + 1} · {label}</span></div>
              <span className="t">{text}</span>
            </button>
          );
        })}
      </div>

      <div className="fade-in" key={step}>
        {step === 'detect' && <Detect snap={snap} rec={rec} f={f} ev={ev} loading={source === 'live' && !obs && !!f} live={source === 'live'} />}
        {step === 'predict' && <Predict snap={snap} rec={rec} f={f} />}
        {step === 'decide' && <Decide snap={snap} rec={rec} />}
        {step === 'simulate' && <Simulate snap={snap} rec={rec} done={simulated === rec.id} onRun={() => setSimulated(rec.id)} />}
        {step === 'human' && (
          <div className="mc">
            <section className="card s7"><div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">05 · Human review</span><h3>Approve, modify or reject</h3></div></div>
              <Review rec={rec} gate={current!.gate} stage={record} /></section>
            <div className="s5 stack" style={{ gap: 14 }}>
              <section className="card"><div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Confidence gate</span><h3>Who is allowed to act</h3></div>{a && <ModeChip mode={a.mode} />}</div>
                {record && record !== 'gated'
                  ? <p className={`note ${record === 'rejected' ? 'crit' : 'ok'}`}>Decided: <b>{record}</b>. The gate required {current!.gate?.requires_human ? 'a human' : 'no human'} at confidence {Math.round((current!.gate?.confidence ?? 0) * 100)}%.</p>
                  : current!.gate ? <GateSummary gate={current!.gate} /> : <p className="empty">No gate result.</p>}
                {a && <p className="small muted" style={{ marginTop: 12 }}>{modeLine(a.mode)}</p>}</section>
              <button className="link" onClick={() => go('/history')}>View decision history and replay →</button>
            </div>
          </div>
        )}
      </div>

      <div className="row between" style={{ marginTop: 14 }}>
        <button className="btn ghost" disabled={idx === 0} onClick={() => setStep(order[idx - 1][0])}>← {idx > 0 ? order[idx - 1][1] : ''}</button>
        {idx < order.length - 1 && <button className="btn dark" onClick={() => { if (order[idx + 1][0] === 'simulate') setStep('simulate'); else setStep(order[idx + 1][0]); }}>Next: {order[idx + 1][1]} →</button>}
      </div>
    </>
  );
}

const short = (s: string) => (s.length > 64 ? s.slice(0, 62) + '…' : s);
function impactShort(rec: Recommendation) {
  const n = noopFuture(rec), c = chosenFuture(rec);
  return n && c ? `${litres(n.network_unmet_liters)} → ${litres(c.network_unmet_liters)} unmet` : 'Projected';
}

function Head({ a, onConf, tick, id }: { a?: Autonomy; onConf: () => void; tick?: number; id?: string }) {
  return (
    <div className="page-head">
      <div>
        <h1>Intelligence</h1>
        <p>How the system reached its current recommendation{tick != null ? ` at tick ${tick}` : ''}. Each step shows what went in, what it found, and what it produced.{id && <span className="faint mono" style={{ fontSize: 12 }}> · {id}</span>}</p>
      </div>
      {a && (
        <button className="conf-pill" onClick={onConf} aria-label="Show decision confidence">
          <Ring v={a.confidence} />
          <span>{Math.round(a.confidence * 100)}% · {a.mode === 'MANUAL' ? 'Manual safe mode' : a.mode.charAt(0) + a.mode.slice(1).toLowerCase()}</span>
        </button>
      )}
    </div>
  );
}

function Ring({ v }: { v: number }) {
  const r = 12, c = 2 * Math.PI * r;
  return (
    <svg className="ring" viewBox="0 0 30 30" aria-hidden="true">
      <circle cx="15" cy="15" r={r} fill="none" stroke="var(--sunken)" strokeWidth="4" />
      <circle cx="15" cy="15" r={r} fill="none" stroke={toneColor(confTone(v))} strokeWidth="4" strokeLinecap="round"
        strokeDasharray={`${c * v} ${c}`} transform="rotate(-90 15 15)" />
    </svg>
  );
}

function ConfidencePanel({ a }: { a: Autonomy }) {
  const { source, refresh } = useLive();
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setMsg(null);
    try { await fn(); refresh(); } catch (e) { setMsg(e instanceof ApiError ? (e.status === 401 ? 'Operator key missing or wrong (enter it in the review step).' : e.message) : 'failed'); }
  };
  return (
    <section className="card fade-in" style={{ marginBottom: 14 }}>
      <div className="mc">
        <div className="s5 stack" style={{ gap: 12 }}>
          <div className="row between"><span className="q">Decision confidence</span><ModeChip mode={a.mode} /></div>
          <div className="row" style={{ alignItems: 'baseline', gap: 10 }}>
            <span className="big" style={{ color: toneColor(confTone(a.confidence)) }}>{Math.round(a.confidence * 100)}%</span>
            <span className="small muted">{a.mode === 'MANUAL' ? 'Manual safe mode' : a.mode.charAt(0) + a.mode.slice(1).toLowerCase()}</span>
          </div>
          <FactorBars factors={a.factors} />
          <p className="small">{a.mode === 'AUTONOMOUS' ? 'Confidence is high and nothing abnormal is active: routine allocations may run inside guardrails.'
            : a.mode === 'SUPERVISED' ? 'Confidence is high enough for supervised recommendations. Human approval remains required.'
            : 'Confidence is too low or data is stale: the system recommends only, and nothing executes without a human.'}</p>
        </div>
        <div className="s7 stack" style={{ gap: 12 }}>
          <StateMachine a={a} />
          <div className="row">
            <button className="btn sm" disabled={source !== 'live' || !operatorKey.get() || a.mode === 'AUTONOMOUS'} onClick={() => act(api.rearm)} title="Needs confidence ≥ 80%, fresh data and no crisis">Re-arm Autonomous</button>
            <button className="btn sm" disabled={source !== 'live' || !operatorKey.get() || a.mode === 'MANUAL'} onClick={() => act(() => api.setMode('MANUAL'))}>Switch to Manual</button>
            <span className="xsmall muted">{a.autopilot === false ? 'Autopilot off' : 'Autopilot acts only in Autonomous mode'}</span>
          </div>
          {msg && <p className="note warn">{msg}</p>}
          {a.log.length > 0 && <div className="log">{a.log.slice(0, 5).map((l, i) => <div key={i}><b>t{l.tick ?? '—'}</b>{l.message.replace('->', '→')}</div>)}</div>}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 01 Detect

function Detect({ snap, rec, f, ev, loading, live }: { snap: NetworkSnapshot; rec: Recommendation; f: ReturnType<typeof focus>; ev: DemandEvidence | null; loading: boolean; live: boolean }) {
  const fuel = f ? fuelName(f.fuel).toLowerCase() : '';
  const at = f ? placeName(snap, f.station_id) : 'the network';
  return (
    <div className="mc">
      <section className="card s7">
        <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">01 · Detect</span>
          <h2>{ev?.anomaly ? `Abnormal ${fuel} demand at ${at}` : rec.signals[0]?.message ? humanize(snap, rec.signals[0].message) : `No demand anomaly for ${fuel} at ${at}`}</h2></div>
          {ev && <Chip tone={ev.anomaly ? 'crit' : 'ok'}>{ev.anomaly ? 'Anomaly' : 'Normal'}</Chip>}</div>
        {loading ? <Skeleton lines={4} /> : ev ? (
          <div className="stack" style={{ gap: 14 }}>
            <dl className="stat">
              <dt>Current demand (last 4 ticks)</dt><dd>{Math.round(ev.current)} L/tick</dd>
              <dt>Normal range (p10–p90, previous {ev.baselineTicks} ticks)</dt><dd>{Math.round(ev.low)}–{Math.round(ev.high)} L/tick</dd>
              <dt>Change vs typical</dt><dd style={{ color: Math.abs(ev.changePct) > 0.15 ? 'var(--crit)' : undefined }}>{ev.changePct >= 0 ? '+' : ''}{Math.round(ev.changePct * 100)}%</dd>
            </dl>
            <DemandSpark ev={ev} />
          </div>
        ) : <p className="empty">{!live ? 'Demand evidence needs the live backend (mock data now).' : f ? 'Not enough demand history yet for this station.' : 'No station is at risk right now.'}</p>}
        <details className="more" style={{ marginTop: 14 }}><summary>How was this calculated?</summary>
          <p className="small muted" style={{ marginTop: 8 }}>Input: the simulator's demand history for this station (<code>/v1/demand-history</code>). Recent demand is compared with the normal band of the ticks before it. The intelligence service's detector flags the same data with residual z-scores and reads live event and route status changes.</p></details>
      </section>
      <section className="card s5">
        <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Signals from the detector</span><h3>{rec.signals.length} signal{rec.signals.length === 1 ? '' : 's'}</h3></div></div>
        {rec.signals.length ? <div className="list">{rec.signals.slice(0, 6).map((s, i) => (
          <div key={i} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }}><Chip tone={s.severity === 'crit' ? 'crit' : s.severity === 'warn' ? 'warn' : 'idle'}>{s.kind.replace(/_/g, ' ')}</Chip><span className="small grow">{humanize(snap, s.message)}</span></div>))}</div>
          : <p className="empty">Nothing unusual detected this tick.</p>}
      </section>
    </div>
  );
}

function DemandSpark({ ev }: { ev: DemandEvidence }) {
  const W = 560, H = 120, p = 6;
  const max = Math.max(ev.high * 1.2, ...ev.series.map((s) => s.v), 1);
  const x = (i: number) => p + (i / Math.max(1, ev.series.length - 1)) * (W - 2 * p);
  const y = (v: number) => H - p - (v / max) * (H - 2 * p);
  const pts = ev.series.map((s, i) => `${x(i)},${y(s.v)}`).join(' ');
  const last = ev.series.length - 4;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Demand per tick with normal band">
      <rect x={p} y={y(ev.high)} width={W - 2 * p} height={Math.max(1, y(ev.low) - y(ev.high))} fill="var(--lime-soft)" />
      <text x={W - p} y={y(ev.high) - 4} textAnchor="end" style={{ font: '600 10px var(--f-body)', fill: 'var(--ok)' }}>normal range</text>
      <polyline points={pts} fill="none" stroke="var(--ink-3)" strokeWidth={1.6} />
      <polyline points={ev.series.slice(last).map((s, i) => `${x(last + i)},${y(s.v)}`).join(' ')} fill="none" stroke={ev.anomaly ? 'var(--crit)' : 'var(--ink)'} strokeWidth={2.6} />
      <circle cx={x(ev.series.length - 1)} cy={y(ev.series[ev.series.length - 1].v)} r={4} fill={ev.anomaly ? 'var(--crit)' : 'var(--ink)'} />
    </svg>
  );
}

// ---------------------------------------------------------------- 02 Predict

function Predict({ snap, rec, f }: { snap: NetworkSnapshot; rec: Recommendation; f: ReturnType<typeof focus> }) {
  const r = f?.risk;
  const st = f ? snap.stations.find((s) => s.id === f.station_id) : undefined;
  const inv = r?.current_inventory && r.current_inventory > 0 ? r.current_inventory : st?.inventory[f!.fuel] ?? 0;
  const risks = [...rec.risks].filter((x) => x.hours_to_stockout != null && x.hours_to_stockout < 99).sort((a, b) => b.p_stockout - a.p_stockout).slice(0, 5);
  return (
    <div className="mc">
      <section className="card s7">
        <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">02 · Predict</span>
          <h2>{r?.hours_to_stockout != null ? `${placeName(snap, f!.station_id)} ${fuelName(f!.fuel).toLowerCase()} may run out in ${hours(r.hours_to_stockout)}` : 'No stockout projected inside the horizon'}</h2></div></div>
        {r?.hours_to_stockout != null ? (
          <div className="stack" style={{ gap: 14 }}>
            <InventoryProjection inv={inv} hrs={r.hours_to_stockout} capacity={st?.capacity[f!.fuel] ?? inv} />
            <dl className="stat">
              <dt>Probability of stockout in the horizon</dt><dd style={{ color: toneColor(r.p_stockout >= 0.5 ? 'crit' : 'warn') }}>{Math.round(r.p_stockout * 100)}%</dd>
              <dt>In the tank now</dt><dd>{litres(inv)}</dd>
              <dt>Already on the way</dt><dd>{litres(snap.in_transit_totals[f!.station_id]?.[f!.fuel] ?? 0)}</dd>
              <dt>Backup route</dt><dd>{snap.routes.filter((x) => x.destination_station_id === f!.station_id).length > 1 ? 'Yes' : 'No, single route'}</dd>
              <dt>Forecast model</dt><dd className="mono">{rec.versions.forecast_model ?? 'fc-v1'}</dd>
            </dl>
          </div>
        ) : <p className="empty">Every station has enough fuel, including shipments already on the way, for the next 24 ticks.</p>}
        <details className="more" style={{ marginTop: 14 }}><summary>How was this calculated?</summary>
          <p className="small muted" style={{ marginTop: 8 }}>Input: forecast demand per station and fuel with p10–p90 bands, current stock, fuel in transit and scheduled supply. The risk engine projects stock tick by tick and reports time to stockout and the probability of hitting zero inside the horizon. The line shows the projected decline to that point.</p></details>
      </section>
      <section className="card s5">
        <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Risk ranking</span><h3>Station × fuel at risk</h3></div></div>
        {risks.length ? <div className="list">{risks.map((x) => (
          <div key={x.station_id + x.fuel_type} className="row between">
            <span className="small">{placeName(snap, x.station_id)} · {fuelName(x.fuel_type)}{snap.routes.filter((rt) => rt.destination_station_id === x.station_id).length === 1 && <span className="xsmall" style={{ color: 'var(--warn)' }}> · single route</span>}</span>
            <span className="row" style={{ gap: 8 }}><span className="small num">{hours(x.hours_to_stockout)}</span><Chip tone={x.p_stockout >= 0.5 ? 'crit' : x.p_stockout >= 0.25 ? 'warn' : 'idle'}>{Math.round(x.p_stockout * 100)}%</Chip></span>
          </div>))}</div> : <p className="empty">No station at risk.</p>}
      </section>
    </div>
  );
}

function InventoryProjection({ inv, hrs, capacity }: { inv: number; hrs: number; capacity: number }) {
  const W = 560, H = 170, pl = 48, pr = 14, pt = 12, pb = 26;
  const span = Math.max(4, Math.ceil(hrs * 1.35));
  const x = (h: number) => pl + (h / span) * (W - pl - pr);
  const y = (v: number) => pt + (1 - v / Math.max(inv, 1)) * (H - pt - pb);
  const crit = Math.min(inv * 0.9, 0.1 * capacity);
  const stepH = Math.max(1, Math.ceil(span / 8));
  const ticks = Array.from({ length: Math.floor(span / stepH) + 1 }, (_, i) => i * stepH);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label={`Inventory projected to reach zero in ${hrs.toFixed(1)} hours`}>
      <rect x={pl} y={y(crit)} width={W - pl - pr} height={H - pb - y(crit)} fill="var(--crit-bg)" />
      <text x={W - pr} y={y(crit) - 4} textAnchor="end" style={{ font: '600 10px var(--f-body)', fill: 'var(--crit)' }}>critical</text>
      {[0, 0.5, 1].map((g) => <text key={g} x={pl - 6} y={y(inv * g) + 3} textAnchor="end" style={{ font: '500 10px var(--f-mono)', fill: 'var(--ink-3)' }}>{Math.round(inv * g).toLocaleString()}</text>)}
      <line x1={x(0)} y1={y(inv)} x2={x(hrs)} y2={y(0)} stroke="var(--ink)" strokeWidth={2.4} strokeDasharray="0" />
      {Array.from({ length: 5 }, (_, i) => (i * hrs) / 4).map((h, i) => <circle key={i} cx={x(h)} cy={y(inv * (1 - h / hrs))} r={4} fill="var(--ink)" />)}
      <line x1={x(hrs)} y1={pt} x2={x(hrs)} y2={H - pb} stroke="var(--crit)" strokeDasharray="4 4" />
      <text x={x(hrs)} y={pt + 10} textAnchor={x(hrs) > W - 90 ? 'end' : 'start'} dx={x(hrs) > W - 90 ? -6 : 6} style={{ font: '600 11px var(--f-body)', fill: 'var(--crit)' }}>stockout ≈ +{hrs.toFixed(1)} h</text>
      {ticks.map((h) => <text key={h} x={x(h)} y={H - 8} textAnchor="middle" style={{ font: '500 10px var(--f-mono)', fill: 'var(--ink-3)' }}>{h === 0 ? 'now' : `+${h}h`}</text>)}
    </svg>
  );
}

// ---------------------------------------------------------------- 03 Decide

function Decide({ snap, rec }: { snap: NetworkSnapshot; rec: Recommendation }) {
  const { current } = useLive();
  const legs = recLegs(rec);
  if (!legs.length) {
    return (
      <section className="card"><span className="q">03 · Decide</span><h2 style={{ marginTop: 6 }}>No shipment needed right now</h2>
        <p className="muted" style={{ marginTop: 8 }}>Stock plus fuel already on the way covers projected demand, or nothing we could send would reduce the projected shortage.</p>
        <div style={{ marginTop: 14 }}><Explanation decisionId={rec.id} /></div></section>
    );
  }
  const lead = legs[0];
  const route = snap.routes.find((r) => r.id === lead.route_id);
  const depot = snap.depots.find((d) => d.id === lead.source_depot_id);
  const total = legs.reduce((s, l) => s + l.quantity, 0);
  const checks = legs.flatMap((l) => constraintChecks(snap, l, current?.gate ?? null));
  const passed = checks.filter((c) => c.ok).length;
  return (
    <div className="mc">
      <div className="s5 stack" style={{ gap: 14 }}>
        <div className="action">
          <span className="kicker" style={{ color: 'var(--lime)' }}>03 · Recommended action</span>
          <span className="headline">{legs.length === 1 ? `Move ${litres(lead.quantity)} ${fuelName(lead.fuel_type)}` : `${legs.length} shipments · ${litres(total)}`}</span>
          <span className="muted">{legs.length === 1 ? `${placeName(snap, lead.source_depot_id)} Depot → ${placeName(snap, lead.station_id)} Station` : 'From the depots below'}</span>
          {legs.length > 1 && <div className="stack" style={{ gap: 6 }}>{legs.map((l, i) => (
            <div key={i} className="row between small"><span>{placeName(snap, l.source_depot_id)} → {placeName(snap, l.station_id)} · {fuelName(l.fuel_type).toLowerCase()}</span><b className="num">{litres(l.quantity)}</b></div>))}</div>}
          <div className="row"><span className="chip act plain">policy {recPolicy(rec)}</span>{rec.mode === 'containment' && <span className="chip crit">containment</span>}</div>
        </div>
        <section className="card">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Evidence</span><h3>Why this is possible</h3></div></div>
          <dl className="stat">
            <dt>{placeName(snap, lead.source_depot_id)} depot {fuelName(lead.fuel_type).toLowerCase()} available</dt><dd>{litres(depot?.inventory[lead.fuel_type])}</dd>
            <dt>Route</dt><dd style={{ color: route?.status === 'AVAILABLE' ? 'var(--ok)' : 'var(--crit)' }}>{route?.status === 'AVAILABLE' ? `Available · ${route.transit_ticks} ticks` : 'Disrupted'}</dd>
            <dt>Shipment limit</dt><dd>{litres(route?.max_shipment)}</dd>
          </dl>
        </section>
      </div>
      <div className="s7 stack" style={{ gap: 14 }}>
        <section className="card">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Constraint checking</span><h3>{passed} of {checks.length} constraints checked ✓</h3></div>
            <Chip tone={passed === checks.length ? 'ok' : 'crit'}>{passed === checks.length ? 'all clear' : `${checks.length - passed} blocked`}</Chip></div>
          <div className="checks">{dedupe(checks).map((c, i) => <div key={i}><i className={c.ok ? 'y' : 'n'}>{c.ok ? '✓' : '✕'}</i><span>{c.label}</span></div>)}</div>
        </section>
        <section className="card">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Why this recommendation?</span><h3>Explanation</h3></div><span className="tag">read-only copilot</span></div>
          <Explanation decisionId={rec.id} />
        </section>
      </div>
    </div>
  );
}

const dedupe = <T extends { label: string }>(xs: T[]) => xs.filter((x, i) => xs.findIndex((y) => y.label === x.label) === i);

// ---------------------------------------------------------------- 04 Simulate

function Simulate({ snap, rec, done, onRun }: { snap: NetworkSnapshot; rec: Recommendation; done: boolean; onRun: () => void }) {
  const [running, setRunning] = useState(false);
  const n = noopFuture(rec), c = chosenFuture(rec);
  const tickH = (snap.tick_minutes || 15) / 60;
  const until = (fut?: TwinFuture) => (fut?.first_stockout_tick != null ? Math.max(0, (fut.first_stockout_tick - rec.tick) * tickH) : null);
  if (!n || !c) return <section className="card"><span className="q">04 · Simulate</span><p className="empty" style={{ marginTop: 8 }}>The Decision Twin did not run for this recommendation (component on fallback).</p></section>;
  const run = () => { setRunning(true); setTimeout(() => { setRunning(false); onRun(); }, 900); };
  const avoided = Math.max(0, n.network_unmet_liters - c.network_unmet_liters);
  return (
    <div className="stack" style={{ gap: 14 }}>
      <section className="card">
        <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">04 · Simulate impact</span>
          <h2>What happens if we act, and if we don't</h2>
          <p className="small muted">The Decision Twin copies the current network and projects the next {n.horizon_ticks} ticks ({(n.horizon_ticks * tickH).toFixed(0)} h) for each option. Nothing is sent.</p></div>
          <span className="tag twin">Projection</span></div>
        {!done ? (
          <div className="row" style={{ padding: '10px 0' }}>
            <button className="btn primary" onClick={run} disabled={running}>{running ? <><span className="spin" /> Running the Decision Twin…</> : 'Simulate impact'}</button>
            <span className="small muted">Compares doing nothing, the greedy baseline and the recommended plan across all {snap.stations.length} stations.</span>
          </div>
        ) : (
          <div className="stack fade-in" style={{ gap: 14 }}>
            <div className="vs">
              <div className="before">
                <span className="kicker" style={{ color: 'var(--crit)' }}>Without action</span>
                <dl className="stat">
                  <dt>Projected unmet demand</dt><dd>{litres(n.network_unmet_liters)}</dd>
                  <dt>First stockout</dt><dd>{until(n) != null ? `in ${hours(until(n))}` : 'none in horizon'}</dd>
                  <dt>Service level</dt><dd>{pct(n.service_level, 0)}</dd>
                </dl>
              </div>
              <div className="after">
                <span className="kicker" style={{ color: 'var(--ok)' }}>With recommendation · {futureLabel(c)}</span>
                <dl className="stat">
                  <dt>Projected unmet demand</dt><dd>{litres(c.network_unmet_liters)}</dd>
                  <dt>First stockout</dt><dd>{until(c) != null ? `in ${hours(until(c))}` : until(n) != null ? 'avoided' : 'none in horizon'}</dd>
                  <dt>Service level</dt><dd>{pct(c.service_level, 0)}</dd>
                </dl>
              </div>
            </div>
            <p style={{ fontSize: 15 }}><b>{litres(avoided)}</b> projected unmet demand avoided vs the no-action counterfactual. {avoided > 0 ? 'The recommended action improves expected service continuity.' : 'Acting does not change the projected outcome much this tick.'}</p>
            <Futures rec={rec} snap={snap} />
            <p className="xsmall muted">These are projections, not outcomes. When the horizon ends the Twin checks itself against what the simulator actually did, and that error feeds decision confidence.</p>
          </div>
        )}
      </section>
      {done && <Scoreboard compact />}
    </div>
  );
}
