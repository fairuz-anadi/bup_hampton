import type { NetworkSnapshot } from '../api/types';
import { FUELS } from '../api/types';
import { placeName } from '../lib/format';
import { fuelColor } from './ui';

const W = 760, NODE_W = 170, NODE_H = 58, DX = 40, SX = W - NODE_W - 40;

type Pt = [number, number];
const bez = (p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): Pt => {
  const u = 1 - t;
  return [0, 1].map((k) => u * u * u * p0[k] + 3 * u * u * t * p1[k] + 3 * u * t * t * p2[k] + t * t * t * p3[k]) as Pt;
};

/** Depots on the left, stations on the right, grouped by region. Everything comes from the snapshot. */
export function NetworkMap({ snap, highlight = [], onStation }: { snap: NetworkSnapshot; highlight?: string[]; onStation?: (id: string) => void }) {
  const regionOrder = snap.regions.map((r) => r.id);
  const byRegion = <T extends { region_id: string; id: string }>(xs: T[]) =>
    [...xs].sort((a, b) => regionOrder.indexOf(a.region_id) - regionOrder.indexOf(b.region_id) || a.id.localeCompare(b.id));
  const stations = byRegion(snap.stations);
  const depots = byRegion(snap.depots);
  const H = Math.max(stations.length, 2) * 84 + 40;
  const sy = (i: number) => 30 + i * 84;
  const stPos = Object.fromEntries(stations.map((s, i) => [s.id, sy(i)]));
  // Each depot sits at the middle of its region's stations.
  const dPos = Object.fromEntries(depots.map((d) => {
    const ys = stations.filter((s) => s.region_id === d.region_id).map((s) => stPos[s.id]);
    return [d.id, ys.length ? (Math.min(...ys) + Math.max(...ys)) / 2 : H / 2 - NODE_H / 2];
  }));
  const routesTo = (sid: string) => snap.routes.filter((r) => r.destination_station_id === sid).length;
  const disruptedUntil = (rid: string) =>
    snap.events.find((e) => e.type === 'route_disruption' && e.status === 'ACTIVE' && ((e.parameters.route_ids as string[] | undefined) ?? []).includes(rid))?.end_tick;

  const geo = (rid: string) => {
    const r = snap.routes.find((x) => x.id === rid)!;
    const p0: Pt = [DX + NODE_W, (dPos[r.source_depot_id] ?? 0) + NODE_H / 2];
    const p3: Pt = [SX, (stPos[r.destination_station_id] ?? 0) + NODE_H / 2];
    const mx = (p0[0] + p3[0]) / 2;
    return { r, p0, p1: [mx, p0[1]] as Pt, p2: [mx, p3[1]] as Pt, p3 };
  };

  return (
    <div className="map">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Network map: depots, stations and routes">
        <defs>
          <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="var(--sunken)" /><line x1="0" y1="0" x2="0" y2="6" stroke="var(--line)" strokeWidth="3" />
          </pattern>
        </defs>
        {snap.regions.map((reg) => {
          const ys = stations.filter((s) => s.region_id === reg.id).map((s) => stPos[s.id]);
          if (!ys.length) return null;
          const y0 = Math.min(...ys) - 14, y1 = Math.max(...ys) + NODE_H + 10;
          return (
            <g key={reg.id}>
              <rect x={8} y={y0} width={W - 16} height={y1 - y0} rx={10} fill="none" stroke="var(--line)" strokeDasharray="3 4" />
              <text x={18} y={y0 + 13} style={{ font: '600 9.5px var(--f-mono)', letterSpacing: '.08em', fill: 'var(--ink-3)' }}>
                {reg.name.toUpperCase()} · DEMAND ×{reg.demand_factor.toFixed(2)}
              </text>
            </g>
          );
        })}
        {snap.routes.map((route) => {
          const { r, p0, p1, p2, p3 } = geo(route.id);
          const hl = highlight.includes(r.id);
          const down = r.status === 'DISRUPTED';
          const mid = bez(p0, p1, p2, p3, 0.5);
          const until = disruptedUntil(r.id);
          return (
            <g key={r.id}>
              <path d={`M${p0} C${p1} ${p2} ${p3}`} fill="none" stroke={down ? 'var(--crit)' : hl ? 'var(--act)' : 'var(--ink-3)'}
                strokeWidth={hl ? 3.5 : down ? 2 : 1.5} strokeDasharray={down ? '6 5' : undefined} opacity={hl || down ? 1 : 0.7} />
              <g transform={`translate(${mid[0]},${mid[1]})`}>
                <rect x={-34} y={-10} width={68} height={20} rx={4} fill="var(--surface)" stroke={down ? 'var(--crit)' : 'var(--line)'} />
                <text textAnchor="middle" y={4} style={{ font: '500 10px var(--f-mono)', fill: down ? 'var(--crit)' : 'var(--ink-2)' }}>
                  {down ? (until != null ? `✕ to t${until}` : '✕ down') : `${r.transit_ticks} ticks`}
                </text>
              </g>
            </g>
          );
        })}
        {snap.in_transit.map((leg) => {
          const g = snap.routes.find((x) => x.id === leg.route_id) && geo(leg.route_id);
          if (!g) return null;
          const left = (leg.expected_arrival_tick ?? snap.tick) - snap.tick;
          let t = leg.status === 'PENDING' ? 0.06 : Math.min(0.94, Math.max(0.06, 1 - left / Math.max(1, g.r.transit_ticks)));
          if (t > 0.38 && t < 0.62) t = t < 0.5 ? 0.37 : 0.63; // keep clear of the route label at the midpoint
          const [x, y] = bez(g.p0, g.p1, g.p2, g.p3, t);
          return (
            <g key={leg.allocation_id}>
              <circle cx={x} cy={y} r={6.5} fill={fuelColor(leg.fuel_type)} stroke="var(--surface)" strokeWidth={2}>
                <title>{`${Math.round(leg.quantity).toLocaleString()} L ${leg.fuel_type} · ${leg.status} · arrives t${leg.expected_arrival_tick ?? '?'}`}</title>
              </circle>
            </g>
          );
        })}
        {depots.map((d) => (
          <g key={d.id} transform={`translate(${DX},${dPos[d.id]})`}>
            <rect width={NODE_W} height={NODE_H} rx={8} fill="url(#hatch)" stroke={d.status === 'OPEN' ? 'var(--ink-3)' : 'var(--warn)'} strokeWidth={1.5} />
            <text x={10} y={20} style={{ font: '600 13px var(--f-display)', fill: 'var(--ink)' }}>{placeName(snap, d.id)} depot</text>
            <text x={10} y={36} style={{ font: '500 10px var(--f-mono)', fill: d.status === 'OPEN' ? 'var(--ink-3)' : 'var(--warn)' }}>
              {d.status} · {Math.round(d.dispatch_capacity_per_tick / 1000)}k L/tick
            </text>
            <MiniBars inv={d.inventory} cap={d.capacity} x={10} y={44} />
          </g>
        ))}
        {stations.map((s) => {
          const one = routesTo(s.id) === 1;
          const empty = FUELS.some((f) => (s.inventory[f] ?? 0) <= 0.5);
          const stroke = s.status !== 'OPEN' ? 'var(--crit)' : empty ? 'var(--crit)' : 'var(--line)';
          return (
            <g key={s.id} className="node" transform={`translate(${SX},${stPos[s.id]})`} onClick={() => onStation?.(s.id)}
              role="link" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onStation?.(s.id)} aria-label={`Open ${placeName(snap, s.id)}`}>
              <rect width={NODE_W} height={NODE_H} rx={8} fill={s.status !== 'OPEN' ? 'var(--crit-bg)' : 'var(--surface)'} stroke={stroke} strokeWidth={1.5} />
              <text x={10} y={20} style={{ font: '600 13px var(--f-display)', fill: 'var(--ink)' }}>{placeName(snap, s.id)}</text>
              <text x={10} y={36} style={{ font: '500 10px var(--f-mono)', fill: s.status !== 'OPEN' ? 'var(--crit)' : 'var(--ink-3)' }}>
                {s.status === 'OPEN' ? `×${s.demand_multiplier.toFixed(1)} demand` : 'OUTAGE'}{one ? ' · 1 route' : ''}
              </text>
              <MiniBars inv={s.inventory} cap={s.capacity} x={10} y={44} />
            </g>
          );
        })}
      </svg>
      <div className="row xsmall muted" style={{ padding: '4px 4px 0', gap: 14 }}>
        {FUELS.map((f) => <span key={f} className="row" style={{ gap: 5 }}><i style={{ width: 9, height: 9, borderRadius: 5, background: fuelColor(f), display: 'inline-block' }} />{f.toLowerCase()} in transit</span>)}
        <span className="row" style={{ gap: 5 }}><i style={{ width: 18, borderTop: '2px dashed var(--crit)', display: 'inline-block' }} />disrupted</span>
        {highlight.length > 0 && <span className="row" style={{ gap: 5 }}><i style={{ width: 18, borderTop: '3px solid var(--act)', display: 'inline-block' }} />recommended</span>}
      </div>
    </div>
  );
}

function MiniBars({ inv, cap, x, y }: { inv: Record<string, number | undefined>; cap: Record<string, number | undefined>; x: number; y: number }) {
  const w = (NODE_W - 28) / 3;
  return (
    <g transform={`translate(${x},${y})`}>
      {FUELS.map((f, i) => {
        const share = cap[f] ? Math.min(1, (inv[f] ?? 0) / (cap[f] as number)) : 0;
        return (
          <g key={f} transform={`translate(${i * (w + 4)},0)`}>
            <rect width={w} height={6} rx={3} fill="var(--sunken)" />
            <rect width={Math.max(share > 0 ? 3 : 0, w * share)} height={6} rx={3} fill={share < 0.2 ? 'var(--crit)' : share < 0.4 ? 'var(--warn)' : fuelColor(f)} />
            <title>{`${f}: ${Math.round(share * 100)}%`}</title>
          </g>
        );
      })}
    </g>
  );
}
