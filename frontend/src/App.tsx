import { useLive, useNow } from './api/live';
import { Chip, ModeChip } from './components/ui';
import { ago, simClock } from './lib/format';
import { useRoute } from './lib/router';
import { MissionControl } from './screens/MissionControl';
import { NetworkScreen } from './screens/NetworkScreen';
import { StationScreen } from './screens/StationScreen';
import { RecommendationScreen } from './screens/RecommendationScreen';
import { HistoryScreen } from './screens/HistoryScreen';
import { HealthScreen } from './screens/HealthScreen';
import { CrisesScreen } from './screens/CrisesScreen';
import { ChaosLab } from './screens/ChaosLab';

const NAV: [string, string][] = [
  ['', 'Mission Control'], ['network', 'Network'], ['station', 'Stations'],
  ['recommendation', 'Recommendation'], ['crises', 'Crises'], ['history', 'History'], ['health', 'System Health'],
  ['chaos', 'Chaos Lab'],
];

export function App() {
  const [page, arg] = useRoute();
  const { source, snap, current, health, lastOkAt, waiting } = useLive();
  const now = useNow();
  const needsReview = current?.gate?.requires_human && current.record_stage === 'gated';
  const stale = snap?.freshness?.stale;

  return (
    <>
      <header className="top">
        <div className="simstrip"><div className="in"><span className="hatch" aria-hidden="true" />
          <span><b>Simulated environment.</b><span className="long"> FuelGuard runs only on the BUP Fuel Supply Simulator. No real fuel infrastructure, purchases or dispatches.</span></span></div></div>
        <div className="bar">
          <a href="#/" className="brand" style={{ color: 'inherit', textDecoration: 'none' }}><i aria-hidden="true" />FuelGuard</a>
          <nav className="nav" aria-label="Screens">
            {NAV.map(([p, label]) => (
              <a key={p} href={`#/${p}`} className={(page ?? '') === p ? 'on' : ''}>
                {label}{p === 'recommendation' && needsReview && <span className="dot" title="Awaiting review" />}
                {p === 'crises' && snap?.events.some((e) => e.status === 'ACTIVE') && <span className="dot" title="Active crisis" />}
              </a>
            ))}
          </nav>
          <div className="status">
            <span className="clock">tick <b>{snap?.tick ?? '—'}</b> · {simClock(snap)}{snap?.sim_status === 'PAUSED' && ' · paused'}</span>
            <ModeChip mode={current?.autonomy.mode} />
            <Chip tone={source === 'live' ? (stale ? 'crit' : health?.status === 'healthy' ? 'ok' : 'warn') : source === 'offline' ? 'crit' : 'idle'}>
              {source === 'live' ? (stale ? 'stale data' : 'live') : source === 'offline' ? 'offline' : 'mock data'}
            </Chip>
          </div>
        </div>
        {source === 'offline' && (
          <div className="banner crit" role="alert"><b>Backend unreachable.</b> Showing the last snapshot (tick {snap?.tick}), received {lastOkAt ? ago((now - lastOkAt) / 1000) : '—'}. Approvals are paused; retrying every 2 s.</div>
        )}
        {source === 'live' && stale && (
          <div className="banner crit" role="alert"><b>Simulator data is stale.</b> {snap?.freshness?.reasons.slice(0, 2).join('; ')}. Recommendations are recommend-only until the data is fresh.</div>
        )}
        {source === 'live' && !stale && snap?.freshness?.circuit && snap.freshness.circuit !== 'CLOSED' && (
          <div className="banner warn" role="status"><b>Simulator degraded.</b> Circuit breaker {snap.freshness.circuit.toLowerCase().replace('_', '-')}; serving cached state, writes are held.</div>
        )}
        {source === 'mock' && (
          <div className="banner info" role="status"><b>Mock data.</b> No backend reached, so these screens show the shared fixtures (tick {snap?.tick}). Start the stack with <code>docker compose up</code>.</div>
        )}
        {waiting && <div className="banner warn" role="status"><b>Waiting for the first simulator sync.</b> The backend is up but has not read the simulator yet.</div>}
      </header>
      <main className="page">
        {page === 'network' ? <NetworkScreen />
          : page === 'station' ? <StationScreen id={arg} />
          : page === 'recommendation' ? <RecommendationScreen />
          : page === 'history' ? <HistoryScreen />
          : page === 'health' ? <HealthScreen />
          : page === 'crises' ? <CrisesScreen />
          : page === 'chaos' ? <ChaosLab />
          : <MissionControl />}
      </main>
    </>
  );
}
