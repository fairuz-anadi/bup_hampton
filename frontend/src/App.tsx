import type { ReactNode } from 'react';
import { useLive, useNow } from './api/live';
import { ModeChip } from './components/ui';
import { ago, simClock } from './lib/format';
import { useRoute } from './lib/router';
import { Overview } from './screens/Overview';
import { Intelligence } from './screens/Intelligence';
import { SimulationLab } from './screens/SimulationLab';
import { SystemHealth } from './screens/SystemHealth';
import { Architecture } from './screens/Architecture';
import { NetworkScreen } from './screens/NetworkScreen';
import { StationScreen } from './screens/StationScreen';
import { HistoryScreen } from './screens/HistoryScreen';

const I = {
  overview: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7.5" height="7.5" rx="2" /><rect x="13.5" y="3" width="7.5" height="7.5" rx="2" /><rect x="3" y="13.5" width="7.5" height="7.5" rx="2" /><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2" /></svg>,
  intelligence: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l1.9 4.6L18.5 9l-4.6 1.9L12 15.5l-1.9-4.6L5.5 9l4.6-1.4z" /><path d="M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z" /></svg>,
  lab: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 3h6M10 3v6.5L4.5 19a1.5 1.5 0 0 0 1.3 2.2h12.4a1.5 1.5 0 0 0 1.3-2.2L14 9.5V3" /><path d="M7.5 15h9" /></svg>,
  health: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12h4l2.5-6 5 12 2.5-6H21" /></svg>,
  arch: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" /></svg>,
};

// Drill-down pages (station, network, history) highlight the page they belong to.
const SECTION: Record<string, string> = { '': 'overview', overview: 'overview', station: 'overview', network: 'overview',
  intelligence: 'intelligence', recommendation: 'intelligence', history: 'intelligence',
  lab: 'lab', chaos: 'lab', crises: 'lab', health: 'health', architecture: 'architecture' };

export function App() {
  const [page = '', arg] = useRoute();
  const { source, snap, current, lastOkAt, waiting } = useLive();
  const now = useNow();
  const section = SECTION[page] ?? 'overview';
  const needsReview = !!current?.gate?.requires_human && current.record_stage === 'gated';
  const crisis = snap?.events.some((e) => e.status === 'ACTIVE');
  const stale = snap?.freshness?.stale;
  const conn = source === 'live' ? (stale ? ['crit', 'Simulator data stale'] : ['ok', 'Simulator connected'])
    : source === 'offline' ? ['crit', 'Backend unreachable'] : ['idle', 'Mock data'];

  const nav = (key: string, href: string, label: string, icon: ReactNode, dot = false) => (
    <a href={href} className={section === key ? 'on' : ''} aria-current={section === key ? 'page' : undefined}>{icon}{label}{dot && <span className="dot" />}</a>
  );

  let body: ReactNode;
  switch (page) {
    case 'intelligence': case 'recommendation': body = <Intelligence />; break;
    case 'lab': case 'chaos': case 'crises': body = <SimulationLab />; break;
    case 'health': body = <SystemHealth />; break;
    case 'architecture': body = <Architecture />; break;
    case 'station': body = <StationScreen id={arg} />; break;
    case 'network': body = <NetworkScreen />; break;
    case 'history': body = <HistoryScreen />; break;
    default: body = <Overview />;
  }

  return (
    <div className="shell">
      <aside className="side">
        <a href="#/" className="brand"><i aria-hidden="true" /><span>FuelGuard</span></a>
        <nav className="nav" aria-label="Main">
          {nav('overview', '#/', 'Overview', I.overview)}
          {nav('intelligence', '#/intelligence', 'Intelligence', I.intelligence, needsReview)}
          {nav('lab', '#/lab', 'Simulation Lab', I.lab, !!crisis)}
          {nav('health', '#/health', 'System Health', I.health)}
          <div className="sep" />
          {nav('architecture', '#/architecture', 'Architecture', I.arch)}
        </nav>
        <div className="side-foot">
          <div className="conn">
            <div className="l"><span className={`pulse ${conn[0]}`} />{conn[1]}</div>
            <div className="row between"><span className="xsmall muted">Tick <b className="num" style={{ color: 'var(--ink)' }}>{snap?.tick ?? '—'}</b> · {simClock(snap).replace('Day ', 'D')}</span></div>
            <div className="row between"><span className="xsmall muted">{snap?.sim_status === 'RUNNING' ? 'Running' : 'Paused'}</span><ModeChip mode={current?.autonomy.mode} /></div>
          </div>
          <div className="simbadge"><span className="hatch" />Simulated environment</div>
        </div>
      </aside>

      <main className="main">
        <div className="topline">
          <span className="simstrip"><span className="hatch" />Simulated environment · BUP Fuel Supply Simulator · no real fuel is moved</span>
        </div>
        {source === 'offline' && <div className="banner crit" role="alert"><b>Backend unreachable.</b> Showing the last snapshot (tick {snap?.tick}), received {lastOkAt ? ago((now - lastOkAt) / 1000) : '—'}. Approvals are paused; retrying every 2 s.</div>}
        {source === 'live' && stale && <div className="banner crit" role="alert"><b>Simulator data is stale.</b> {snap?.freshness?.reasons.slice(0, 2).join('; ')}. Recommendations are recommend-only until the data is fresh.</div>}
        {source === 'live' && !stale && snap?.freshness?.circuit && snap.freshness.circuit !== 'CLOSED' && <div className="banner warn" role="status"><b>Simulator degraded.</b> Circuit breaker {snap.freshness.circuit.toLowerCase().replace('_', '-')}; serving cached state, writes are held.</div>}
        {source === 'mock' && <div className="banner info" role="status"><b>Mock data.</b> No backend reached, so these pages show the shared example fixtures (tick {snap?.tick}).</div>}
        {waiting && <div className="banner warn" role="status"><b>Waiting for the first simulator sync.</b> The backend is up but has not read the simulator yet.</div>}
        <div className="fade-in" key={page + (arg ?? '')}>{body}</div>
      </main>
    </div>
  );
}
