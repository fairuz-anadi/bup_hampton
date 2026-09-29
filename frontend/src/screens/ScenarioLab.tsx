import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError, operatorKey } from '../api/client';
import { useLive, useNow, type RecChange, type ScenarioRun } from '../api/live';
import { ops, type ChaosTimeline, type PacerStatus, type PolicyStatus, type TimelineFault } from '../api/ops';
import type { CurrentView, ExplainResponse, HealthReport, NetworkSnapshot, SimEvent } from '../api/types';
import { Chip, RichText, Skeleton } from '../components/ui';
import { approxHours, faultTitle, planName, stepsLabel, stepsWithTime, techName } from '../lib/copy';
import { describeActive, eventGroups, summarize } from '../lib/derive';
import { fuelName, placeName, recLegs, routeName, simClock } from '../lib/format';
import { EVENT_PRESETS, FAULT_PRESETS, type EventPreset, type FaultPreset } from '../lib/playbooks';
import { go } from '../lib/router';

const svg = (d: ReactNode) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{d}</svg>;
const ICON: Record<string, ReactNode> = {
  spike: svg(<><path d="M3 17l6-6 4 4 8-8" /><path d="M15 7h6v6" /></>),
  outage: svg(<><path d="M4 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16" /><path d="M3 21h12M7 8h4" /><path d="M14 10h2a2 2 0 0 1 2 2v4a1.5 1.5 0 0 0 3 0V9l-3-3" /></>),
  route: svg(<><circle cx="6" cy="19" r="2" /><circle cx="18" cy="5" r="2" /><path d="M8 19h5a4 4 0 0 0 0-8H11a4 4 0 0 1 0-8h5" /><path d="M9 9l6 6M15 9l-6 6" /></>),
  stream: svg(<><path d="M2 8.8a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0" /><path d="M3 3l18 18" /></>),
};

type Kind = 'event' | 'fault' | 'forecaster';
interface Scenario {
  id: string; kind: Kind; title: string; icon?: string; event?: EventPreset; fault?: FaultPreset; forecaster?: 'disable' | 'exit';
  blurb: (s: NetworkSnapshot) => string; running: (s: NetworkSnapshot) => string;
}
const ev = (id: string) => EVENT_PRESETS.find((p) => p.id === id)!;
const ft = (id: string) => FAULT_PRESETS.find((p) => p.id === id)!;
const ids = (p: Record<string, unknown>, k: string) => (p[k] as string[] | undefined) ?? [];
const region = (s: NetworkSnapshot, p: Record<string, unknown>) => ids(p, 'region_ids').map((r) => s.regions.find((x) => x.id === r)?.name ?? r).join(', ');

const FEATURED: Scenario[] = [
  { id: 'spike', kind: 'event', title: 'Demand surge', icon: 'spike', event: ev('spike'),
    blurb: (s) => `Increase demand in ${region(s, ev('spike').parameters)} to ${ev('spike').parameters.multiplier}× normal.`,
    running: (s) => `Demand surge in ${region(s, ev('spike').parameters)}` },
  { id: 'outage', kind: 'event', title: 'Station unavailable', icon: 'outage', event: ev('outage'),
    blurb: (s) => `Temporarily close ${placeName(s, ids(ev('outage').parameters, 'station_ids')[0])} station.`,
    running: (s) => `${placeName(s, ids(ev('outage').parameters, 'station_ids')[0])} station unavailable` },
  { id: 'route', kind: 'event', title: 'Route closed', icon: 'route', event: ev('route'),
    blurb: (s) => `Temporarily block the ${routeName(s, ids(ev('route').parameters, 'route_ids')[0])} route.`,
    running: (s) => `${routeName(s, ids(ev('route').parameters, 'route_ids')[0])} route closed` },
  { id: 'stream', kind: 'fault', title: 'Data connection lost', icon: 'stream', fault: ft('stream_disconnect'),
    blurb: () => 'Simulate loss of live simulator updates.', running: () => 'Live simulator updates lost' },
];
const MORE: Scenario[] = [
  ...['depot', 'delay', 'short'].map((id) => ({ id, kind: 'event' as const, title: ev(id).name.replace('Depot constraint', 'Depot constrained').replace('Shipment delay', 'Shipment delayed'),
    event: ev(id), blurb: () => '', running: (s: NetworkSnapshot) => `${ev(id).name}: ${[...ids(ev(id).parameters, 'depot_ids')].map((d) => placeName(s, d)).join(', ')}` })),
  ...['stale_data', 'unavailable', 'error_rate', 'latency'].map((id) => ({ id, kind: 'fault' as const, title: faultTitle(id), fault: ft(id), blurb: () => '', running: () => faultTitle(id) })),
  { id: 'fc-disable', kind: 'forecaster', title: 'Forecaster offline (60 s)', forecaster: 'disable', blurb: () => '', running: () => 'Forecaster offline' },
  { id: 'fc-kill', kind: 'forecaster', title: 'Forecaster crash', forecaster: 'exit', blurb: () => '', running: () => 'Forecaster crashed' },
];
const ALL = [...FEATURED, ...MORE];

// ---------------------------------------------------------------- is this scenario already running?

/** The simulator's open event matching this preset (same type, overlapping targets), if any. */
function openEvent(snap: NetworkSnapshot, p: EventPreset): SimEvent | undefined {
  const keys = ['station_ids', 'route_ids', 'region_ids', 'depot_ids'];
  return snap.events
    .filter((e) => e.status !== 'RESOLVED' && e.type === p.type && keys.every((k) => {
      const want = ids(p.parameters, k);
      return !want.length || want.some((w) => ids(e.parameters, k).includes(w));
    }))
    .sort((a, b) => b.end_tick - a.end_tick)[0];
}
const openFault = (t: ChaosTimeline | null, type: string) => (t?.faults ?? []).find((f) => f.active && f.type === type);
const faultLeft = (f: TimelineFault, now: number) =>
  f.start_wall_time && f.duration_seconds ? Math.max(0, Math.round((Date.parse(f.start_wall_time) + f.duration_seconds * 1000 - now) / 1000)) : null;

// ---------------------------------------------------------------- the response timeline

type LineState = 'done' | 'wait' | 'live' | 'skip' | 'todo';
interface Line { title: string; text: string; state: LineState }
const SPIKE_KINDS = new Set(['demand_spike', 'demand_anomaly', 'persistent_demand_drift']);

/** "Event introduced → detected → plan updated → protected → recovery". Every ✓ is backed by what the backend reports. */
function response(run: ScenarioRun, sc: Scenario, snap: NetworkSnapshot, cur: CurrentView | null, health: HealthReport | null, change: RecChange | null): Line[] {
  const rec = cur?.recommendation ?? null;
  const fresh = !!rec && rec.tick >= run.startTick;
  const legs = fresh && rec ? recLegs(rec) : [];
  const e = run.eventId != null ? snap.events.find((x) => x.id === run.eventId) : sc.event ? openEvent(snap, sc.event) : undefined;
  const started = e?.status === 'ACTIVE' || e?.status === 'RESOLVED';
  const over = e?.status === 'RESOLVED';
  const left = e && !over ? Math.max(0, e.end_tick - snap.tick) : 0;
  const d = (b: boolean): LineState => (b ? 'done' : 'live');
  const waitStep = 'Waiting for the next simulation step…';
  const changed = !!change && change.tick >= run.startTick;
  const planText = legs.length ? `Now recommends: ${summarize(snap, legs)}.` : 'No shipment is needed yet.';
  const intro: Line = { title: 'Event introduced', state: run.error ? 'skip' : 'done', text: run.error ?? '' };
  const review: Line = !fresh ? { title: 'Human review', state: 'todo', text: 'An operator reviews any new shipment.' }
    : !legs.length ? { title: 'Human review', state: 'skip', text: 'No approval needed: nothing to send.' }
    : cur?.record_stage && cur.record_stage !== 'gated' ? { title: 'Human review', state: 'done', text: cur.record_stage === 'rejected' ? 'An operator rejected the plan.' : 'An operator approved the plan and it was sent.' }
    : { title: 'Human review', state: 'wait', text: `Waiting for an operator in the Decision Center (confidence ${Math.round((cur?.gate?.confidence ?? 0) * 100)}%).` };

  if (sc.kind === 'event') {
    const p = sc.event!.parameters;
    if (sc.id === 'spike') {
      const stations = snap.stations.filter((s) => ids(p, 'region_ids').includes(s.region_id));
      const hot = stations.filter((s) => s.demand_multiplier > 1.05);
      const anomaly = fresh && rec!.signals.some((s) => SPIKE_KINDS.has(s.kind));
      const risk = fresh ? rec!.risks.filter((r) => stations.some((s) => s.id === r.station_id) && r.hours_to_stockout != null && r.hours_to_stockout < 99 && r.p_stockout >= 0.25)
        .sort((a, b) => (a.hours_to_stockout ?? 99) - (b.hours_to_stockout ?? 99)) : [];
      return [
        { ...intro, text: intro.text || `Demand in ${region(snap, p)} was raised to ${p.multiplier}× normal for ${stepsLabel(sc.event!.duration_ticks)}.` },
        { title: 'FuelGuard detected it', state: started ? d(anomaly || hot.length > 0) : 'todo',
          text: hot.length ? `Higher demand spotted at ${hot.map((s) => placeName(snap, s.id)).join(', ')} (${hot[0].demand_multiplier.toFixed(1)}× normal).` : anomaly ? 'Demand is running above the forecast.' : waitStep },
        { title: 'Risk updated', state: started ? d(fresh) : 'todo',
          text: !fresh ? waitStep : risk[0] ? `${placeName(snap, risk[0].station_id)} ${fuelName(risk[0].fuel_type).toLowerCase()} may now run short in about ${approxHours(risk[0].hours_to_stockout ?? 0)}.` : `Stations in ${region(snap, p)} still have enough fuel for now.` },
        { title: changed ? 'Recommendation updated' : 'Recommendation re-checked', state: started ? d(fresh) : 'todo',
          text: !fresh ? waitStep : changed ? `${change!.before} → ${change!.after}.` : `${planText} FuelGuard will keep re-checking every step.` },
        review,
      ];
    }
    if (sc.id === 'outage') {
      const sid = ids(p, 'station_ids')[0], name = placeName(snap, sid);
      const st = snap.stations.find((s) => s.id === sid);
      const toIt = legs.some((l) => l.station_id === sid);
      return [
        { ...intro, text: intro.text || `${name} was temporarily closed for ${stepsLabel(sc.event!.duration_ticks)}.` },
        { title: 'FuelGuard detected it', state: started ? d(st?.status === 'OUTAGE' || over) : 'todo', text: st?.status === 'OUTAGE' || over ? 'The station is no longer able to receive fuel.' : waitStep },
        { title: 'Plan updated', state: started && fresh ? (toIt ? 'skip' : 'done') : started ? 'live' : 'todo',
          text: !fresh ? waitStep : toIt ? `A shipment to ${name} is still in the plan; the safety gate will block it.` : `No shipments will go to ${name} while it is closed.` },
        { title: 'Network protected', state: started ? d(fresh) : 'todo', text: fresh ? `Available fuel was preserved for the other stations. ${planText}` : waitStep },
        { title: 'Recovery', state: over && st?.status === 'OPEN' ? 'done' : started ? 'wait' : 'todo',
          text: over && st?.status === 'OPEN' ? `${name} reopened; FuelGuard is considering it again.` : `FuelGuard will reconsider ${name} when the station reopens${left ? ` (in ${stepsLabel(left)})` : ''}.` },
      ];
    }
    if (sc.id === 'route') {
      const rid = ids(p, 'route_ids')[0];
      const route = snap.routes.find((r) => r.id === rid);
      const dest = route?.destination_station_id, name = placeName(snap, dest);
      const backup = snap.routes.find((r) => r.destination_station_id === dest && r.id !== rid);
      const reroute = legs.filter((l) => l.station_id === dest && l.route_id !== rid);
      const over2 = legs.some((l) => l.route_id === rid);
      return [
        { ...intro, text: intro.text || `The ${routeName(snap, rid)} route was blocked for ${stepsLabel(sc.event!.duration_ticks)}.` },
        { title: 'FuelGuard detected it', state: started ? d(route?.status === 'DISRUPTED' || over) : 'todo', text: route?.status === 'DISRUPTED' || over ? 'The route is marked closed.' : waitStep },
        { title: 'Plan updated', state: started && fresh ? (over2 ? 'skip' : 'done') : started ? 'live' : 'todo',
          text: !fresh ? waitStep : over2 ? 'A shipment still uses the closed route; the safety gate will block it.' : 'No shipment will use the closed route.' },
        { title: 'Network protected', state: started ? d(fresh) : 'todo',
          text: !fresh ? waitStep : reroute.length ? `${name} is now supplied via ${routeName(snap, reroute[0].route_id)} instead.`
            : backup ? `${name} can still be supplied from ${placeName(snap, backup.source_depot_id)} if it needs fuel: ${stepsLabel(backup.transit_ticks)}, about ${approxHours((backup.transit_ticks * (snap.tick_minutes || 15)) / 60)}.`
            : `${name} has no other route; FuelGuard is watching its stock.` },
        { title: 'Recovery', state: over && route?.status === 'AVAILABLE' ? 'done' : started ? 'wait' : 'todo',
          text: over && route?.status === 'AVAILABLE' ? 'The route reopened; the shorter route can be used again.' : `The route reopens${left ? ` in ${stepsLabel(left)}` : ' when the event ends'}.` },
      ];
    }
    return [{ ...intro, text: intro.text || `${sc.title} started.` }, { title: 'Plan re-checked', state: started ? d(fresh) : 'todo', text: fresh ? planText : waitStep }, review];
  }

  const comp = (n: string) => health?.components.find((c) => c.name === n);
  const elapsed = (Date.now() - run.at) / 1000;
  const dur = sc.fault?.duration_seconds ?? 60;
  const done = elapsed > dur;
  if (sc.id === 'stream') {
    const lost = comp('Event stream')?.status === 'degraded' || comp('Event stream')?.status === 'down';
    return [
      { ...intro, text: intro.text || `Live updates from the simulator were cut for ${dur} seconds.` },
      { title: 'FuelGuard detected it', state: d(lost || done), text: lost || done ? 'The live update stream dropped.' : 'Waiting for the next health check…' },
      { title: 'Fallback in place', state: d((lost || done) && !snap.freshness?.stale), text: 'FuelGuard switched to regular data checks, so the data stays fresh.' },
      { title: 'Network protected', state: d(!!cur?.recommendation && !snap.freshness?.stale), text: 'Decisions keep running on up-to-date data.' },
      { title: 'Recovery', state: done && comp('Event stream')?.status === 'healthy' ? 'done' : 'wait',
        text: done ? (comp('Event stream')?.status === 'healthy' ? 'Live updates are back.' : 'Reconnecting to live updates…') : `Live updates return in about ${Math.max(0, Math.round(dur - elapsed))} s.` },
    ];
  }
  if (sc.id === 'stale_data' || sc.id === 'unavailable') {
    return [
      { ...intro, text: intro.text || `${sc.title} for ${dur} seconds.` },
      { title: 'FuelGuard detected it', state: d(!!snap.freshness?.stale || snap.freshness?.circuit !== 'CLOSED'), text: 'The simulator data can no longer be trusted as current.' },
      { title: 'Switched to review-only', state: d(cur?.autonomy.mode === 'MANUAL'), text: 'FuelGuard only recommends; an operator must review everything.' },
      { title: 'Network protected', state: d(cur?.gate ? !cur.gate.executable : false), text: 'Nothing is sent on out-of-date data.' },
      { title: 'Recovery', state: done && !snap.freshness?.stale ? 'done' : 'wait', text: 'After the fault, automation returns one level at a time.' },
    ];
  }
  if (sc.kind === 'forecaster') {
    return [{ ...intro, text: intro.text || 'The demand forecaster was taken offline.' },
      { title: 'FuelGuard detected it', state: d(comp('Forecaster')?.status === 'down' || comp('Forecaster')?.status === 'degraded'), text: 'The forecaster reported down.' },
      { title: 'Fallback in place', state: d(!!cur?.recommendation), text: 'A simpler built-in predictor keeps decisions running; confidence drops.' }];
  }
  return [{ ...intro, text: intro.text || `${sc.title} for ${dur} seconds.` }, { title: 'Network protected', state: 'done', text: 'FuelGuard keeps serving the last good data.' },
    { title: 'Recovery', state: done ? 'done' : 'wait', text: 'The fault expires on its own.' }];
}

// ---------------------------------------------------------------- page

export function ScenarioLab() {
  const { source, snap, current, health, refresh, scenario: run, setScenario: setRun, recChange } = useLive();
  const now = useNow();
  const [key, setKey] = useState(operatorKey.get());
  const [unlocked, setUnlocked] = useState(!!operatorKey.get());
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<ChaosTimeline | null>(null);
  const [pacer, setPacer] = useState<PacerStatus | null>(null);
  const [policy, setPolicy] = useState<PolicyStatus | null>(null);
  const [report, setReport] = useState<ExplainResponse | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const live = source === 'live';
  // Synchronous lock: a double click lands before React re-renders the disabled button.
  const inflight = useRef(false);

  const reload = useCallback(() => {
    if (!live) return;
    ops.timeline().then(setTimeline).catch(() => setTimeline(null));
    ops.pacer().then(setPacer).catch(() => undefined);
    ops.policy().then(setPolicy).catch(() => undefined);
  }, [live]);
  useEffect(() => { reload(); const t = setInterval(reload, 3000); return () => clearInterval(t); }, [reload]);

  if (!snap) return <div className="card"><Skeleton lines={5} /></div>;
  const sc = run ? ALL.find((s) => s.id === run.id) : undefined;
  const lines = !run || !sc ? [] : run.error ? [{ title: 'Scenario not started', text: run.error, state: 'skip' as const }] : response(run, sc, snap, current, health, recChange);
  const complete = lines.length > 0 && lines.every((l) => l.state === 'done' || l.state === 'skip' || l.state === 'wait');

  /** Active = the simulator (or fault injector) already has it; pending = we just asked and it hasn't shown up yet. */
  const status = (s: Scenario): { active: boolean; text: string | null } => {
    if (s.event) {
      const e = openEvent(snap, s.event);
      if (e) return { active: true, text: e.status === 'SCHEDULED' ? 'Starting at the next simulation step' : `Active · ${stepsWithTime(Math.max(0, e.end_tick - snap.tick), snap)} remaining` };
    }
    if (s.fault) {
      const f = openFault(timeline, s.fault.type);
      if (f) { const l = faultLeft(f, now); return { active: true, text: `Active${l != null ? ` · about ${l} s remaining` : ''}` }; }
    }
    if (run?.id === s.id && !run.error && Date.now() - run.at < 6000) return { active: true, text: 'Starting…' };
    return { active: false, text: null };
  };

  const start = async (s: Scenario) => {
    if (!unlocked) { setMsg('Enter the operator key first (top right) to start scenarios.'); return; }
    if (inflight.current || status(s).active) return; // never inject the same scenario twice while it is running
    inflight.current = true;
    setBusy(s.id); setMsg(null); setReport(null);
    const startTick = snap.tick + 1;
    const base = { id: s.id, title: s.running(snap), kind: s.kind, at: Date.now(), steps: 0 };
    try {
      let eventId: number | undefined;
      if (s.kind === 'event') eventId = ((await ops.injectEvent(s.event!.type, s.event!.duration_ticks, s.event!.parameters, 1)) as { id?: number })?.id;
      else if (s.kind === 'fault') await ops.injectFault(s.fault!.type, s.fault!.duration_seconds, s.fault!.parameters);
      else await ops.forecaster(s.forecaster!, 60);
      setRun(() => ({ ...base, startTick: s.kind === 'event' ? startTick : snap.tick, eventId }));
      refresh(); reload();
    } catch (e) {
      const text = e instanceof ApiError ? (e.status === 401 ? 'Operator key missing or wrong.' : `${e.code}: ${e.message}`) : 'Request failed';
      setRun(() => ({ ...base, startTick, error: `Couldn’t start the scenario: ${text}` }));
    } finally { setBusy(null); inflight.current = false; }
  };

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setMsg(null);
    try { await fn(); refresh(); reload(); if (done) setMsg(done); } catch (e) { setMsg(e instanceof ApiError ? (e.status === 401 ? 'Operator key missing or wrong.' : `${e.code}: ${e.message}`) : 'Request failed'); }
  };
  const genReport = () => {
    if (!live) return;
    setReportBusy(true);
    ops.incidentReport(run ? Math.max(0, run.startTick - 2) : undefined).then(setReport).catch(() => setReport(null)).finally(() => setReportBusy(false));
  };

  // One row per running scenario, even if an older build injected the same one several times.
  const groups = eventGroups(snap, ['ACTIVE', 'SCHEDULED']);
  const faults = (timeline?.faults ?? []).filter((f) => f.active);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Scenario Lab</h1>
          <p>Create a simulated disruption and see how FuelGuard responds.</p>
        </div>
        {unlocked ? (
          <div className="row"><Chip tone="ok">Operator key entered</Chip><button className="btn ghost sm" onClick={() => setUnlocked(false)}>Change</button></div>
        ) : (
          <form className="row" onSubmit={(e) => { e.preventDefault(); operatorKey.set(key); setUnlocked(!!key); }}>
            <input className="field" style={{ width: 200 }} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Operator key" aria-label="Operator key" />
            <button className="btn dark" disabled={!key}>Unlock</button>
          </form>
        )}
      </div>
      <p className="simnote"><span className="hatch" />Simulation only — no real fuel is moved.</p>
      {!live && <p className="note warn" style={{ marginBottom: 14 }}>The Scenario Lab needs the live backend{source === 'mock' ? ' (showing example data now)' : ' (backend unreachable)'}.</p>}
      {msg && <p className="note" style={{ marginBottom: 14 }}>{msg}</p>}

      {run && sc && (
        <section className="result fade-in">
          <div className="row between" style={{ alignItems: 'flex-start' }}>
            <div className="stack" style={{ gap: 4 }}>
              <span className="kicker" style={{ color: run.error ? 'var(--crit)' : 'var(--lime)' }}>{run.error ? 'Scenario failed to start' : complete ? 'FuelGuard has responded' : 'Scenario running'}</span>
              <h2>{run.title}</h2>
              {!run.error && <span className="xsmall" style={{ color: 'var(--on-dark-2)' }}>{snap.tick >= run.startTick ? 'Started' : 'Starts'} at {simClock({ ...snap, tick: run.startTick })} · now {simClock(snap)}{run.steps ? ` · advanced ${stepsLabel(run.steps)} automatically` : ''}</span>}
            </div>
            <button className="btn ghost-dark sm" onClick={() => { setRun(() => null); setReport(null); }}>Dismiss</button>
          </div>
          <ol className="timeline">
            {lines.map((l, i) => (
              <li key={i} className={l.state}>
                <span className="dotc">{l.state === 'done' ? '✓' : l.state === 'live' ? <span className="spin" /> : l.state === 'skip' ? '–' : l.state === 'wait' ? '●' : i + 1}</span>
                <div><b>{i + 1}. {l.title}</b><p>{l.text}</p></div>
              </li>
            ))}
          </ol>
          <div className="row">
            {!run.error && <button className="btn primary" onClick={() => go('/decisions')}>View updated decision →</button>}
            <button className="btn ghost-dark" onClick={genReport} disabled={!live || reportBusy}>{reportBusy ? 'Writing…' : 'Write incident report'}</button>
          </div>
          {report && (
            <div className="report fade-in"><RichText text={report.text} />
              <p className="xsmall" style={{ color: 'var(--on-dark-2)', marginTop: 8 }}>{report.source === 'llm' ? `Written by the copilot (${report.llm_model}) and checked against the facts` : 'Written from events and decision records'}.</p></div>
          )}
        </section>
      )}

      <div className="scenarios" style={{ marginBottom: 14 }}>
        {FEATURED.map((s) => {
          const st = status(s);
          return (
            <div key={s.id} className={`scenario ${st.active ? 'on' : ''}`}>
              <span className="ic">{ICON[s.icon!]}</span>
              <h3>{s.title}</h3>
              <p className="small muted" style={{ flex: 1 }}>{s.blurb(snap)}</p>
              <p className="xsmall" style={{ color: st.active ? 'var(--ok)' : 'var(--ink-3)', fontWeight: st.active ? 600 : 400 }}>
                {st.text ?? (s.event ? `Lasts ${stepsWithTime(s.event.duration_ticks, snap)}` : `Lasts ${s.fault?.duration_seconds ?? 60} seconds`)}</p>
              <button className={`btn ${st.active ? '' : 'primary'} sm`} disabled={!live || !!busy || st.active} onClick={() => start(s)}>
                {busy === s.id ? 'Starting…' : st.active ? 'Scenario active' : 'Start scenario'}</button>
            </div>
          );
        })}
      </div>

      <div className="mc">
        <section className="card s6">
          <span className="q">Happening now</span>
          {groups.length || faults.length ? (
            <div className="list" style={{ marginTop: 6 }}>
              {groups.map((g) => {
                const e = g[0];
                return (
                  <div key={e.id} className="row between" style={{ flexWrap: 'nowrap' }}>
                    <div className="stack" style={{ gap: 2 }}>
                      <b className="small">{describeActive(snap, e)}</b>
                      {g.length > 1 && <span className="xsmall faint">{g.length} overlapping injections, shown once</span>}
                    </div>
                    <Chip tone={e.status === 'ACTIVE' ? 'warn' : 'idle'}>{e.status === 'ACTIVE' ? `Active · ${stepsLabel(Math.max(0, e.end_tick - snap.tick))} remaining` : 'Starts next step'}</Chip>
                  </div>);
              })}
              {faults.map((f) => { const l = faultLeft(f, now); return (
                <div key={f.id} className="row between"><b className="small">{faultTitle(f.type)}</b><Chip tone="warn">Active{l != null ? ` · ${l} s remaining` : ''}</Chip></div>); })}
            </div>
          ) : <p className="small muted" style={{ marginTop: 8 }}>No scenarios running. Start one above to see FuelGuard respond.</p>}
        </section>
        <section className="card s6">
          <span className="q">How to read the response</span>
          <ol className="small muted" style={{ margin: '8px 0 0', paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <li>The simulator applies the disruption at the next simulation step.</li>
            <li>FuelGuard notices it from live data, not from the button you pressed.</li>
            <li>It re-forecasts risk and re-plans every step, then checks the impact first.</li>
            <li>Any shipment still waits for a human in the Decision Center.</li>
          </ol>
        </section>
      </div>

      <details className="more card" style={{ marginTop: 14 }}>
        <summary>More scenarios and simulation controls</summary>
        <div className="stack" style={{ gap: 16, marginTop: 14 }}>
          <div className="stack" style={{ gap: 8 }}>
            <span className="kicker">Simulation · {simClock(snap)} · {snap.sim_status === 'RUNNING' ? 'running' : pacer?.running ? 'stepping 1 per second' : 'paused'} <span className="faint">(technical: step {snap.tick})</span></span>
            <div className="row">
              <button className="btn sm" disabled={!live || !unlocked} onClick={() => act(() => ops.sim('step'))}>Advance 1 step</button>
              <button className="btn sm" disabled={!live || !unlocked} onClick={() => act(() => ops.setPacer(!pacer?.running, 1000))}>{pacer?.running ? 'Stop auto-advance' : 'Auto-advance 1 step/s'}</button>
              <button className="btn sm" disabled={!live || !unlocked} onClick={() => act(() => ops.sim(snap.sim_status === 'RUNNING' ? 'pause' : 'run'))}>{snap.sim_status === 'RUNNING' ? 'Pause' : 'Run fast'}</button>
              <button className="btn sm ghost" disabled={!live || !unlocked || !faults.length} onClick={() => act(ops.clearFaults, 'All data faults cleared.')}>Clear data faults</button>
              <button className="btn sm danger" disabled={!live || !unlocked} onClick={() => { if (confirm('Reset the simulated world to its starting state?')) act(() => ops.sim('reset'), 'Simulation reset.').then(() => setRun(() => null)); }}>Reset simulation</button>
            </div>
          </div>
          {policy && (
            <div className="stack" style={{ gap: 8 }}>
              <span className="kicker">Planning method</span>
              <div className="row">{['greedy-v1', 'lp-v2'].map((p) => (
                <button key={p} className={`btn sm ${policy.active === p ? 'dark' : ''}`} disabled={!live || !unlocked} onClick={() => act(() => ops.setPolicy(p), `Now using the ${planName(p).toLowerCase()}.`)}>
                  {planName(p)} <span className="faint">· {techName(p)}</span></button>))}
                <button className="btn sm ghost" disabled={!live || !unlocked || policy.active === policy.accepted} onClick={() => act(ops.rollbackPolicy, `Back to the ${planName(policy.accepted).toLowerCase()}.`)}>Undo switch</button></div>
            </div>
          )}
          <div className="stack" style={{ gap: 8 }}>
            <span className="kicker">More scenarios</span>
            <div className="row">{MORE.map((s) => { const st = status(s); return (
              <button key={s.id} className={`btn sm ${st.active ? 'dark' : ''}`} disabled={!live || !!busy || st.active} onClick={() => start(s)} title={st.text ?? undefined}>{s.title}{st.active ? ' · active' : ''}</button>); })}</div>
          </div>
          {(timeline?.events.length || timeline?.faults.length) ? (
            <div className="stack" style={{ gap: 6 }}>
              <span className="kicker">Everything injected so far</span>
              <div className="feed">{[...(timeline?.events ?? []).map((e) => ({ k: `e${e.id}`, t: `step ${e.start_tick}`, s: e.status === 'ACTIVE' ? 'crit' : e.status === 'SCHEDULED' ? 'warn' : 'ok', x: `${describeActive(snap, e as SimEvent)} · ${e.status.toLowerCase()}` })),
                ...(timeline?.faults ?? []).map((f) => ({ k: `f${f.id}`, t: 'fault', s: f.active ? 'crit' : 'ok', x: `${faultTitle(f.type)} · ${f.active ? 'active' : 'expired'}` }))].slice(0, 10)
                .map((r) => <div key={r.k}><span className="tk">{r.t}</span><span className={`d ${r.s}`} /><span>{r.x}</span></div>)}</div>
            </div>
          ) : null}
          <p className="xsmall muted">Technical: events go to the simulator’s <code>/admin/events</code>, data faults to <code>/admin/faults</code>, both through the backend with the operator key. Forecaster scenarios need the forecaster service deployed.</p>
        </div>
      </details>
    </>
  );
}
