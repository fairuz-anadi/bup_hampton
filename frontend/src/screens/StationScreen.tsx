import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useLive } from '../api/live';
import type { Fuel } from '../api/types';
import { FUELS } from '../api/types';
import { BackHead, Card, Chip, InventoryBars, Skeleton, fuelColor } from '../components/ui';
import { fuelName, hours, litres, placeName, recLegs, routeName } from '../lib/format';
import { go } from '../lib/router';
import { ops } from '../api/ops';
import { Ask } from '../components/Ask';

type Obs = { tick: number; fuel_type: string; demand_liters: number; unmet_liters: number };

export function StationScreen({ id }: { id?: string }) {
  const { snap, current, source } = useLive();
  const sid = id ?? snap?.stations[0]?.id;
  const [hist, setHist] = useState<Obs[] | null>(null);
  const [histErr, setHistErr] = useState<string | null>(null);
  const tick = snap?.tick;
  useEffect(() => {
    if (!sid || source !== 'live') { setHist(null); return; }
    api.demandHistory(sid, 400).then((h) => { setHist(h); setHistErr(null); }).catch(() => setHistErr('Demand history unavailable'));
  }, [sid, source, tick]);

  if (!snap) return <div className="card"><Skeleton lines={6} /></div>;
  const st = snap.stations.find((s) => s.id === sid);
  if (!st) return <div className="card"><p>Unknown station {sid}.</p></div>;
  const routes = snap.routes.filter((r) => r.destination_station_id === st.id);
  const incoming = snap.in_transit.filter((l) => l.station_id === st.id);
  const risks = current?.recommendation?.risks.filter((r) => r.station_id === st.id) ?? [];
  const planned = current?.recommendation ? recLegs(current.recommendation).filter((l) => l.station_id === st.id) : [];
  const region = snap.regions.find((r) => r.id === st.region_id);

  return (
    <div className="stack" style={{ gap: 14 }}>
      <BackHead back="#/" label="Overview" title={placeName(snap, st.id)} sub="Stock, fuel on the way, risk, supply routes and observed demand for one station." />
      <div className="row" role="tablist" aria-label="Stations">
        {snap.stations.map((s) => (
          <button key={s.id} role="tab" aria-selected={s.id === st.id} className={`btn sm ${s.id === st.id ? 'dark' : ''}`} onClick={() => go(`/station/${s.id}`)}>{placeName(snap, s.id)}</button>
        ))}
      </div>
      <div className="mc">
        <Card className="s5" q={`${region?.name ?? st.region_id} · ${st.demand_profile.replace(/_/g, ' ')}`} title={st.name || placeName(snap, st.id)}
          right={<Chip tone={st.status === 'OPEN' ? 'ok' : 'crit'}>{st.status.toLowerCase()}</Chip>}>
          <div className="stack" style={{ gap: 14 }}>
            <InventoryBars inventory={st.inventory} capacity={st.capacity} inTransit={snap.in_transit_totals[st.id]} />
            <div className="tbl"><table>
              <thead><tr><th>Fuel</th><th className="n">In tank</th><th className="n">Capacity</th><th className="n">In transit</th><th className="n">Stockout in</th></tr></thead>
              <tbody>{FUELS.map((f) => {
                const r = risks.find((x) => x.fuel_type === f);
                return (<tr key={f}><td>{fuelName(f)}</td><td className="n">{litres(st.inventory[f])}</td><td className="n">{litres(st.capacity[f])}</td>
                  <td className="n">{litres(snap.in_transit_totals[st.id]?.[f] ?? 0)}</td>
                  <td className="n">{r ? <span style={{ color: r.p_stockout >= 0.6 ? 'var(--crit)' : undefined }}>{hours(r.hours_to_stockout)} · P {r.p_stockout.toFixed(2)}</span> : '—'}</td></tr>);
              })}</tbody>
            </table></div>
            <p className="xsmall muted">Demand multiplier ×{st.demand_multiplier.toFixed(2)} · region factor ×{region?.demand_factor.toFixed(2) ?? '—'}</p>
          </div>
        </Card>

        <Card className="s7" q="Demand" title="Observed demand per tick" right={<span className="xsmall faint">simulator /v1/demand-history</span>}>
          {source !== 'live' ? <p className="empty">Demand history needs the live backend.</p>
            : histErr ? <p className="note warn">{histErr}</p>
            : !hist ? <Skeleton lines={4} /> : <DemandChart obs={hist} />}
        </Card>

        <Card className="s6" q="Supply" title={`${routes.length} route${routes.length === 1 ? '' : 's'} in`} right={routes.length === 1 && <Chip tone="warn">no backup route</Chip>}>
          <div className="list">
            {routes.map((r) => (
              <div key={r.id} className="row between small">
                <span>{routeName(snap, r.id)} <span className="xsmall faint">· {r.transit_ticks} ticks · max {litres(r.max_shipment)}</span></span>
                <Chip tone={r.status === 'AVAILABLE' ? 'ok' : 'crit'}>{r.status.toLowerCase()}</Chip>
              </div>
            ))}
          </div>
        </Card>
        <Card className="s6" q="On the way" title="Shipments to this station">
          <div className="list">
            {incoming.map((l) => (
              <div key={l.allocation_id} className="row between small">
                <span>{litres(l.quantity)} {fuelName(l.fuel_type).toLowerCase()} from {placeName(snap, l.source_depot_id)}</span>
                <span className="row" style={{ gap: 6 }}><Chip tone="act">{l.status.toLowerCase().replace('_', ' ')}</Chip><span className="mono xsmall faint">arrives t{l.expected_arrival_tick ?? '?'}</span></span>
              </div>
            ))}
            {planned.map((l, i) => (
              <div key={`p${i}`} className="row between small">
                <span>{litres(l.quantity)} {fuelName(l.fuel_type).toLowerCase()} from {placeName(snap, l.source_depot_id)}</span>
                <a href="#/recommendation" className="xsmall">recommended, awaiting review →</a>
              </div>
            ))}
            {!incoming.length && !planned.length && <p className="empty">Nothing in transit.</p>}
          </div>
        </Card>
        <Card className="s12" q="Copilot · investigate" title={`Ask about ${placeName(snap, st.id)}`} right={<span className="tag">read-only</span>}>
          <Ask ask={(q) => ops.investigate(st.id, q)} deps={[st.id]} placeholder={`e.g. Can ${placeName(snap, st.id)} be resupplied if its main route closes?`} />
        </Card>
      </div>
    </div>
  );
}

function DemandChart({ obs }: { obs: Obs[] }) {
  if (!obs.length) return <p className="empty">No observations yet.</p>;
  const W = 640, H = 200, pl = 44, pr = 10, pt = 10, pb = 24;
  const ticks = obs.map((o) => o.tick);
  const t0 = Math.min(...ticks), t1 = Math.max(...ticks);
  const max = Math.max(1, ...obs.map((o) => o.demand_liters));
  const x = (t: number) => pl + ((t - t0) / Math.max(1, t1 - t0)) * (W - pl - pr);
  const y = (v: number) => pt + (1 - v / max) * (H - pt - pb);
  const series = (f: Fuel) => obs.filter((o) => o.fuel_type === f).sort((a, b) => a.tick - b.tick);
  const unmet = obs.filter((o) => o.unmet_liters > 0);
  return (
    <div className="stack" style={{ gap: 6 }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Demand per tick by fuel">
        {[0, 0.5, 1].map((g) => (
          <g key={g}><line x1={pl} x2={W - pr} y1={y(max * g)} y2={y(max * g)} stroke="var(--line-2)" />
            <text x={pl - 6} y={y(max * g) + 3} textAnchor="end" style={{ font: '500 9.5px var(--f-mono)', fill: 'var(--ink-3)' }}>{Math.round(max * g)}</text></g>
        ))}
        {unmet.map((o, i) => <rect key={i} x={x(o.tick) - 1.5} y={pt} width={3} height={H - pt - pb} fill="var(--crit-bg)" />)}
        {FUELS.map((f) => {
          const s = series(f);
          return s.length > 1 && <polyline key={f} fill="none" stroke={fuelColor(f)} strokeWidth={1.6} points={s.map((o) => `${x(o.tick)},${y(o.demand_liters)}`).join(' ')} />;
        })}
        <text x={pl} y={H - 6} style={{ font: '500 9.5px var(--f-mono)', fill: 'var(--ink-3)' }}>t{t0}</text>
        <text x={W - pr} y={H - 6} textAnchor="end" style={{ font: '500 9.5px var(--f-mono)', fill: 'var(--ink-3)' }}>t{t1}</text>
      </svg>
      <div className="row xsmall muted" style={{ gap: 14 }}>
        {FUELS.map((f) => <span key={f} className="row" style={{ gap: 5 }}><i style={{ width: 14, height: 2, background: fuelColor(f), display: 'inline-block' }} />{fuelName(f)} L/tick</span>)}
        <span className="row" style={{ gap: 5 }}><i style={{ width: 8, height: 10, background: 'var(--crit-bg)', display: 'inline-block' }} />ticks with unmet demand</span>
      </div>
    </div>
  );
}
