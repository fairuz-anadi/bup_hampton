import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useLive, useNow } from './api/live';
import { ops } from './api/ops';
import { ControlBadge } from './components/Control';
import { ago, simClock } from './lib/format';
import { useRoute } from './lib/router';
import { Overview } from './screens/Overview';
import { DecisionCenter } from './screens/DecisionCenter';
import { ScenarioLab } from './screens/ScenarioLab';
import { SystemHealth } from './screens/SystemHealth';
import { Architecture } from './screens/Architecture';
import { NetworkScreen } from './screens/NetworkScreen';
import { StationScreen } from './screens/StationScreen';
import { HistoryScreen } from './screens/HistoryScreen';

const svg = (d: ReactNode) => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{d}</svg>;
const I = {
  overview: svg(<><rect x="3" y="3" width="7.5" height="7.5" rx="2" /><rect x="13.5" y="3" width="7.5" height="7.5" rx="2" /><rect x="3" y="13.5" width="7.5" height="7.5" rx="2" /><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2" /></>),
  decision: svg(<><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z" /><path d="M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z" /></>),
  lab: svg(<><path d="M9 3h6M10 3v6.5L4.5 19a1.5 1.5 0 0 0 1.3 2.2h12.4a1.5 1.5 0 0 0 1.3-2.2L14 9.5V3" /><path d="M7.5 15h9" /></>),
};

// Old paths (#/intelligence, #/chaos, ...) still land on the page that replaced them.
const SECTION: Record<string, string> = { '': 'overview', overview: 'overview', station: 'overview', network: 'overview',
  decisions: 'decisions', intelligence: 'decisions', recommendation: 'decisions',
  lab: 'lab', chaos: 'lab', crises: 'lab', health: 'health', architecture: 'architecture', history: 'history' };

export function App() {
  const [page = '', arg] = useRoute();
  const { source, snap, current, lastOkAt, waiting, scenario } = useLive();
  const now = useNow();
  const section = SECTION[page] ?? 'overview';
  const needsReview = !!current?.gate?.requires_human && current.record_stage === 'gated';
  const stale = snap?.freshness?.stale;
  const conn = source === 'live' ? (stale ? ['crit', 'Simulator data out of date'] : ['ok', 'Simulator connected'])
    : source === 'offline' ? ['crit', 'Backend unreachable'] : ['idle', 'Example data'];

  const nav = (key: string, href: string, label: string, icon?: ReactNode, dot = false) => (
    <a href={href} className={section === key ? 'on' : ''} aria-current={section === key ? 'page' : undefined}>{icon}{label}{dot && <span className="dot" />}</a>
  );

  let body: ReactNode;
  switch (section === 'overview' ? page : section) {
    case 'decisions': body = <DecisionCenter />; break;
    case 'lab': body = <ScenarioLab />; break;
    case 'health': body = <SystemHealth />; break;
    case 'architecture': body = <Architecture />; break;
    case 'history': body = <HistoryScreen />; break;
    case 'station': body = <StationScreen id={arg} />; break;
    case 'network': body = <NetworkScreen />; break;
    default: body = <Overview />;
  }

  return (
    <div className="shell">
      <ScenarioDriver />
      <aside className="side">
        <a href="#/" className="brand"><i aria-hidden="true" /><span>FuelGuard</span></a>
        <nav className="nav" aria-label="Main">
          {nav('overview', '#/', 'Overview', I.overview)}
          {nav('decisions', '#/decisions', 'Decision Center', I.decision, needsReview)}
          {nav('lab', '#/lab', 'Scenario Lab', I.lab, !!scenario && !scenario.error)}
          <div className="nav-tech">
            <span className="kicker">Technical details</span>
            {nav('architecture', '#/architecture', 'Architecture')}
            {nav('health', '#/health', 'System health')}
            {nav('history', '#/history', 'Decision history')}
          </div>
        </nav>
        <div className="side-foot">
          <div className="conn">
            <div className="l"><span className={`pulse ${conn[0]}`} />{conn[1]}</div>
            <div className="stack" style={{ gap: 2 }}>
              <span className="xsmall muted">Simulation time</span>
              <b className="small num">{simClock(snap)}</b>
              <span className="xsmall faint">{snap?.sim_status === 'RUNNING' ? 'Running' : 'Paused'} · technical: step {snap?.tick ?? '—'}</span>
            </div>
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="topline">
          <span className="simstrip"><span className="hatch" />Simulation only · no real fuel is moved</span>
          <ControlBadge />
        </div>
        {source === 'offline' && <div className="banner crit" role="alert"><b>Backend unreachable.</b> Showing the last data we received ({lastOkAt ? ago((now - lastOkAt) / 1000) : '—'}). Approvals are paused; retrying every 2 s.</div>}
        {source === 'live' && stale && <div className="banner crit" role="alert"><b>Simulator data is out of date.</b> FuelGuard will only recommend until fresh data arrives. <span className="faint">{snap?.freshness?.reasons.slice(0, 2).join('; ')}</span></div>}
        {source === 'live' && !stale && snap?.freshness?.circuit && snap.freshness.circuit !== 'CLOSED' && <div className="banner warn" role="status"><b>Simulator is responding slowly.</b> Showing the last good data; shipments are held until it recovers.</div>}
        {source === 'mock' && <div className="banner info" role="status"><b>Example data.</b> No backend reached, so these pages show a recorded example situation.</div>}
        {waiting && <div className="banner warn" role="status"><b>Connecting to the simulator…</b> The backend is up but has not read the simulator yet.</div>}
        <div className="fade-in" key={page + (arg ?? '')}>{body}</div>
      </main>
    </div>
  );
}

/**
 * While a scenario runs on a paused simulator (and no pacer), advance it a few steps so the response is visible
 * wherever the presenter is. Stops as soon as FuelGuard has re-planned after the event started.
 */
function ScenarioDriver() {
  const { scenario, setScenario, snap, current, source, refresh } = useLive();
  const [pacer, setPacer] = useState<boolean | null>(null);
  const stepping = useRef(false);
  const live = source === 'live';
  const on = !!scenario && !scenario.error && scenario.kind === 'event' && live;
  useEffect(() => {
    if (!on) return;
    const read = () => ops.pacer().then((p) => setPacer(p.running)).catch(() => setPacer(null));
    read(); const t = setInterval(read, 3000);
    return () => clearInterval(t);
  }, [on]);
  const replanned = !!scenario && (current?.recommendation?.tick ?? -1) >= scenario.startTick;
  useEffect(() => {
    if (!on || !scenario || stepping.current || replanned || scenario.steps >= 6) return;
    if (snap?.sim_status === 'RUNNING' || pacer !== false) return;
    stepping.current = true;
    const t = setTimeout(async () => {
      try { await ops.sim('step'); setScenario((r) => (r ? { ...r, steps: r.steps + 1 } : r)); refresh(); } catch { /* shown via health */ }
      stepping.current = false;
    }, 1300);
    return () => { clearTimeout(t); stepping.current = false; };
  }, [on, scenario, replanned, snap?.tick, snap?.sim_status, pacer, refresh, setScenario]);
  return null;
}
