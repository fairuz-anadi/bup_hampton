import { useEffect, useRef, useState } from 'react';
import { ApiError, operatorKey } from '../api/client';
import { useLive } from '../api/live';
import { ops, type PacerStatus } from '../api/ops';

type Runner = { kind: 'pacer'; interval_ms: number } | { kind: 'sim' };
const STORE = 'fuelguard.resumeWith';
const readRunner = (): Runner | null => { try { return JSON.parse(sessionStorage.getItem(STORE) ?? 'null'); } catch { return null; } };

/**
 * Pause / resume the simulation from any page. The world advances either by the simulator's own runner
 * (sim_status RUNNING) or by our backend pacer stepping a paused simulator; pause stops both and remembers
 * which one to restart.
 */
export function SimControl() {
  const { snap, source, refresh } = useLive();
  const [pacer, setPacer] = useState<PacerStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [askKey, setAskKey] = useState(false);
  const [key, setKey] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const live = source === 'live';

  const readPacer = () => ops.pacer().then(setPacer).catch(() => setPacer(null));
  useEffect(() => {
    if (!live) return;
    readPacer(); const t = setInterval(readPacer, 3000);
    return () => clearInterval(t);
  }, [live]);
  useEffect(() => {
    if (!askKey && !err) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) { setAskKey(false); setErr(null); } };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [askKey, err]);

  if (!live || !snap) return null;
  const running = snap.sim_status === 'RUNNING' || !!pacer?.running;

  const toggle = async () => {
    if (!operatorKey.get()) { setAskKey(true); return; }
    setBusy(true); setErr(null);
    try {
      if (running) {
        const runner: Runner = pacer?.running ? { kind: 'pacer', interval_ms: pacer.interval_ms } : { kind: 'sim' };
        try { sessionStorage.setItem(STORE, JSON.stringify(runner)); } catch { /* private mode */ }
        if (pacer?.running) await ops.setPacer(false);
        if (snap.sim_status === 'RUNNING') await ops.sim('pause');
      } else {
        const r = readRunner() ?? { kind: 'pacer', interval_ms: 1000 };
        if (r.kind === 'pacer') await ops.setPacer(true, r.interval_ms);
        else await ops.sim('run');
      }
      await readPacer(); refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? (e.status === 401 ? 'Operator key missing or wrong.' : e.message) : 'Request failed');
    } finally { setBusy(false); }
  };

  return (
    <div className="simctl" ref={ref}>
      <span className={`simctl-state ${running ? 'on' : ''}`}><i />{running ? 'Simulation running' : 'Simulation paused'}</span>
      <button className={`btn sm ${running ? 'dark' : 'primary'}`} onClick={toggle} disabled={busy} aria-label={running ? 'Pause simulation' : 'Resume simulation'}>
        {running
          ? <><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>{busy ? 'Pausing…' : 'Pause'}</>
          : <><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 5v14l12-7z" /></svg>{busy ? 'Resuming…' : 'Resume'}</>}
      </button>
      {askKey && (
        <form className="simctl-pop fade-in" onSubmit={(e) => { e.preventDefault(); if (!key) return; operatorKey.set(key); setAskKey(false); setKey(''); toggle(); }}>
          <span className="xsmall muted">Enter the operator key to control the simulation.</span>
          <div className="row" style={{ flexWrap: 'nowrap' }}>
            <input className="field" type="password" autoComplete="off" autoFocus value={key} onChange={(e) => setKey(e.target.value)} placeholder="Operator key" aria-label="Operator key" />
            <button className="btn dark sm" disabled={!key}>OK</button>
          </div>
        </form>
      )}
      {err && <div className="simctl-pop note warn fade-in">{err}</div>}
    </div>
  );
}
