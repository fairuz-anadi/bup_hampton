import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { useLive } from '../api/live';
import type { AllocationLeg, CurrentView, NetworkSnapshot, Recommendation } from '../api/types';
import { Alternatives, Explanation, Futures, Review } from '../components/Decision';
import { Scoreboard } from '../components/Scoreboard';
import { Chip, Skeleton } from '../components/ui';
import { approxHours, planName, techName } from '../lib/copy';
import { activeEvents, arrival, constraintChecks, demandEvidence, describeActive, focus, impact, situation, summarize, uniqueSignals, whyBullets, type DemandEvidence, type DemandObs, type Impact } from '../lib/derive';
import { fuelName, humanize, litres, pct, placeName, recConstraints, recLegs, recPolicy, simClock } from '../lib/format';

type St = 'done' | 'wait' | 'off';

/** One page, read top to bottom: what's happening → recommendation → expected impact → alternatives → human review. */
export function DecisionCenter() {
  const { snap, current, currentError, source, scenario, recChange } = useLive();
  const [obs, setObs] = useState<DemandObs[] | null>(null);
  const rec = current?.recommendation ?? null;
  const legs = useMemo(() => (rec ? recLegs(rec) : []), [rec]);
  const f = snap ? focus(snap, rec, legs) : null;
  const tick = snap?.tick;

  useEffect(() => {
    if (!f || source !== 'live') { setObs(null); return; }
    api.demandHistory(f.station_id, 800).then(setObs).catch(() => setObs(null));
  }, [f?.station_id, source, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!snap || (!current && !currentError)) return <div className="card"><Skeleton lines={6} /></div>;
  const closed = !!f && snap.stations.find((s) => s.id === f.station_id)?.status !== 'OPEN';
  const ev = f && obs && !closed ? demandEvidence(obs, f.fuel) : null;
  const showStock = !!f?.risk && f.risk.hours_to_stockout != null && invOf(snap, f) > 0.5;

  const head = (
    <div className="page-head">
      <div>
        <h1>Decision Center</h1>
        <p>Understand the current risk, FuelGuard’s recommendation, and its expected impact.</p>
      </div>
      {rec && <span className="xsmall muted" style={{ textAlign: 'right' }}>Last checked at <b style={{ color: 'var(--ink)' }}>{simClockAt(snap, rec.tick)}</b><br /><span className="faint">technical: step {rec.tick} · {rec.id}</span></span>}
    </div>
  );

  if (!rec) {
    return (
      <>{head}
        <section className="card">
          <h2>No recommendation right now</h2>
          <p className="muted" style={{ marginTop: 8 }}>FuelGuard’s decision engine didn’t return a recommendation, so nothing will be sent. The network is still being monitored.</p>
          <details className="more" style={{ marginTop: 12 }}><summary>Technical details</summary>
            <p className="small muted mono" style={{ marginTop: 8 }}>{currentError ?? current?.error ?? 'engine unavailable'}</p></details>
        </section>
      </>
    );
  }

  const sit = situation(snap, rec, f, ev);
  const alsoActive = activeEvents(snap).filter((e) => !((e.parameters.station_ids as string[] | undefined) ?? []).includes(f?.station_id ?? '')).map((e) => describeActive(snap, e));
  const im = impact(snap, rec);
  const stage = current!.record_stage;
  const decided = !!stage && stage !== 'gated';
  const story: [string, St, string][] = [
    ['Problem detected', f ? 'done' : 'off', f ? problemShort(snap, f, sit.ratio) : 'Nothing unusual'],
    ['Risk forecast', f?.risk?.hours_to_stockout != null ? 'done' : 'off', f?.risk?.hours_to_stockout != null ? `Shortage in ~${approxHours(f.risk.hours_to_stockout)}` : 'No shortage expected'],
    ['Recommendation created', 'done', legs.length ? summarize(snap, legs).split(' · ')[0] : 'No shipment needed'],
    ['Impact checked', im ? 'done' : 'off', im ? (legs.length && im.avoided > 0.5 ? `${litres(im.avoided)} avoided` : 'Outcome projected') : 'Not available'],
    ['Human review', !legs.length ? 'off' : decided ? 'done' : 'wait', !legs.length ? 'Not needed' : decided ? (stage === 'rejected' ? 'Rejected' : 'Approved') : current!.gate?.requires_human ? 'Waiting for you' : 'Cleared to run'],
  ];

  return (
    <>
      {head}
      {updateBanner(snap, rec)}

      <nav className="story" aria-label="How FuelGuard reached this decision">
        {story.map(([label, st, text], i) => (
          <a key={label} href={`#/decisions`} onClick={(e) => { e.preventDefault(); document.getElementById(`dc-${i + 1}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }} className={st}>
            <span className="n">{st === 'done' ? '✓' : st === 'wait' ? '●' : i + 1}</span>
            <span className="stack" style={{ gap: 1 }}><b>{label}</b><span className="xsmall">{text}</span></span>
          </a>
        ))}
      </nav>

      <div className="dc">
        {/* 1 · What's happening? */}
        <section className="card" id="dc-1">
          <span className="q">1 · What’s happening?</span>
          <div className="mc" style={{ marginTop: 8 }}>
            <div className={ev || showStock ? 's6' : 's12'}>
              <h2 className="lead">{sit.headline}</h2>
              {sit.detail && <p className="lead-sub">{sit.detail}</p>}
              {f && alsoActive.length > 0 && <p className="small muted" style={{ marginTop: 10 }}>Also happening: {alsoActive.join('; ')}.</p>}
            </div>
            {(ev || showStock) && (
              <div className="s6">
                {ev ? <><DemandSpark ev={ev} /><p className="xsmall muted">Demand per simulation step · <span style={{ color: 'var(--ok)' }}>shaded band = normal demand</span></p></>
                  : <><InventoryProjection inv={invOf(snap, f!)} hrs={f!.risk!.hours_to_stockout!} capacity={snap.stations.find((s) => s.id === f!.station_id)?.capacity[f!.fuel] ?? 0} />
                    <p className="xsmall muted">Projected {fuelName(f!.fuel).toLowerCase()} stock at {placeName(snap, f!.station_id)} if nothing changes</p></>}
              </div>
            )}
          </div>
          <div className="disclose">
            <details className="more"><summary>How was this detected?</summary><Detected snap={snap} rec={rec} ev={ev} live={source === 'live'} /></details>
            {f?.risk && <details className="more"><summary>How was this forecast?</summary><Forecast snap={snap} rec={rec} f={f} /></details>}
          </div>
        </section>

        {/* 2 · Recommendation */}
        <section id="dc-2">
          {legs.length ? <Recommend snap={snap} rec={rec} legs={legs} f={f} cur={current!} /> : <NoShipment snap={snap} rec={rec} im={im} f={f} />}
        </section>

        {/* 3 · Expected impact */}
        <section className="card" id="dc-3">
          <span className="q">3 · Expected impact</span>
          <h2 style={{ marginTop: 6 }}>What if we do this?</h2>
          {!im ? <p className="empty" style={{ marginTop: 8 }}>An impact projection isn’t available for this recommendation.</p> : (
            <ImpactView im={im} shipping={legs.length > 0} />
          )}
          <div className="disclose">
            <details className="more"><summary>View technical details</summary>
              <div className="stack" style={{ gap: 12, marginTop: 10 }}>
                <p className="small muted">The Decision Twin copies the current network and simulates each plan for the next {im?.without.horizon_ticks ?? '—'} steps (the horizon). “Without this action” is the counterfactual: the same simulation with no shipment. Nothing is sent while it runs. After the horizon, the Twin compares its projection with what the simulator actually did, and that error feeds decision confidence.</p>
                <Futures rec={rec} snap={snap} />
                <Scoreboard compact />
              </div>
            </details>
          </div>
        </section>

        {/* 4 · Alternatives */}
        <details className="card alts-card" id="dc-4">
          <summary><span className="q">4 · Other options considered</span><span className="xsmall muted">{Math.max(0, (rec.futures?.length || rec.twin_futures?.length || 0))} options compared</span></summary>
          <div style={{ marginTop: 12 }}><Alternatives rec={rec} /></div>
          <p className="xsmall muted" style={{ marginTop: 10 }}>The priority-based plan serves the most urgent station first. The optimized plan solves for the least total shortage across the whole network.</p>
        </details>

        {/* 5 · Human review */}
        <section className="card" id="dc-5">
          <span className="q">5 · Human review</span>
          {legs.length ? <HumanReview rec={rec} cur={current!} /> : (
            <div className="stack" style={{ gap: 4, marginTop: 6 }}>
              <h2>No approval needed</h2>
              <p className="muted">No shipment is recommended at this time. FuelGuard will ask for review as soon as it recommends one.</p>
            </div>
          )}
        </section>
      </div>
      {scenario && <p className="xsmall faint" style={{ marginTop: 14, textAlign: 'center' }}>Scenario in the lab: {scenario.title}. <a href="#/lab">Back to the Scenario Lab</a></p>}
    </>
  );

  function updateBanner(snap: NetworkSnapshot, rec: Recommendation) {
    if (scenario && !scenario.error && rec.tick >= scenario.startTick) {
      const changed = recChange && recChange.tick >= scenario.startTick;
      return (
        <div className={`updated ${changed ? 'on' : ''}`} role="status">
          <span className="kicker">{changed ? 'Recommendation updated because conditions changed' : 'Re-checked after conditions changed'}</span>
          <p><b>{scenario.title}</b> started at {simClockAt(snap, scenario.startTick)}. {changed
            ? <>FuelGuard re-evaluated the network and changed its recommendation.</>
            : <>FuelGuard re-evaluated the network at {simClockAt(snap, rec.tick)}; the recommendation did not need to change.</>}</p>
          {changed && <div className="ba"><span><span className="xsmall muted">Before</span><s>{recChange!.before}</s></span><span className="arrow">→</span><span><span className="xsmall muted">Now</span><b>{recChange!.after}</b></span></div>}
        </div>
      );
    }
    if (recChange && snap.tick - recChange.tick <= 4) {
      return <div className="updated on" role="status"><span className="kicker">Recommendation updated at {simClockAt(snap, recChange.tick)}</span>
        <div className="ba"><span><span className="xsmall muted">Before</span><s>{recChange.before}</s></span><span className="arrow">→</span><span><span className="xsmall muted">Now</span><b>{recChange.after}</b></span></div></div>;
    }
    return null;
  }
}

function problemShort(snap: NetworkSnapshot, f: NonNullable<ReturnType<typeof focus>>, ratio: number | null): string {
  const st = snap.stations.find((s) => s.id === f.station_id);
  const where = placeName(snap, f.station_id), fuel = fuelName(f.fuel).toLowerCase();
  if (st?.status !== 'OPEN') return `${where} closed`;
  if (ratio && ratio > 1.05) return `Demand ${ratio.toFixed(1)}× at ${where}`;
  if ((st.inventory[f.fuel] ?? 0) <= 0.5) return `${where} out of ${fuel}`;
  return `${where} ${fuel} running low`;
}

const simClockAt = (snap: NetworkSnapshot, tick: number) => simClock({ ...snap, tick });
const invOf = (snap: NetworkSnapshot, f: NonNullable<ReturnType<typeof focus>>) => {
  const r = f.risk;
  return r?.current_inventory && r.current_inventory > 0 ? r.current_inventory : snap.stations.find((s) => s.id === f.station_id)?.inventory[f.fuel] ?? 0;
};

// ---------------------------------------------------------------- 1 · technical disclosures

function Detected({ snap, rec, ev, live }: { snap: NetworkSnapshot; rec: Recommendation; ev: DemandEvidence | null; live: boolean }) {
  const signals = uniqueSignals(snap, rec);
  return (
    <div className="stack" style={{ gap: 12, marginTop: 10 }}>
      {ev ? (
        <dl className="stat">
          <dt>Current demand (average of the last 4 steps)</dt><dd>{Math.round(ev.current)} L per step</dd>
          <dt>Normal demand (typical range over the previous {ev.baselineTicks} steps)</dt><dd>{Math.round(ev.low)}–{Math.round(ev.high)} L per step</dd>
          <dt>Change vs typical (median {Math.round(ev.median)} L)</dt><dd>{ev.changePct >= 0 ? '+' : ''}{Math.round(ev.changePct * 100)}%</dd>
          <dt>Flagged as unusual</dt><dd>{ev.anomaly ? 'Yes (outside the p10–p90 band by more than 10%)' : 'No'}</dd>
        </dl>
      ) : <p className="small muted">{live ? 'No per-station demand comparison for this situation.' : 'The demand comparison needs the live backend (example data now).'}</p>}
      <div>
        <span className="kicker">Detector signals ({signals.length})</span>
        {signals.length ? <div className="list">{signals.slice(0, 8).map((s, i) => (
          <div key={i} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }}><Chip tone={s.severity === 'crit' ? 'crit' : s.severity === 'warn' ? 'warn' : 'idle'}>{s.kind.replace(/_/g, ' ')}</Chip><span className="small grow mono" style={{ fontSize: 12 }}>{s.text}</span></div>))}</div>
          : <p className="empty">Nothing unusual detected at this step.</p>}
      </div>
      <p className="xsmall muted">Source: the simulator’s demand history (<code>/v1/demand-history</code>). The intelligence service’s detector flags residual z-scores against the forecast and reads live station, route and event status changes.</p>
    </div>
  );
}

function Forecast({ snap, rec, f }: { snap: NetworkSnapshot; rec: Recommendation; f: NonNullable<ReturnType<typeof focus>> }) {
  const r = f.risk!;
  const risks = rec.risks.filter((x) => x.hours_to_stockout != null && x.hours_to_stockout < 99).sort((a, b) => b.p_stockout - a.p_stockout).slice(0, 6);
  return (
    <div className="stack" style={{ gap: 12, marginTop: 10 }}>
      <dl className="stat">
        <dt>Probability of running out within the forecast window</dt><dd>{Math.round(r.p_stockout * 100)}%</dd>
        <dt>Time until shortage</dt><dd>{r.hours_to_stockout != null ? `${r.hours_to_stockout.toFixed(1)} h` : '—'}</dd>
        <dt>In the tank now</dt><dd>{litres(invOf(snap, f))}</dd>
        <dt>Already on the way</dt><dd>{litres(snap.in_transit_totals[f.station_id]?.[f.fuel] ?? 0)}</dd>
        <dt>Backup route</dt><dd>{snap.routes.filter((x) => x.destination_station_id === f.station_id).length > 1 ? 'Yes' : 'No, single route'}</dd>
        <dt>Forecast model</dt><dd className="mono">{rec.versions.forecast_model ?? '—'}</dd>
      </dl>
      <div>
        <span className="kicker">Risk ranking</span>
        <div className="list">{risks.map((x) => (
          <div key={x.station_id + x.fuel_type} className="row between small">
            <span>{placeName(snap, x.station_id)} · {fuelName(x.fuel_type)}</span>
            <span className="mono xsmall">{x.hours_to_stockout?.toFixed(1)} h · P={x.p_stockout.toFixed(2)}</span>
          </div>))}</div>
      </div>
      <p className="xsmall muted">The forecaster predicts demand per station and fuel with p10–p90 bands. The risk engine projects stock step by step (current stock + fuel on the way + scheduled supply − forecast demand) and reports the time to a stockout and its probability.</p>
    </div>
  );
}

// ---------------------------------------------------------------- 2 · recommendation

function Recommend({ snap, rec, legs, f, cur }: { snap: NetworkSnapshot; rec: Recommendation; legs: AllocationLeg[]; f: ReturnType<typeof focus>; cur: CurrentView }) {
  const lead = legs[0];
  const total = legs.reduce((s, l) => s + l.quantity, 0);
  const why = whyBullets(snap, rec, legs, f);
  const eta = arrival(snap, lead);
  const blocked = cur.gate?.blocked_legs ?? [];
  const checks = legs.flatMap((l) => constraintChecks(snap, l, cur.gate));
  const constraints = recConstraints(rec);
  return (
    <div className="rec">
      <div className="rec-main">
        <span className="kicker" style={{ color: 'var(--lime)' }}>2 · FuelGuard recommends</span>
        <span className="headline">{legs.length === 1 ? `Send ${litres(lead.quantity)} ${fuelName(lead.fuel_type).toLowerCase()}` : `Send ${litres(total)} in ${legs.length} shipments`}</span>
        {legs.length === 1
          ? <span className="route">{placeName(snap, lead.source_depot_id)} Depot <i>→</i> {placeName(snap, lead.station_id)}</span>
          : <div className="stack" style={{ gap: 6 }}>{legs.map((l, i) => (
            <div key={i} className="row between"><span>{placeName(snap, l.source_depot_id)} → {placeName(snap, l.station_id)} · {fuelName(l.fuel_type).toLowerCase()}</span><b className="num">{litres(l.quantity)}</b></div>))}</div>}
        {rec.mode === 'containment' && <p className="note warn">Not enough fuel is available to prevent every shortage, so this plan shares the shortfall fairly across stations.</p>}
        {blocked.length > 0 && <p className="note crit">{blocked.length} shipment{blocked.length > 1 ? 's are' : ' is'} blocked by a safety check: {humanize(snap, blocked[0].reason)}.</p>}
        {eta && <span className="eta"><span className="muted">Expected arrival</span> ~{eta}</span>}
      </div>
      <div className="rec-why">
        <span className="kicker">Why this action?</span>
        <ul className="why">{why.map((w, i) => <li key={i}>{w}</li>)}</ul>
        <details className="more dark"><summary>How was this recommendation chosen?</summary>
          <div className="stack" style={{ gap: 12, marginTop: 10 }}>
            <p className="small">Plan: <b>{planName(recPolicy(rec))}</b> <span className="faint">(technical: {techName(recPolicy(rec))})</span>. FuelGuard compared doing nothing, a priority-based plan and an optimized plan, then checked every shipment against the safety rules below.</p>
            <div className="checks">{dedupe(checks).map((c, i) => <div key={i}><i className={c.ok ? 'y' : 'n'}>{c.ok ? '✓' : '✕'}</i><span>{c.label}</span></div>)}</div>
            {constraints.length > 0 && <ul className="xsmall" style={{ margin: 0, paddingLeft: 18, opacity: .8 }}>{constraints.map((c, i) => <li key={i}>{humanize(snap, c)}</li>)}</ul>}
            <div className="explain-box"><span className="kicker">Explanation</span><Explanation decisionId={rec.id} /></div>
          </div>
        </details>
      </div>
    </div>
  );
}

function NoShipment({ snap, rec, im, f }: { snap: NetworkSnapshot; rec: Recommendation; im: Impact | null; f: ReturnType<typeof focus> }) {
  const closed = f && snap.stations.find((s) => s.id === f.station_id)?.status !== 'OPEN';
  const short = im ? im.without.network_unmet_liters : 0;
  const why = closed ? `${placeName(snap, f!.station_id)} is closed, so no fuel can be delivered there until it reopens. ${short > 0.5 ? `An expected shortage of ${litres(short)} remains elsewhere in ${im!.horizon}.` : 'The other stations have enough fuel for now.'}`
    : short > 0.5 ? `An expected shortage of ${litres(short)} remains in ${im!.horizon}, and the selected plan doesn’t send fuel at this step. Compare the other options below to see what each alternative would change.`
    : `Current stock and fuel already on the way are enough to cover expected demand${im ? ` for ${im.horizon}` : ''}.`;
  return (
    <div className="card noship">
      <span className="q">2 · FuelGuard recommends</span>
      <h2 className="lead" style={{ marginTop: 6 }}>No shipment needed right now</h2>
      <p className="lead-sub">{why}</p>
      <p className="xsmall muted" style={{ marginTop: 8 }}>This is a deliberate decision, re-checked every simulation step. It will change as soon as conditions do.</p>
      <details className="more" style={{ marginTop: 10 }}><summary>How was this recommendation chosen?</summary>
        <div className="stack" style={{ gap: 10, marginTop: 10 }}>
          <p className="small muted">Plan: <b>{planName(recPolicy(rec))}</b> (technical: {techName(recPolicy(rec))}). Every option was simulated; the selected one sends nothing at this step.</p>
          <Explanation decisionId={rec.id} />
        </div>
      </details>
    </div>
  );
}

const dedupe = <T extends { label: string }>(xs: T[]) => xs.filter((x, i) => xs.findIndex((y) => y.label === x.label) === i);

// ---------------------------------------------------------------- 3 · impact

function ImpactView({ im, shipping }: { im: Impact; shipping: boolean }) {
  const benefit = !shipping ? `No shipment is recommended, so the expected outcome is the same as doing nothing.`
    : im.avoided > 0.5 ? null
    : im.avoided < -0.5 ? `This plan is expected to leave ${litres(-im.avoided)} more unmet demand than doing nothing.`
    : im.without.network_unmet_liters <= 0.5 ? `No shortage is expected in ${im.horizon} either way; the shipment builds stock ahead of demand.`
    : `The recommendation doesn’t change the expected shortage in ${im.horizon}.`;
  const col = (label: string, cls: string, fut: Impact['with'], affected: number) => (
    <div className={cls}>
      <span className="kicker">{label}</span>
      <div className="impact-rows">
        <div><span>Expected shortage</span><b>{litres(fut.network_unmet_liters)}</b></div>
        <div><span>Stations affected</span><b>{affected}</b></div>
        <div><span>Service level</span><b>{pct(fut.service_level, 0)}</b></div>
      </div>
    </div>
  );
  return (
    <div className="stack" style={{ gap: 14, marginTop: 6 }}>
      <p className="small muted">Projected over {im.horizon} of simulated time for the whole network. A projection, not a guarantee.</p>
      {shipping ? (
        <div className="vs">
          {col('Without this action', 'before', im.without, im.affectedWithout)}
          {col('With recommendation', 'after', im.with, im.affectedWith)}
        </div>
      ) : <div className="vs one">{col('Expected outcome', 'neutral', im.without, im.affectedWithout)}</div>}
      {benefit ? <p className="benefit plain">{benefit}</p>
        : <p className="benefit"><span className="kicker">Expected benefit</span><b>{litres(im.avoided)}</b> of unmet demand avoided.</p>}
    </div>
  );
}

// ---------------------------------------------------------------- 5 · human review

function HumanReview({ rec, cur }: { rec: Recommendation; cur: CurrentView }) {
  const g = cur.gate;
  const conf = g?.confidence ?? cur.autonomy.confidence;
  const min = cur.autonomy.thresholds.autonomous_min;
  const reasons = g?.reasons ?? [];
  const why = !g ? 'The approval gate hasn’t evaluated this recommendation yet.'
    : !g.executable ? (reasons.some((r) => r.includes('stale')) ? 'The simulator data is out of date, so this recommendation can’t be carried out. It is shown for review only.' : 'Every shipment in this plan is blocked by a safety check, so it can’t be carried out.')
    : !g.requires_human ? 'Confidence is high and the shipment is routine, so FuelGuard may send it automatically. You can still reject it.'
    : conf < min ? `Because confidence is below the automatic-action threshold (${Math.round(min * 100)}%), a human must review this recommendation.`
    : g.mode === 'MANUAL' ? 'FuelGuard is in review-everything mode, so every action needs a human.'
    : reasons.some((r) => r.startsWith('containment')) ? 'Plans that share a shortage between stations always need a human.'
    : reasons.some((r) => r.includes('auto limit') || r.includes('routine')) ? 'This shipment is larger than FuelGuard may send on its own, so a human must approve it.'
    : 'A safety rule requires a human to approve this recommendation.';
  return (
    <div className="mc" style={{ marginTop: 8 }}>
      <div className="s5 stack" style={{ gap: 8 }}>
        <span className="small muted">FuelGuard confidence</span>
        <span className="big" style={{ color: conf >= min ? 'var(--ok)' : conf >= cur.autonomy.thresholds.manual_below ? 'var(--warn)' : 'var(--crit)' }}>{Math.round(conf * 100)}%</span>
        <div className="confbar"><i style={{ width: `${conf * 100}%` }} /><b style={{ left: `${min * 100}%` }} title={`Automatic-action threshold ${Math.round(min * 100)}%`} /></div>
        <p className="small">{why}</p>
        {reasons.length > 0 && <details className="more"><summary>Technical details: approval gate</summary>
          <ul className="xsmall muted" style={{ margin: '8px 0 0', paddingLeft: 18 }}>{reasons.map((r, i) => <li key={i} className="mono">{r}</li>)}</ul></details>}
      </div>
      <div className="s7">
        <Review rec={rec} gate={g} stage={cur.record_stage} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- small visuals (from the old Intelligence page)

function DemandSpark({ ev }: { ev: DemandEvidence }) {
  const W = 560, H = 130, p = 6;
  const max = Math.max(ev.high * 1.2, ...ev.series.map((s) => s.v), 1);
  const x = (i: number) => p + (i / Math.max(1, ev.series.length - 1)) * (W - 2 * p);
  const y = (v: number) => H - p - (v / max) * (H - 2 * p);
  const pts = ev.series.map((s, i) => `${x(i)},${y(s.v)}`).join(' ');
  const last = ev.series.length - 4;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Demand per step against the normal range">
      <rect x={p} y={y(ev.high)} width={W - 2 * p} height={Math.max(1, y(ev.low) - y(ev.high))} fill="var(--lime-soft)" />
      <text x={W - p} y={y(ev.high) - 4} textAnchor="end" style={{ font: '600 10px var(--f-body)', fill: 'var(--ok)' }}>normal demand</text>
      <polyline points={pts} fill="none" stroke="var(--ink-3)" strokeWidth={1.6} />
      <polyline points={ev.series.slice(last).map((s, i) => `${x(last + i)},${y(s.v)}`).join(' ')} fill="none" stroke={ev.anomaly ? 'var(--crit)' : 'var(--ink)'} strokeWidth={2.6} />
      <circle cx={x(ev.series.length - 1)} cy={y(ev.series[ev.series.length - 1].v)} r={4} fill={ev.anomaly ? 'var(--crit)' : 'var(--ink)'} />
      <text x={x(ev.series.length - 1) - 8} y={y(ev.series[ev.series.length - 1].v) - 8} textAnchor="end" style={{ font: '600 11px var(--f-body)', fill: ev.anomaly ? 'var(--crit)' : 'var(--ink)' }}>now</text>
    </svg>
  );
}

function InventoryProjection({ inv, hrs, capacity }: { inv: number; hrs: number; capacity: number }) {
  const W = 560, H = 170, pl = 48, pr = 14, pt = 12, pb = 26;
  const span = Math.max(4, Math.ceil(hrs * 1.35));
  const x = (h: number) => pl + (h / span) * (W - pl - pr);
  const y = (v: number) => pt + (1 - v / Math.max(inv, 1)) * (H - pt - pb);
  const crit = Math.min(inv * 0.9, 0.1 * capacity);
  const stepH = Math.max(1, Math.ceil(span / 8));
  const ticks = Array.from({ length: Math.floor(span / stepH) + 1 }, (_, i) => i * stepH);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label={`Stock projected to run out in about ${hrs.toFixed(1)} hours`}>
      <rect x={pl} y={y(crit)} width={W - pl - pr} height={H - pb - y(crit)} fill="var(--crit-bg)" />
      {[0, 0.5, 1].map((g) => <text key={g} x={pl - 6} y={y(inv * g) + 3} textAnchor="end" style={{ font: '500 10px var(--f-mono)', fill: 'var(--ink-3)' }}>{Math.round(inv * g).toLocaleString()}</text>)}
      <line x1={x(0)} y1={y(inv)} x2={x(hrs)} y2={y(0)} stroke="var(--ink)" strokeWidth={2.4} />
      <line x1={x(hrs)} y1={pt} x2={x(hrs)} y2={H - pb} stroke="var(--crit)" strokeDasharray="4 4" />
      <text x={x(hrs)} y={pt + 10} textAnchor={x(hrs) > W - 90 ? 'end' : 'start'} dx={x(hrs) > W - 90 ? -6 : 6} style={{ font: '600 11px var(--f-body)', fill: 'var(--crit)' }}>runs short ≈ {hrs.toFixed(1)} h</text>
      {ticks.map((h) => <text key={h} x={x(h)} y={H - 8} textAnchor="middle" style={{ font: '500 10px var(--f-mono)', fill: 'var(--ink-3)' }}>{h === 0 ? 'now' : `+${h}h`}</text>)}
    </svg>
  );
}
