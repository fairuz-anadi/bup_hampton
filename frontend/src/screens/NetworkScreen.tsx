import { useLive } from '../api/live';
import { FUELS } from '../api/types';
import { Card, Chip, InventoryBars, Skeleton } from '../components/ui';
import { NetworkMap } from '../components/NetworkMap';
import { eventName, fuelName, litres, placeName, recLegs, routeName } from '../lib/format';
import { describeEvent } from './MissionControl';
import { go } from '../lib/router';

export function NetworkScreen() {
  const { snap, current } = useLive();
  if (!snap) return <div className="card"><Skeleton lines={6} /></div>;
  const legs = current?.recommendation ? recLegs(current.recommendation) : [];
  const upcoming = snap.supply_arrivals.filter((a) => a.status === 'SCHEDULED' || a.status === 'DELAYED').sort((a, b) => (a.actual_tick ?? a.planned_tick) - (b.actual_tick ?? b.planned_tick)).slice(0, 8);
  return (
    <div className="mc">
      <Card className="s8 pad-0">
        <div style={{ padding: '14px 16px 0' }} className="stack"><span className="q">Network</span><h3>Depots, stations and routes at tick {snap.tick}</h3></div>
        <div style={{ padding: '6px 10px 12px' }}><NetworkMap snap={snap} highlight={legs.map((l) => l.route_id)} onStation={(id) => go(`/station/${id}`)} /></div>
      </Card>
      <div className="s4 stack" style={{ gap: 14 }}>
        <Card q="Events" title="Disruptions">
          <div className="list">
            {snap.events.filter((e) => e.status !== 'RESOLVED').map((e) => (
              <div key={e.id} className="stack" style={{ gap: 3 }}>
                <div className="row between"><b className="small">{eventName(e.type)}</b><Chip tone={e.status === 'ACTIVE' ? 'crit' : 'idle'}>{e.status.toLowerCase()}</Chip></div>
                <span className="xsmall muted">{describeEvent(snap, e.parameters)} · t{e.start_tick}–t{e.end_tick}</span>
              </div>
            ))}
            {!snap.events.some((e) => e.status !== 'RESOLVED') && <p className="empty">No active or scheduled events.</p>}
          </div>
        </Card>
        <Card q="Supply" title="Next arrivals at depots">
          <div className="list">
            {upcoming.map((a) => (
              <div key={a.id} className="row between small">
                <span>{placeName(snap, a.depot_id)} · {fuelName(a.fuel_type).toLowerCase()} {litres(a.quantity)}</span>
                <span className="row" style={{ gap: 6 }}>{a.status === 'DELAYED' && <Chip tone="warn">delayed</Chip>}<span className="mono xsmall faint">t{a.actual_tick ?? a.planned_tick}</span></span>
              </div>
            ))}
            {!upcoming.length && <p className="empty">No scheduled supply. Depots will not be refilled.</p>}
          </div>
        </Card>
      </div>

      {snap.depots.map((d) => (
        <Card key={d.id} className="s6" q="Depot" title={`${placeName(snap, d.id)} depot`} right={<Chip tone={d.status === 'OPEN' ? 'ok' : 'warn'}>{d.status.toLowerCase()}</Chip>}>
          <InventoryBars inventory={d.inventory} capacity={d.capacity} />
          <p className="xsmall muted" style={{ marginTop: 10 }}>
            {FUELS.map((f) => `${fuelName(f)} ${litres(d.inventory[f])}`).join(' · ')} · dispatched this tick {litres(snap.dispatched_this_tick[d.id] ?? 0)} of {litres(d.dispatch_capacity_per_tick)}
          </p>
        </Card>
      ))}

      <Card className="s12" q="Stations" title="Inventory, capacity and fuel on the way">
        <div className="g2">
          {snap.stations.map((s) => (
            <div key={s.id} className="stack" style={{ gap: 8, padding: 12, border: '1px solid var(--line-2)', borderRadius: 8 }}>
              <div className="row between"><a href={`#/station/${s.id}`} style={{ font: '600 14px var(--f-display)' }}>{placeName(snap, s.id)}</a>
                <span className="row" style={{ gap: 6 }}><span className="xsmall faint">×{s.demand_multiplier.toFixed(1)}</span><Chip tone={s.status === 'OPEN' ? 'ok' : 'crit'}>{s.status.toLowerCase()}</Chip></span></div>
              <InventoryBars inventory={s.inventory} capacity={s.capacity} inTransit={snap.in_transit_totals[s.id]} />
            </div>
          ))}
        </div>
      </Card>

      <Card className="s12" q="Routes" title={`${snap.routes.length} routes`}>
        <div className="tbl"><table>
          <thead><tr><th>Route</th><th>Status</th><th className="n">Transit</th><th className="n">Max shipment</th><th className="n">In transit now</th></tr></thead>
          <tbody>{snap.routes.map((r) => {
            const on = snap.in_transit.filter((l) => l.route_id === r.id);
            return (
              <tr key={r.id} style={legs.some((l) => l.route_id === r.id) ? { background: 'var(--act-bg)' } : undefined}>
                <td>{routeName(snap, r.id)}<div className="xsmall faint mono">{r.id}</div></td>
                <td><Chip tone={r.status === 'AVAILABLE' ? 'ok' : 'crit'}>{r.status.toLowerCase()}</Chip></td>
                <td className="n">{r.transit_ticks} ticks · {(r.transit_ticks * snap.tick_minutes / 60).toFixed(1)} h</td>
                <td className="n">{litres(r.max_shipment)}</td>
                <td className="n">{on.length ? `${on.length} · ${litres(on.reduce((s, l) => s + l.quantity, 0))}` : '—'}</td>
              </tr>);
          })}</tbody>
        </table></div>
      </Card>
    </div>
  );
}
