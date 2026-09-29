import { useLive } from '../api/live';
import { Chip, ModeChip, Skeleton, confTone, toneColor } from '../components/ui';
import { NetworkMap } from '../components/NetworkMap';
import { attention, overall, stationStates } from '../lib/derive';
import { eventName, fuelName, hours, litres, pct, placeName, recLegs, simClock } from '../lib/format';
import { go } from '../lib/router';

/** What is happening across the network right now. A summary, not a control room. */
export function Overview() {
  const { snap, current } = useLive();
  if (!snap) return <div className="card"><Skeleton lines={6} /></div>;
  const rec = current?.recommendation ?? null;
  const states = stationStates(snap, rec);
  const risky = snap.stations.filter((s) => states[s.id].tone !== 'ok');
  const active = snap.events.filter((e) => e.status === 'ACTIVE');
  const items = attention(snap, rec);
  const ov = overall(snap, rec);
  const m = snap.metrics;
  const a = current?.autonomy;
  const legs = rec ? recLegs(rec) : [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Fuel Operations</h1>
          <p>Monitor the network, identify risks, and review recommended actions.</p>
        </div>
        <div className="row"><Chip tone={ov.tone}>{ov.label}</Chip><span className="xsmall muted">Tick {snap.tick} · {simClock(snap)}</span></div>
      </div>

      <div className="kpis">
        <div className="kpi">
          <span className="kicker">Service level</span>
          <span className="v" style={{ color: m && m.service_level < 0.9 ? 'var(--crit)' : undefined }}>{pct(m?.service_level)}</span>
          <span className="s">{m ? `${litres(m.unmet_demand_liters)} unmet so far` : '—'}</span>
        </div>
        <div className="kpi">
          <span className="kicker">Stations at risk</span>
          <span className="v" style={{ color: risky.length ? toneColor(risky.some((s) => states[s.id].tone === 'crit') ? 'crit' : 'warn') : undefined }}>{risky.length} <small>of {snap.stations.length}</small></span>
          <span className="s">{risky.length ? risky.map((s) => placeName(snap, s.id)).join(', ') : 'All stations healthy'}</span>
        </div>
        <button className="kpi button" onClick={() => go('/lab')} aria-label="Active events: open the Simulation Lab">
          <span className="kicker">Active events</span>
          <span className="v">{active.length}</span>
          <span className="s">{active.length ? active.map((e) => eventName(e.type)).join(', ') : 'No disruptions'}</span>
        </button>
        <button className="kpi button" onClick={() => go('/intelligence')} aria-label="System mode: open Intelligence">
          <span className="kicker">System mode</span>
          <span className="v" style={{ fontSize: 24, lineHeight: '32px' }}>{a ? a.mode.charAt(0) + a.mode.slice(1).toLowerCase() : '—'}</span>
          <span className="s">{a ? <>Confidence <b style={{ color: toneColor(confTone(a.confidence)) }}>{Math.round(a.confidence * 100)}%</b></> : '—'}</span>
        </button>
      </div>

      <div className="mc">
        <section className="card s8 pad-0">
          <div className="row between" style={{ padding: '18px 20px 0' }}>
            <div className="stack" style={{ gap: 4 }}><span className="q">Live fuel network</span>
              <h3>{snap.depots.length} depots · {snap.stations.length} stations · {snap.routes.length} routes</h3></div>
            <div className="row"><span className="xsmall muted">{snap.in_transit.length} shipment(s) on the road</span><button className="link" onClick={() => go('/network')}>Details →</button></div>
          </div>
          <div style={{ padding: '4px 12px 14px' }}>
            <NetworkMap snap={snap} states={states} highlight={legs.map((l) => l.route_id)} onStation={(id) => go(`/station/${id}`)} />
          </div>
        </section>

        <div className="s4 stack" style={{ gap: 14 }}>
          <div className="row between"><h3>Needs attention</h3>{items.length > 0 && <span className="xsmall muted">{items.length} item{items.length > 1 ? 's' : ''}</span>}</div>
          {items.length === 0 ? (
            <div className="card lime">
              <div className="stack" style={{ gap: 8 }}>
                <span className="kicker" style={{ color: 'var(--ok)' }}>All clear</span>
                <h3>Network operating normally</h3>
                <p className="small muted">{pct(m?.service_level)} service level · 0 critical risks. Try a scenario in the Simulation Lab to see the system respond.</p>
                <div><button className="btn sm" onClick={() => go('/lab')}>Open Simulation Lab →</button></div>
              </div>
            </div>
          ) : items.map((it, i) => (
            <div key={it.station_id + it.fuel} className={`card ${i === 0 ? 'dark' : ''}`}>
              <div className="stack" style={{ gap: 10 }}>
                <div className="row between">
                  <b style={{ font: '600 16px var(--f-display)' }}>{placeName(snap, it.station_id)} · {fuelName(it.fuel)}</b>
                  <Chip tone={it.tone}>{it.p >= 1 ? 'now' : `${Math.round(it.p * 100)}%`}</Chip>
                </div>
                <p style={{ fontSize: 15 }}>
                  {it.hours === 0 ? <b>Out of fuel now</b> : it.hours == null ? <b>Station in outage</b>
                    : <>Shortage risk in <b>{hours(it.hours)}</b></>}
                </p>
                <p className="small muted">{it.why}</p>
                {i === 0 && rec && <div><button className="btn primary sm" onClick={() => go('/intelligence')}>Review recommendation →</button></div>}
              </div>
            </div>
          ))}
          {a && (
            <button className="card" style={{ border: 0, textAlign: 'left', cursor: 'pointer' }} onClick={() => go('/intelligence')}>
              <div className="row between"><span className="kicker">Decision confidence</span><ModeChip mode={a.mode} /></div>
              <p className="small muted" style={{ marginTop: 8 }}>{modeLine(a.mode)}</p>
            </button>
          )}
        </div>
      </div>
    </>
  );
}

export function modeLine(mode: string): string {
  return mode === 'AUTONOMOUS' ? 'Confidence is high: routine allocations may run inside guardrails.'
    : mode === 'SUPERVISED' ? 'Recommendations are reviewed by an operator before anything is sent.'
    : 'Safe mode: the system recommends only. Every action needs a human.';
}
