import { useLive } from '../api/live';
import { Skeleton } from '../components/ui';
import { NetworkMap } from '../components/NetworkMap';
import { approxHours } from '../lib/copy';
import { activeEvents, attention, describeActive, focus, impact, overall, stationStates } from '../lib/derive';
import { fuelName, litres, pct, placeName, recLegs, simClock } from '../lib/format';
import { go } from '../lib/router';

/** "What needs my attention right now?" One status line, one recommendation, then the network. */
export function Overview() {
  const { snap, current } = useLive();
  if (!snap) return <div className="card"><Skeleton lines={6} /></div>;
  const rec = current?.recommendation ?? null;
  const legs = rec ? recLegs(rec) : [];
  const states = stationStates(snap, rec);
  const items = attention(snap, rec, 9);
  const needing = snap.stations.filter((s) => states[s.id].tone !== 'ok');
  const active = activeEvents(snap);
  const status = overall(snap, rec, legs);
  const f = focus(snap, rec, legs);
  const top = items.find((i) => i.station_id === f?.station_id) ?? items[0];
  const others = [...new Set(items.map((i) => i.station_id))].filter((id) => id !== top?.station_id);
  const m = snap.metrics;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Fuel Operations</h1>
          <p>See the current network status and anything that needs your attention.</p>
        </div>
        <span className="xsmall muted">Simulation time <b className="num" style={{ color: 'var(--ink)' }}>{simClock(snap)}</b></span>
      </div>

      <div className={`status ${status.tone}`} role="status">
        <span className="status-dot" />
        <div className="grow">
          <h2>{status.title}</h2>
          <p>{status.sub}</p>
        </div>
        <div className="minis">
          <Mini label="Service level" value={pct(m?.service_level, 0)} warn={!!m && m.service_level < 0.95} />
          <Mini label="Stations needing attention" value={String(needing.length)} warn={needing.length > 0} />
          <Mini label="Active disruptions" value={String(active.length)} warn={active.length > 0} />
        </div>
      </div>

      <div className="mc" style={{ marginBottom: 14 }}>
        <div className="s8">
          {top ? attentionCard() : allClear()}
        </div>
        <div className="s4 stack" style={{ gap: 14 }}>
          {others.length > 0 && (
            <section className="card">
              <span className="q">Also needs attention</span>
              <div className="list" style={{ marginTop: 6 }}>{others.map((id) => {
                const it = items.find((i) => i.station_id === id)!;
                return (
                  <button key={id} className="rowbtn" onClick={() => go(`/station/${id}`)}>
                    <span className={`sdot ${it.tone}`} />
                    <span className="grow"><b>{placeName(snap, id)}</b><span className="xsmall muted" style={{ display: 'block' }}>{line(it)}</span></span>
                    <span className="faint">›</span>
                  </button>);
              })}</div>
            </section>
          )}
          <section className="card">
            <span className="q">Active disruptions</span>
            {active.length ? <div className="list" style={{ marginTop: 6 }}>{active.map((e) => (
              <div key={e.id} className="stack" style={{ gap: 2 }}>
                <b className="small">{describeActive(snap, e)}</b>
                <span className="xsmall muted">{Math.max(0, e.end_tick - snap.tick)} simulation steps remaining</span>
              </div>))}</div>
              : <p className="small muted" style={{ marginTop: 8 }}>No disruptions right now.</p>}
            <button className="link" style={{ marginTop: 12 }} onClick={() => go('/lab')}>Create a disruption in the Scenario Lab →</button>
          </section>
        </div>
      </div>

      <section className="card pad-0">
        <div className="row between" style={{ padding: '18px 20px 0' }}>
          <div className="stack" style={{ gap: 4 }}>
            <h3>Fuel Network</h3>
            <span className="xsmall muted">{snap.depots.length} depots supply {snap.stations.length} stations over {snap.routes.length} routes · {snap.in_transit.length} shipment{snap.in_transit.length === 1 ? '' : 's'} on the road</span>
          </div>
          <button className="link" onClick={() => go('/network')}>Network details →</button>
        </div>
        <div style={{ padding: '4px 12px 14px' }}>
          <NetworkMap snap={snap} states={states} highlight={legs.map((l) => l.route_id)} onStation={(id) => go(`/station/${id}`)} />
        </div>
      </section>
    </>
  );

  function attentionCard() {
    const it = top!;
    const where = placeName(snap!, it.station_id);
    const mine = legs.filter((l) => l.station_id === it.station_id);
    const lead = mine[0] ?? legs[0];
    const im = rec ? impact(snap!, rec) : null;
    const action = lead
      ? `Send ${litres(lead.quantity)} ${fuelName(lead.fuel_type).toLowerCase()} from ${placeName(snap, lead.source_depot_id)} Depot${lead.station_id !== it.station_id ? ` to ${placeName(snap, lead.station_id)}` : ''}${legs.length > 1 ? ` (+${legs.length - 1} more shipment${legs.length > 2 ? 's' : ''})` : ''}`
      : it.kind === 'closed' ? 'No shipment until the station reopens' : 'No shipment needed right now';
    const result = !rec ? 'Recommendation unavailable'
      : !im ? 'Impact projection not available'
      : !legs.length ? (im.without.network_unmet_liters > 0.5 ? `${litres(im.without.network_unmet_liters)} expected shortage remains` : `No shortage expected in ${im.horizon}`)
      : im.with.network_unmet_liters <= 0.5 && im.without.network_unmet_liters > 0.5 ? 'Shortage prevented'
      : im.avoided > 0.5 ? `Expected shortage cut from ${litres(im.without.network_unmet_liters)} to ${litres(im.with.network_unmet_liters)}`
      : `No shortage expected in ${im.horizon}`;
    return (
      <section className="attn">
        <span className="kicker" style={{ color: 'var(--lime)' }}>Needs attention</span>
        <div className="stack" style={{ gap: 4 }}>
          <h2>{where} Station</h2>
          <p className="attn-line">{line(it)}</p>
        </div>
        <div className="attn-grid">
          <div><span className="kicker">Recommended action</span><b>{action}</b></div>
          <div><span className="kicker">Why?</span><span>{it.why}</span></div>
          <div><span className="kicker">Expected result</span><b style={{ color: result === 'Shortage prevented' ? 'var(--lime)' : undefined }}>{result}</b></div>
        </div>
        <div className="row">
          <button className="btn primary" onClick={() => go('/decisions')}>Review decision →</button>
          <button className="btn ghost-dark" onClick={() => go(`/station/${it.station_id}`)}>Station details</button>
        </div>
      </section>
    );
  }

  function allClear() {
    return (
      <section className="card lime allclear">
        <span className="kicker" style={{ color: 'var(--ok)' }}>Needs attention</span>
        <h2>Nothing needs your attention</h2>
        <p className="muted">Every station has enough fuel, counting shipments already on the way. FuelGuard re-checks the whole network every simulation step{legs.length ? '' : ' and currently recommends no shipment'}.</p>
        <div className="row">
          <button className="btn dark" onClick={() => go('/decisions')}>See the current decision →</button>
          <button className="btn" onClick={() => go('/lab')}>Try a scenario</button>
        </div>
      </section>
    );
  }
}

function line(it: ReturnType<typeof attention>[number]): string {
  const fuel = fuelName(it.fuel);
  return it.kind === 'closed' ? 'Temporarily unavailable'
    : it.kind === 'empty' ? `Out of ${fuel.toLowerCase()} now`
    : `${fuel} shortage expected in ~${approxHours(it.hours ?? 0)}`;
}

function Mini({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return <div className="mini"><span className="v" style={{ color: warn ? 'var(--warn)' : undefined }}>{value}</span><span className="l">{label}</span></div>;
}
