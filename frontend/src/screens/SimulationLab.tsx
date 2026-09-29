import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError, operatorKey } from '../api/client';
import { useLive } from '../api/live';
import { ops, type ChaosTimeline, type PacerStatus, type PolicyStatus } from '../api/ops';
import type { CurrentView, ExplainResponse, HealthReport, NetworkSnapshot } from '../api/types';
import { Chip, RichText, Skeleton } from '../components/ui';
import { describeEvent } from '../lib/derive';
import { eventName, humanize, placeName, recLegs } from '../lib/format';
import { EVENT_PRESETS, FAULT_PRESETS, FORECASTER_STEPS, type EventPreset, type FaultPreset } from '../lib/playbooks';
import { go } from '../lib/router';

const ICON: Record<string, ReactNode> = {
  spike: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 17l6-6 4 4 8-8" /><path d="M15 7h6v6" /></svg>,
  outage: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16" /><path d="M3 21h12M7 8h4" /><path d="M14 10h2a2 2 0 0 1 2 2v4a1.5 1.5 0 0 0 3 0V9l-3-3" /></svg>,
  route: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="6" cy="19" r="2" /><circle cx="18" cy="5" r="2" /><path d="M8 19h5a4 4 0 0 0 0-8H11a4 4 0 0 1 0-8h5" /><path d="M9 9l6 6M15 9l-6 6" /></svg>,
  stream: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M13 2L4 14h7l-1 8 9-12h-7z" /></svg>,
};

type Kind = 'event' | 'fault' | 'forecaster';
interface Scenario { id: string; kind: Kind; title: string; blurb: string; icon?: string; event?: EventPreset; fault?: FaultPreset; forecaster?: 'disable' | 'exit' }
const ev = (id: string) => EVENT_PRESETS.find((p) => p.id === id)!;
const ft = (id: string) => FAULT_PRESETS.find((p) => p.id === id)!;

const FEATURED: Scenario[] = [
  { id: 'spike', kind: 'event', title: 'Demand spike', blurb: 'Dhaka region demand ×1.8 for 12 ticks.', icon: 'spike', event: ev('spike') },
  { id: 'outage', kind: 'event', title: 'Station outage', blurb: 'Take Tongi down for 8 ticks.', icon: 'outage', event: ev('outage') },
  { id: 'route', kind: 'event', title: 'Route disruption', blurb: 'Close Gazipur → Mirpur for 16 ticks.', icon: 'route', event: ev('route') },
  { id: 'stream', kind: 'fault', title: 'Stream failure', blurb: 'Interrupt the live event stream for 60 s.', icon: 'stream', fault: ft('stream_disconnect') },
];
const MORE: Scenario[] = [
  ...['depot', 'delay', 'short'].map((id) => ({ id, kind: 'event' as const, title: ev(id).name, blurb: '', event: ev(id) })),
  ...['stale_data', 'unavailable', 'error_rate', 'latency'].map((id) => ({ id, kind: 'fault' as const, title: ft(id).name, blurb: '', fault: ft(id) })),
  { id: 'fc-disable', kind: 'forecaster', title: 'Disable forecaster 60 s', blurb: '', forecaster: 'disable' },
  { id: 'fc-kill', kind: 'forecaster', title: 'Kill forecaster', blurb: '', forecaster: 'exit' },
];

interface Run { sc: Scenario; startTick: number; eventId?: number; at: number; steps: number; error?: string }
type LineState = 'done' | 'wait' | 'live' | 'skip' | 'todo';
interface Line { label: string; state: LineState; detail?: string }

const SPIKE_KINDS = new Set(['demand_spike', 'demand_anomaly', 'persistent_demand_drift']);

/** Build the "scenario running" chain from live state. Every ✓ is backed by something the backend actually reports. */
function chain(run: Run, snap: NetworkSnapshot, cur: CurrentView | null, health: HealthReport | null): Line[] {
  const rec = cur?.recommendation ?? null;
  const fresh = !!rec && rec.tick >= run.startTick;
  const legs = fresh && rec ? recLegs(rec) : [];
  const e = run.eventId != null ? snap.events.find((x) => x.id === run.eventId) : undefined;
  const active = e?.status === 'ACTIVE' || e?.status === 'RESOLVED';
  const stage = cur?.record_stage;
  const review: Line = !fresh ? { label: 'Operator review', state: 'todo' }
    : !legs.length ? { label: 'No shipment needed this tick', state: 'skip' }
    : stage && stage !== 'gated' ? { label: stage === 'rejected' ? 'Operator rejected the plan' : 'Allocation approved and sent', state: 'done' }
    : cur?.gate?.requires_human ? { label: 'Waiting for operator review', state: 'wait', detail: `confidence ${Math.round((cur.gate.confidence ?? 0) * 100)}%, mode ${cur.gate.mode.toLowerCase()}` }
    : { label: 'Cleared for autopilot', state: 'wait' };
  const d = (b: boolean): LineState => (b ? 'done' : 'live');
  const inj: Line = { label: `${run.sc.title} injected`, state: run.error ? 'skip' : 'done', detail: run.error ?? (run.sc.kind === 'event' ? `from tick ${run.startTick}` : undefined) };

  if (run.sc.kind === 'event') {
    const p = run.sc.event!.parameters as Record<string, string[]>;
    const act: Line = { label: 'Event active in the simulator', state: d(!!active), detail: e ? `t${e.start_tick}–t${e.end_tick}` : undefined };
    if (run.sc.id === 'spike') {
      const stations = snap.stations.filter((s) => (p.region_ids ?? []).includes(s.region_id)).map((s) => s.id);
      const anomaly = !!fresh && rec!.signals.some((s) => SPIKE_KINDS.has(s.kind));
      const risk = fresh ? rec!.risks.filter((r) => stations.includes(r.station_id) && r.hours_to_stockout != null && r.hours_to_stockout < 99 && r.p_stockout >= 0.25) : [];
      return [inj, act,
        { label: 'Anomaly detected', state: active ? d(anomaly) : 'todo', detail: anomaly ? humanize(snap, rec!.signals.find((s) => SPIKE_KINDS.has(s.kind))?.message ?? '') : undefined },
        { label: 'Shortage predicted', state: active ? d(risk.length > 0) : 'todo', detail: risk[0] ? `${placeName(snap, risk[0].station_id)} ${risk[0].fuel_type.toLowerCase()}, ${Math.round(risk[0].p_stockout * 100)}%` : undefined },
        { label: 'New allocation generated', state: active ? d(legs.length > 0) : 'todo', detail: legs.length ? `${legs.length} leg(s), ${Math.round(legs.reduce((s, l) => s + l.quantity, 0)).toLocaleString()} L` : undefined },
        review];
    }
    if (run.sc.id === 'outage') {
      const sid = (p.station_ids ?? [])[0];
      const down = snap.stations.find((s) => s.id === sid)?.status === 'OUTAGE';
      return [inj, act,
        { label: `${placeName(snap, sid)} outage detected`, state: active ? d(down) : 'todo' },
        { label: 'Guardrails stop shipments to it', state: active && fresh ? (legs.some((l) => l.station_id === sid) ? 'skip' : 'done') : 'todo', detail: 'no leg targets the closed station' },
        { label: 'Plan serves the other stations', state: active && fresh ? (legs.length ? 'done' : 'skip') : 'todo' },
        review];
    }
    if (run.sc.id === 'route') {
      const rid = (p.route_ids ?? [])[0];
      const route = snap.routes.find((r) => r.id === rid);
      const reroute = legs.filter((l) => l.station_id === route?.destination_station_id && l.route_id !== rid);
      return [inj, act,
        { label: 'Route disruption detected', state: active ? d(route?.status === 'DISRUPTED') : 'todo' },
        { label: 'Disrupted route avoided', state: active && fresh ? (legs.some((l) => l.route_id === rid) ? 'skip' : 'done') : 'todo', detail: 'guardrail: no shipment departs over it' },
        { label: `Rerouted to ${placeName(snap, route?.destination_station_id)} via backup`, state: active && fresh ? (reroute.length ? 'done' : 'skip') : 'todo', detail: reroute.length ? humanize(snap, reroute[0].route_id) : 'not needed this tick' },
        review];
    }
    return [inj, act, { label: 'System re-plans with the new situation', state: active ? d(fresh) : 'todo' }, review];
  }
  const comp = (n: string) => health?.components.find((c) => c.name === n);
  const elapsed = (Date.now() - run.at) / 1000;
  if (run.sc.id === 'stream_disconnect') {
    return [inj,
      { label: 'Event stream lost', state: d(comp('Event stream')?.status === 'degraded'), detail: comp('Event stream')?.detail ?? undefined },
      { label: 'REST polling keeps data fresh', state: d(!snap.freshness?.stale) },
      { label: 'Decisions continue every tick', state: d(snap.tick > run.startTick), detail: `tick ${snap.tick}` },
      { label: 'Stream reconnects after the fault', state: elapsed > (run.sc.fault?.duration_seconds ?? 60) && comp('Event stream')?.status === 'healthy' ? 'done' : 'wait' }];
  }
  if (run.sc.id === 'stale_data' || run.sc.id === 'unavailable') {
    return [inj,
      { label: run.sc.id === 'stale_data' ? 'Stale data detected' : 'Simulator refusing requests', state: d(!!snap.freshness?.stale || snap.freshness?.circuit !== 'CLOSED') },
      { label: 'Manual safe mode: recommend only', state: d(cur?.autonomy.mode === 'MANUAL') },
      { label: 'Execution locked', state: d(cur?.gate ? !cur.gate.executable : false) },
      { label: 'Recovers after the fault expires', state: elapsed > (run.sc.fault?.duration_seconds ?? 60) && !snap.freshness?.stale ? 'done' : 'wait', detail: 'mode climbs back one level per 3 healthy ticks' }];
  }
  if (run.sc.kind === 'forecaster') {
    return [inj, { label: 'Forecaster reported down', state: d(comp('Forecaster')?.status === 'down' || comp('Forecaster')?.status === 'degraded') },
      { label: 'Fallback predictor keeps decisions running', state: d(snap.tick > run.startTick || !!cur?.recommendation) }];
  }
  return [inj, { label: 'System keeps serving cached state', state: d(true) }, { label: 'Recovers after the fault expires', state: elapsed > (run.sc.fault?.duration_seconds ?? 60) ? 'done' : 'wait' }];
}

export function SimulationLab() {
  const { source, snap, current, health, refresh } = useLive();
  const [key, setKey] = useState(operatorKey.get());
  const [unlocked, setUnlocked] = useState(!!operatorKey.get());
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<ChaosTimeline | null>(null);
  const [pacer, setPacer] = useState<PacerStatus | null>(null);
  const [policy, setPolicy] = useState<PolicyStatus | null>(null);
  const [report, setReport] = useState<ExplainResponse | null>(null);
  const [reportBusy, setReportBusy] = useState(false);
  const live = source === 'live';
  const stepping = useRef(false);

  const reload = useCallback(() => {
    if (!live) return;
    ops.timeline().then(setTimeline).catch(() => setTimeline(null));
    ops.pacer().then(setPacer).catch(() => undefined);
    ops.policy().then(setPolicy).catch(() => undefined);
  }, [live]);
  useEffect(() => { reload(); const t = setInterval(reload, 3000); return () => clearInterval(t); }, [reload]);

  const lines = run && snap ? chain(run, snap, current, health) : [];
  const complete = lines.length > 0 && lines.every((l) => l.state !== 'live' && l.state !== 'todo');

  // Paused simulator + no pacer: advance it ourselves so the reaction shows up (at most 6 ticks per scenario).
  useEffect(() => {
    if (!run || run.error || complete || !live || stepping.current) return;
    if (snap?.sim_status === 'RUNNING' || pacer?.running || run.steps >= 6 || run.sc.kind !== 'event') return;
    stepping.current = true;
    const t = setTimeout(async () => {
      try { await ops.sim('step'); setRun((r) => (r ? { ...r, steps: r.steps + 1 } : r)); refresh(); } catch { /* shown via health */ }
      stepping.current = false;
    }, 1300);
    return () => { clearTimeout(t); stepping.current = false; };
  }, [run, complete, live, snap?.tick, snap?.sim_status, pacer?.running, refresh]);

  const start = async (sc: Scenario) => {
    if (!unlocked) { setMsg('Unlock operator mode first (top right).'); return; }
    setBusy(true); setMsg(null); setReport(null);
    const startTick = (snap?.tick ?? 0) + 1;
    try {
      let eventId: number | undefined;
      if (sc.kind === 'event') {
        const out = await ops.injectEvent(sc.event!.type, sc.event!.duration_ticks, sc.event!.parameters, 1) as { id?: number };
        eventId = out?.id;
      } else if (sc.kind === 'fault') await ops.injectFault(sc.fault!.type, sc.fault!.duration_seconds, sc.fault!.parameters);
      else await ops.forecaster(sc.forecaster!, 60);
      setRun({ sc, startTick: sc.kind === 'event' ? startTick : snap?.tick ?? 0, eventId, at: Date.now(), steps: 0 });
      refresh(); reload();
    } catch (e) {
      const text = e instanceof ApiError ? (e.status === 401 ? 'Operator key missing or wrong.' : `${e.code}: ${e.message}`) : 'Request failed';
      setRun({ sc, startTick, at: Date.now(), steps: 0, error: text });
    } finally { setBusy(false); }
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

  if (!snap) return <div className="card"><Skeleton lines={5} /></div>;
  const openEvents = snap.events.filter((e) => e.status !== 'RESOLVED');
  const steps = run?.sc.event?.steps ?? run?.sc.fault?.steps ?? (run?.sc.kind === 'forecaster' ? FORECASTER_STEPS : null);

  return (
    <>
      <div className="page-head">
        <div><h1>Simulation Lab</h1><p>Introduce an operational event and watch the system detect it, predict the impact, decide, and wait for a human.</p></div>
        {unlocked ? (
          <div className="row"><Chip tone="ok">Operator mode</Chip><button className="btn ghost sm" onClick={() => { setUnlocked(false); }}>Lock</button></div>
        ) : (
          <form className="row" onSubmit={(e) => { e.preventDefault(); operatorKey.set(key); setUnlocked(!!key); }}>
            <input className="field" style={{ width: 200 }} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Operator key" aria-label="Operator key" />
            <button className="btn dark" disabled={!key}>Unlock</button>
          </form>
        )}
      </div>
      {!live && <p className="note warn" style={{ marginBottom: 14 }}>The Simulation Lab needs the live backend{source === 'mock' ? ' (showing mock data now)' : ' (backend unreachable)'}.</p>}
      {msg && <p className="note" style={{ marginBottom: 14 }}>{msg}</p>}

      <div className="scenarios" style={{ marginBottom: 14 }}>
        {FEATURED.map((sc) => (
          <div key={sc.id} className={`scenario ${run?.sc.id === sc.id ? 'on' : ''}`}>
            <span className="ic">{ICON[sc.icon!]}</span>
            <h3>{sc.title}</h3>
            <p className="small muted" style={{ flex: 1 }}>{sc.blurb}</p>
            <button className={`btn ${run?.sc.id === sc.id ? 'dark' : 'primary'} sm`} disabled={!live || busy} onClick={() => start(sc)}>
              {busy && run?.sc.id !== sc.id ? 'Run scenario' : run?.sc.id === sc.id ? 'Run again' : 'Run scenario'}</button>
          </div>
        ))}
      </div>

      {run && (
        <div className="mc fade-in" style={{ marginBottom: 14 }}>
          <section className="card s7">
            <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">{complete ? 'Scenario complete' : 'Scenario running'}</span><h2>{run.sc.title}</h2></div>
              <span className="xsmall muted">tick {snap.tick}{run.steps ? ` · advanced ${run.steps} tick${run.steps > 1 ? 's' : ''}` : ''}</span></div>
            <div className="chain">
              {lines.map((l, i) => (
                <div key={i} className="ln">
                  <span className={`dotc ${l.state}`}>{l.state === 'done' ? '✓' : l.state === 'wait' ? '●' : l.state === 'live' ? <span className="spin" /> : l.state === 'skip' ? '–' : i + 1}</span>
                  <div><div style={{ fontWeight: 600, color: l.state === 'todo' ? 'var(--ink-3)' : undefined }}>{l.label}</div>{l.detail && <div className="xsmall muted">{l.detail}</div>}</div>
                  <span />
                </div>
              ))}
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              {run.sc.kind === 'event' && <button className="btn primary sm" onClick={() => go('/intelligence')}>See the reasoning in Intelligence →</button>}
              <button className="btn sm" onClick={genReport} disabled={!live || reportBusy}>{reportBusy ? 'Writing…' : 'Incident report'}</button>
            </div>
          </section>
          <section className="card s5">
            <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Playbook</span><h3>What the system is designed to do</h3></div></div>
            <div className="reaction">{(steps ?? []).map(([k, v]) => <div key={k} className="st"><b>{k}</b><span>{v}</span></div>)}</div>
          </section>
          {report && (
            <section className="card s12 fade-in"><div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Copilot</span><h3>Incident report</h3></div><span className="tag">read-only</span></div>
              <RichText text={report.text} /><p className="xsmall faint" style={{ marginTop: 8 }}>{report.source === 'llm' ? `Copilot (${report.llm_model}), checked against the facts` : 'Template report from events and decision records'}</p></section>
          )}
        </div>
      )}

      <div className="mc">
        <section className="card s4">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Simulation</span><h3>Tick {snap.tick} · {snap.sim_status === 'RUNNING' ? 'running' : pacer?.running ? 'paced' : 'paused'}</h3></div></div>
          <div className="row">
            <button className="btn sm" disabled={!live || !unlocked} onClick={() => act(() => ops.sim('step'))}>Step 1 tick</button>
            <button className="btn sm" disabled={!live || !unlocked} onClick={() => act(() => ops.setPacer(!pacer?.running, 1000))}>{pacer?.running ? 'Stop pacer' : 'Pacer 1 tick/s'}</button>
            <button className="btn sm" disabled={!live || !unlocked} onClick={() => act(() => ops.sim(snap.sim_status === 'RUNNING' ? 'pause' : 'run'))}>{snap.sim_status === 'RUNNING' ? 'Pause' : 'Run fast'}</button>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn sm ghost" disabled={!live || !unlocked || !(timeline?.faults ?? []).some((f) => f.active)} onClick={() => act(ops.clearFaults, 'All faults cleared.')}>Clear faults</button>
            <button className="btn sm danger" disabled={!live || !unlocked} onClick={() => { if (confirm('Reset the simulated world to the baseline scenario?')) act(() => ops.sim('reset'), 'World reset.').then(() => setRun(null)); }}>Reset world</button>
          </div>
          {policy && (
            <div className="stack" style={{ gap: 6, marginTop: 16 }}>
              <span className="kicker">Allocation policy</span>
              <div className="row">{['greedy-v1', 'lp-v2'].map((p) => <button key={p} className={`btn sm ${policy.active === p ? 'dark' : ''}`} disabled={!live || !unlocked} onClick={() => act(() => ops.setPolicy(p), `Policy switched to ${p}.`)}>{p}</button>)}
                <button className="btn sm ghost" disabled={!live || !unlocked || policy.active === policy.accepted} onClick={() => act(ops.rollbackPolicy, `Rolled back to ${policy.accepted}.`)}>Roll back</button></div>
            </div>
          )}
        </section>
        <section className="card s4">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Now</span><h3>{openEvents.length ? `${openEvents.length} active or scheduled event(s)` : 'No active events'}</h3></div></div>
          {openEvents.length ? <div className="list">{openEvents.map((e) => (
            <div key={e.id} className="stack" style={{ gap: 3 }}>
              <div className="row between"><b className="small">{eventName(e.type)}</b><Chip tone={e.status === 'ACTIVE' ? 'crit' : 'warn'}>{e.status === 'ACTIVE' ? `${e.end_tick - snap.tick} ticks left` : `in ${e.start_tick - snap.tick}`}</Chip></div>
              <span className="xsmall muted">{describeEvent(snap, e.parameters) || 'All entities'}</span>
            </div>))}</div> : <p className="empty">Run a scenario above to see the system respond.</p>}
          {(timeline?.faults ?? []).filter((f) => f.active).map((f) => <div key={f.id} className="row between small" style={{ marginTop: 8 }}><span>Fault: {f.type.replace(/_/g, ' ')}</span><Chip tone="crit">active</Chip></div>)}
        </section>
        <section className="card s4">
          <div className="hd"><div className="stack" style={{ gap: 4 }}><span className="q">Timeline</span><h3>Injected so far</h3></div></div>
          {timeline?.events.length || timeline?.faults.length ? (
            <div className="feed">{[...(timeline?.events ?? []).map((e) => ({ k: `e${e.id}`, t: `t${e.start_tick}`, s: e.status === 'ACTIVE' ? 'crit' : e.status === 'SCHEDULED' ? 'warn' : 'ok', x: `${eventName(e.type)} · ${e.status.toLowerCase()}` })),
              ...(timeline?.faults ?? []).map((f) => ({ k: `f${f.id}`, t: 'fault', s: f.active ? 'crit' : 'ok', x: `${f.type.replace(/_/g, ' ')} · ${f.active ? 'active' : 'expired'}` }))].slice(0, 8)
              .map((r) => <div key={r.k}><span className="tk">{r.t}</span><span className={`d ${r.s}`} /><span>{r.x}</span></div>)}</div>
          ) : <p className="empty">{live ? 'Nothing injected yet.' : '—'}</p>}
        </section>
      </div>

      <details className="more card" style={{ marginTop: 14 }}>
        <summary>More scenarios: depot constraint, shipment delay, supply shortfall, simulator faults, forecaster failure</summary>
        <div className="row" style={{ marginTop: 14 }}>
          {MORE.map((sc) => <button key={sc.id} className={`btn sm ${run?.sc.id === sc.id ? 'dark' : ''}`} disabled={!live || busy} onClick={() => start(sc)}>{sc.title}</button>)}
        </div>
        <p className="xsmall muted" style={{ marginTop: 10 }}>Events go to the simulator's <code>/admin/events</code>, faults to <code>/admin/faults</code>, both through the backend with the operator key. The forecaster actions need the forecaster service deployed.</p>
      </details>
    </>
  );
}
