import { useCallback, useEffect, useState } from 'react';
import { ApiError, operatorKey } from '../api/client';
import { useLive } from '../api/live';
import { ops, type ChaosTimeline, type PacerStatus, type PolicyStatus } from '../api/ops';
import { Card, Chip, ModeChip, healthTone, toneColor, confTone } from '../components/ui';
import { eventName } from '../lib/format';
import { EVENT_PRESETS, FAULT_PRESETS, FORECASTER_STEPS } from '../lib/playbooks';

type Sel =
  | { kind: 'event'; id: string } | { kind: 'fault'; id: string }
  | { kind: 'forecaster'; id: 'disable' | 'exit' } | { kind: 'sim'; id: 'pause' | 'run' | 'step' | 'reset' };

const SIM_STEPS: Record<string, [string, string][]> = {
  pause: [['Effect', 'Simulator paused; nothing moves until it resumes.']],
  run: [['Effect', 'Simulator runs at its own speed (default 8 ticks/s). Stops the pacer first.']],
  step: [['Effect', 'Exactly one deterministic tick; the whole decision loop runs once.']],
  reset: [['Effect', 'Wipes the simulated world back to the baseline scenario. Our decision history is kept.']],
};

/** Judge-operated panel (blueprint section 11). Every action needs the operator key; the backend proxies /admin/*. */
export function ChaosLab() {
  const { source, current, health, snap, refresh } = useLive();
  const [key, setKey] = useState(operatorKey.get());
  const [unlocked, setUnlocked] = useState(!!operatorKey.get());
  const [sel, setSel] = useState<Sel | null>(null);
  const [duration, setDuration] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: 'ok' | 'crit'; text: string } | null>(null);
  const [timeline, setTimeline] = useState<ChaosTimeline | null>(null);
  const [pacer, setPacer] = useState<PacerStatus | null>(null);
  const [policy, setPolicy] = useState<PolicyStatus | null>(null);
  const live = source === 'live';

  const reload = useCallback(() => {
    if (!live) return;
    ops.timeline().then(setTimeline).catch(() => setTimeline(null));
    ops.pacer().then(setPacer).catch(() => undefined);
    ops.policy().then(setPolicy).catch(() => undefined);
  }, [live]);
  useEffect(() => { reload(); const t = setInterval(reload, 3000); return () => clearInterval(t); }, [reload]);

  const ev = sel?.kind === 'event' ? EVENT_PRESETS.find((p) => p.id === sel.id) : undefined;
  const ft = sel?.kind === 'fault' ? FAULT_PRESETS.find((p) => p.id === sel.id) : undefined;
  const choose = (s: Sel) => {
    setSel(s); setMsg(null);
    setDuration(s.kind === 'event' ? EVENT_PRESETS.find((p) => p.id === s.id)!.duration_ticks
      : s.kind === 'fault' ? FAULT_PRESETS.find((p) => p.id === s.id)!.duration_seconds : 60);
  };

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg({ tone: 'ok', text: done }); refresh(); reload(); }
    catch (e) {
      setMsg({ tone: 'crit', text: e instanceof ApiError ? (e.status === 401 ? 'Operator key missing or wrong.' : `${e.code}: ${e.message}`) : 'Request failed' });
    } finally { setBusy(false); }
  };

  const run = () => {
    if (!sel) return;
    if (sel.kind === 'event' && ev) return act(() => ops.injectEvent(ev.type, duration, ev.parameters, 1), `${ev.name} injected from tick ${(snap?.tick ?? 0) + 1} for ${duration} ticks.`);
    if (sel.kind === 'fault' && ft) return act(() => ops.injectFault(ft.type, duration, ft.parameters), `${ft.name} active for ${duration} s.`);
    if (sel.kind === 'forecaster') return act(() => ops.forecaster(sel.id, duration), sel.id === 'disable' ? `Forecaster disabled for ${duration} s.` : 'Forecaster process told to exit.');
    if (sel.kind === 'sim') {
      if (sel.id === 'reset' && !confirm('Reset the simulated world to the baseline scenario?')) return;
      return act(() => ops.sim(sel.id), `Simulator: ${sel.id}.`);
    }
  };

  const steps = ev?.steps ?? ft?.steps ?? (sel?.kind === 'forecaster' ? FORECASTER_STEPS : sel?.kind === 'sim' ? SIM_STEPS[sel.id] : null);
  const payload = ev ? { type: ev.type, start_in_ticks: 1, duration_ticks: duration, parameters: ev.parameters }
    : ft ? { type: ft.type, duration_seconds: duration, parameters: ft.parameters }
    : sel?.kind === 'forecaster' ? `POST /api/chaos/forecaster/${sel.id}?seconds=${duration}` : sel ? `POST /api/chaos/sim/${sel.id}` : null;
  const endpoint = ev ? 'POST /api/chaos/events → /admin/events' : ft ? 'POST /api/chaos/faults → /admin/faults' : '';
  const disabled = !live || !unlocked || busy;
  const btn = (s: Sel, label: string) => (
    <button key={s.kind + s.id} type="button" aria-pressed={sel?.kind === s.kind && sel.id === s.id} disabled={!live || !unlocked} onClick={() => choose(s)}>{label}</button>
  );
  const a = current?.autonomy;
  const activeFaults = (timeline?.faults ?? []).filter((f) => f.active);

  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="row between">
        <div className="stack" style={{ gap: 4 }}><span className="kicker">Chaos Lab · judge-operated</span><h2>Break it and watch it recover</h2></div>
        <p className="small muted" style={{ maxWidth: '62ch' }}>Inject any simulator crisis or fault, or take our forecaster down, then watch FuelGuard detect, explain, adapt, fall back and recover. Locked behind the operator key.</p>
      </div>
      {!live && <p className="note warn">The Chaos Lab needs the live backend{source === 'mock' ? ' (showing mock data now)' : ' (backend unreachable)'}.</p>}

      <div className="mc">
        <section className="console s6" aria-label="Chaos controls">
          <div className="row between"><span className="kicker">FuelGuard · Chaos Lab</span>{unlocked ? <Chip tone="ok">operator mode</Chip> : <Chip tone="idle">locked</Chip>}</div>
          <form className="lock" onSubmit={(e) => { e.preventDefault(); operatorKey.set(key); setUnlocked(!!key); }}>
            {unlocked ? (
              <><span className="xsmall">Actions send X-Operator-Key; the backend checks it before proxying.</span>
                <button type="button" onClick={() => { setUnlocked(false); setSel(null); }}>Lock</button></>
            ) : (
              <><label htmlFor="chaos-key" className="xsmall">Operator key</label>
                <input id="chaos-key" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="X-Operator-Key" />
                <button type="submit" disabled={!key}>Unlock</button></>
            )}
          </form>
          <div className="stack" style={{ gap: 7 }}><span className="kicker">Domain events → /admin/events</span>
            <div className="grp">{EVENT_PRESETS.map((p) => btn({ kind: 'event', id: p.id }, p.name))}</div></div>
          <div className="stack" style={{ gap: 7 }}><span className="kicker">Engineering faults → /admin/faults</span>
            <div className="grp">{FAULT_PRESETS.map((p) => btn({ kind: 'fault', id: p.id }, p.name))}
              <button type="button" disabled={disabled || !activeFaults.length} onClick={() => act(ops.clearFaults, 'All faults cleared.')}>Clear faults</button></div></div>
          <div className="stack" style={{ gap: 7 }}><span className="kicker">Our components</span>
            <div className="grp">{btn({ kind: 'forecaster', id: 'disable' }, 'Disable forecaster')}{btn({ kind: 'forecaster', id: 'exit' }, 'Kill forecaster')}</div></div>
          <div className="stack" style={{ gap: 7 }}><span className="kicker">Simulation</span>
            <div className="grp">
              {btn({ kind: 'sim', id: 'pause' }, 'Pause')}{btn({ kind: 'sim', id: 'step' }, 'Step 1 tick')}{btn({ kind: 'sim', id: 'run' }, 'Run')}
              <button type="button" disabled={disabled} aria-pressed={!!pacer?.running}
                onClick={() => act(() => ops.setPacer(!pacer?.running, 1000), pacer?.running ? 'Pacer off.' : 'Pacer on: 1 tick per second.')}>
                {pacer?.running ? `Pacer on · ${pacer.ticks_done} ticks` : 'Pacer: 1 tick/s'}</button>
              {btn({ kind: 'sim', id: 'reset' }, 'Reset world')}
            </div></div>
        </section>

        <Card className="s6" q="What FuelGuard does" title={ev?.name ?? ft?.name ?? (sel?.kind === 'forecaster' ? (sel.id === 'disable' ? 'Disable forecaster' : 'Kill forecaster') : sel ? `Simulator ${sel.id}` : unlocked ? 'Pick a crisis or fault' : 'Unlock operator mode first')}>
          {!sel ? <p className="small muted">Each action shows the call we make and the reaction to expect, then this panel tracks the live response below.</p> : (
            <div className="stack" style={{ gap: 12 }}>
              {(sel.kind !== 'sim') && (
                <label className="row small" style={{ gap: 8 }}>{sel.kind === 'event' ? 'Duration (ticks)' : 'Duration (seconds)'}
                  <input className="field num-in" type="number" min={1} max={sel.kind === 'event' ? 2000 : 3600} value={duration} onChange={(e) => setDuration(Math.max(1, Number(e.target.value) || 1))} /></label>
              )}
              <div><p className="xsmall faint mono" style={{ marginBottom: 4 }}>{endpoint}</p>
                <pre className="code">{typeof payload === 'string' ? payload : JSON.stringify(payload, null, 1)}</pre></div>
              <div className="reaction">{(steps ?? []).map(([k, v]) => <div key={k} className="st"><b>{k}</b><span>{v}</span></div>)}</div>
              <div className="row"><button className="btn primary" disabled={disabled} onClick={run}>{busy ? 'Sending…' : sel.kind === 'sim' ? `Run: ${sel.id}` : 'Inject'}</button>
                {msg && <span className={`small`} style={{ color: toneColor(msg.tone) }}>{msg.text}</span>}</div>
            </div>
          )}
        </Card>

        <Card className="s4" q="Live reaction" title="How the system is responding">
          <div className="stack" style={{ gap: 10 }}>
            <div className="row between small"><span className="muted">Mode</span><ModeChip mode={a?.mode} /></div>
            <div className="row between small"><span className="muted">Confidence</span><b className="num" style={{ color: a ? toneColor(confTone(a.confidence)) : undefined }}>{a ? a.confidence.toFixed(2) : '—'}</b></div>
            <div className="row between small"><span className="muted">Data</span><Chip tone={snap?.freshness?.stale ? 'crit' : 'ok'}>{snap?.freshness?.stale ? 'stale' : 'fresh'}</Chip></div>
            <div className="row between small"><span className="muted">Simulator circuit</span><Chip tone={snap?.freshness?.circuit === 'CLOSED' ? 'ok' : snap?.freshness?.circuit === 'HALF_OPEN' ? 'warn' : 'crit'}>{snap?.freshness?.circuit?.toLowerCase() ?? '—'}</Chip></div>
            <div className="row between small"><span className="muted">System health</span>{health ? <Chip tone={healthTone(health.status)}>{health.status}</Chip> : '—'}</div>
            {(health?.components ?? []).filter((c) => c.status !== 'healthy' && c.status !== 'unknown').map((c) => (
              <div key={c.name} className="xsmall" style={{ color: toneColor(healthTone(c.status)) }}>{c.name}: {c.detail ?? c.status}</div>
            ))}
            {a && a.log.length > 0 && <div className="log">{a.log.slice(0, 6).map((l, i) => <div key={i}><b>t{l.tick ?? '—'}</b>{l.message}</div>)}</div>}
          </div>
        </Card>

        <Card className="s5" q="Timeline" title="Injected events" right={<span className="xsmall faint">tick {snap?.tick ?? '—'}</span>}>
          {timeline?.events.length ? (
            <div className="tbl"><table>
              <thead><tr><th>Event</th><th className="n">Ticks</th><th>Status</th></tr></thead>
              <tbody>{timeline.events.slice(0, 12).map((e) => (
                <tr key={e.id}><td>{eventName(e.type)}</td><td className="n mono">t{e.start_tick}–t{e.end_tick}</td>
                  <td><Chip tone={e.status === 'ACTIVE' ? 'crit' : e.status === 'SCHEDULED' ? 'warn' : 'idle'}>{e.status.toLowerCase()}</Chip></td></tr>))}</tbody>
            </table></div>
          ) : <p className="empty">{live ? 'No events injected yet.' : '—'}</p>}
        </Card>

        <Card className="s3" q="Faults" title="Simulator faults">
          {timeline?.faults.length ? (
            <div className="list">{timeline.faults.slice(0, 8).map((f) => (
              <div key={f.id} className="row between small"><span>{f.type.replace(/_/g, ' ')}</span><Chip tone={f.active ? 'crit' : 'idle'}>{f.active ? 'active' : 'expired'}</Chip></div>))}</div>
          ) : <p className="empty">{live ? 'No faults injected.' : '—'}</p>}
          {policy && (
            <div className="stack" style={{ gap: 6, marginTop: 14 }}>
              <span className="kicker">Allocation policy</span>
              <div className="row">
                {['greedy-v1', 'lp-v2'].map((p) => (
                  <button key={p} className={`btn sm ${policy.active === p ? 'primary' : ''}`} disabled={disabled}
                    onClick={() => act(() => ops.setPolicy(p), `Policy switched to ${p}.`)}>{p}</button>
                ))}
                <button className="btn sm ghost" disabled={disabled || policy.active === policy.accepted} onClick={() => act(ops.rollbackPolicy, `Rolled back to ${policy.accepted}.`)}>Roll back</button>
              </div>
              <span className="xsmall faint">Active {policy.active} · last accepted {policy.accepted}</span>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
