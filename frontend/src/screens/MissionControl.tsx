import { useLive } from '../api/live';
import type { NetworkSnapshot, Recommendation } from '../api/types';
import { FUELS } from '../api/types';
import { Card, Chip, ModeChip, Skeleton, healthTone, sevTone, type Tone } from '../components/ui';
import { NetworkMap } from '../components/NetworkMap';
import { Explanation, Futures, GateSummary, ImpactLine } from '../components/Decision';
import { eventName, fuelName, hours, litres, pct, placeName, recLegs, routeName } from '../lib/format';
import { go } from '../lib/router';

export function overall(snap: NetworkSnapshot, rec: Recommendation | null): { tone: Tone; label: string; why: string[] } {
  const why: string[] = [];
  const outage = snap.stations.filter((s) => s.status !== 'OPEN');
  const dry = snap.stations.filter((s) => s.status === 'OPEN').flatMap((s) => FUELS.filter((f) => (s.inventory[f] ?? 0) <= 0.5).map((f) => `${placeName(snap, s.id)} ${fuelName(f).toLowerCase()}`));
  const active = snap.events.filter((e) => e.status === 'ACTIVE');
  outage.forEach((s) => why.push(`${placeName(snap, s.id)} in outage`));
  if (dry.length) why.push(`${dry.length} dry tank${dry.length > 1 ? 's' : ''}: ${dry.slice(0, 3).join(', ')}${dry.length > 3 ? '…' : ''}`);
  const crit = (rec?.signals ?? []).some((s) => s.severity === 'crit');
  if (outage.length || dry.length) return { tone: 'crit', label: 'Shortage', why };
  if (crit) return { tone: 'crit', label: 'At risk', why: (rec?.signals ?? []).filter((s) => s.severity === 'crit').map((s) => s.kind.replace(/_/g, ' ')).filter((k, i, a) => a.indexOf(k) === i) };
  if (active.length || snap.freshness?.stale) return { tone: 'warn', label: 'Under pressure', why: active.map((e) => eventName(e.type)) };
  return { tone: 'ok', label: 'Normal', why: ['No active disruptions'] };
}

export function MissionControl() {
  const { snap, current, health, currentError } = useLive();
  if (!snap) return <div className="mc"><div className="card s12"><Skeleton lines={5} /></div></div>;
  const rec = current?.recommendation ?? null;
  const gate = current?.gate ?? null;
  const ov = overall(snap, rec);
  const active = snap.events.filter((e) => e.status === 'ACTIVE');
  const soon = snap.events.filter((e) => e.status === 'SCHEDULED' && e.start_tick - snap.tick <= 8);
  const delayed = snap.supply_arrivals.filter((a) => a.status === 'DELAYED');
  const signals = (rec?.signals ?? []).filter((s) => s.severity !== 'info');
  const risks = [...(rec?.risks ?? [])].sort((a, b) => (a.hours_to_stockout ?? 999) - (b.hours_to_stockout ?? 999)).slice(0, 5);
  const legs = rec ? recLegs(rec) : [];
  const m = snap.metrics;

  return (
    <div className="mc">
      <Card className="s3" q="1 · Are we OK?">
        <div className="stack" style={{ gap: 10 }}>
          <div className="row between"><Chip tone={ov.tone}>{ov.label}</Chip><ModeChip mode={current?.autonomy.mode} /></div>
          <div><div className="big" style={{ color: m && m.service_level < 0.9 ? 'var(--crit)' : m && m.service_level < 0.97 ? 'var(--warn)' : 'var(--ink)' }}>{pct(m?.service_level)}</div>
            <p className="xsmall muted">service level so far (simulator, ground truth)</p></div>
          <div className="row small"><span className="muted">Unmet</span><b className="num">{litres(m?.unmet_demand_liters)}</b><span className="muted">· failures</span><b className="num">{m?.allocation_failures ?? '—'}</b></div>
          {ov.why.length > 0 && <p className="xsmall muted">{ov.why.slice(0, 3).join(' · ')}</p>}
        </div>
      </Card>

      <Card className="s5" q="2 · What is going wrong?" right={<span className="xsmall faint">{active.length} active event(s)</span>}>
        <div className="list">
          {active.map((e) => (
            <div key={e.id} className="row between">
              <div className="grow"><b className="small">{eventName(e.type)}</b> <span className="xsmall muted">{describeEvent(snap, e.parameters)}</span></div>
              <span className="xsmall faint mono">until t{e.end_tick}</span>
            </div>
          ))}
          {signals.slice(0, 4).map((s, i) => (
            <div key={i} className="row"><Chip tone={sevTone(s.severity)}>{s.severity}</Chip><span className="small grow">{s.message}</span></div>
          ))}
          {delayed.map((a) => <div key={a.id} className="small"><Chip tone="warn">delay</Chip> {placeName(snap, a.depot_id)} {fuelName(a.fuel_type).toLowerCase()} supply {litres(a.quantity)} was due t{a.planned_tick}</div>)}
          {soon.map((e) => <div key={e.id} className="small muted">Scheduled: {eventName(e.type)} from t{e.start_tick}</div>)}
          {!active.length && !signals.length && !delayed.length && <p className="empty">Nothing unusual right now.</p>}
        </div>
      </Card>

      <Card className="s4" q="7 · Is our system healthy?" right={health && <Chip tone={healthTone(health.status)}>{health.status}</Chip>}>
        {health ? (
          <div className="list">
            {health.components.map((c) => (
              <div key={c.name} className="row between small"><span>{c.name}</span>
                <span className="row" style={{ gap: 6 }}>{c.detail && <span className="xsmall faint" style={{ maxWidth: 170, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={c.detail}>{c.detail}</span>}<Chip tone={healthTone(c.status)}>{c.status}</Chip></span></div>
            ))}
          </div>
        ) : <Skeleton lines={4} />}
      </Card>

      <Card className="s7" q="4 · What should we do?" right={rec && <button className="btn primary sm" onClick={() => go('/recommendation')}>Review & approve →</button>}>
        {!rec ? (
          <p className="empty">{currentError ? `No recommendation: ${currentError}` : current?.error ? `Decision engine unavailable (${current.error}). Operators can still act from the Network view.` : 'Waiting for the first recommendation…'}</p>
        ) : (
          <div className="stack" style={{ gap: 12 }}>
            {rec.mode === 'containment' && <p className="note crit"><b>Containment mode.</b> A single-route station cannot be fully supplied; the plan spreads the shortage.</p>}
            {legs.length ? (
              <div className="list">
                {legs.map((l, i) => (
                  <div key={i} className="row between">
                    <span className="small"><b className="num">{litres(l.quantity)}</b> {fuelName(l.fuel_type).toLowerCase()} · {routeName(snap, l.route_id)}</span>
                    <span className="xsmall faint mono">{l.route_id}</span>
                  </div>
                ))}
              </div>
            ) : <p className="empty">No shipment recommended this tick.</p>}
            <ImpactLine rec={rec} />
            {gate && <GateSummary gate={gate} />}
          </div>
        )}
      </Card>

      <Card className="s5" q="3 · What is likely to happen?" title="Time to stockout">
        {risks.length ? (
          <div className="list">
            {risks.map((r) => {
              const tone: Tone = r.p_stockout >= 0.6 ? 'crit' : r.p_stockout >= 0.3 ? 'warn' : 'idle';
              return (
                <div key={r.station_id + r.fuel_type} className="row between">
                  <span className="small"><a href={`#/station/${r.station_id}`}>{placeName(snap, r.station_id)}</a> {fuelName(r.fuel_type).toLowerCase()}{!r.has_backup_route && <span className="xsmall" style={{ color: 'var(--warn)' }}> · single route</span>}</span>
                  <span className="row" style={{ gap: 8 }}><span className="small num">{hours(r.hours_to_stockout)}</span><Chip tone={tone}>P {r.p_stockout.toFixed(2)}</Chip></span>
                </div>
              );
            })}
          </div>
        ) : <p className="empty">{rec ? 'No station is projected to run out inside the horizon.' : 'Risk ranking appears with the first recommendation.'}</p>}
      </Card>

      <Card className="s7" q="5 · Why?" right={rec && <span className="xsmall faint mono">{rec.id}</span>}>
        {rec ? <Explanation decisionId={rec.id} /> : <Explanation decisionId={null} />}
      </Card>

      <Card className="s5" q="6 · What if we do nothing?" right={<span className="tag twin">Projected</span>}>
        {rec ? <Futures rec={rec} snap={snap} compact /> : <p className="empty">The Decision Twin runs with each recommendation.</p>}
      </Card>

      <Card className="s12 pad-0" >
        <div className="row between" style={{ padding: '14px 16px 0' }}><div className="stack" style={{ gap: 4 }}><span className="q">Network now</span><h3>2 depots · {snap.stations.length} stations · {snap.routes.length} routes</h3></div>
          <span className="xsmall faint">{snap.in_transit.length} shipment(s) in transit · click a station</span></div>
        <div style={{ padding: '6px 10px 12px' }}><NetworkMap snap={snap} highlight={legs.map((l) => l.route_id)} onStation={(id) => go(`/station/${id}`)} /></div>
      </Card>
    </div>
  );
}

export function describeEvent(snap: NetworkSnapshot, p: Record<string, unknown>): string {
  const bits: string[] = [];
  const ids = (k: string) => ((p[k] as string[] | undefined) ?? []).map((x) => (k === 'route_ids' ? routeName(snap, x) : placeName(snap, x)));
  if (p.region_ids) bits.push(((p.region_ids as string[]) ?? []).map((r) => snap.regions.find((x) => x.id === r)?.name ?? r).join(', '));
  if (p.station_ids) bits.push(ids('station_ids').join(', '));
  if (p.depot_ids) bits.push(ids('depot_ids').join(', '));
  if (p.route_ids) bits.push(ids('route_ids').join(', '));
  if (typeof p.multiplier === 'number') bits.push(`×${p.multiplier}`);
  if (typeof p.factor === 'number') bits.push(`×${p.factor}`);
  if (typeof p.delay_ticks === 'number') bits.push(`+${p.delay_ticks} ticks`);
  return bits.join(' · ');
}
