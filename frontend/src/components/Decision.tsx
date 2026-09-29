import { useEffect, useMemo, useState } from 'react';
import { api, ApiError, operatorKey } from '../api/client';
import { useLive } from '../api/live';
import type { AllocationLeg, DecisionRecord, ExplainResponse, Gate, NetworkSnapshot, Recommendation } from '../api/types';
import { mockExplain } from '../mocks';
import { chosenFuture, fuelName, futureLabel, futureNotes, litres, noopFuture, pct, placeName, recFutures, recLegs, routeName } from '../lib/format';
import { Chip, RichText, Skeleton } from './ui';

/** Three projected futures side by side, plus unmet litres per station. Always labelled as projections. */
export function Futures({ rec, snap, compact = false }: { rec: Recommendation; snap: NetworkSnapshot | null; compact?: boolean }) {
  const futures = recFutures(rec);
  const chosen = chosenFuture(rec);
  if (!futures.length) return <p className="empty">The Decision Twin did not run for this recommendation (component on fallback).</p>;
  const best = Math.min(...futures.map((f) => f.network_unmet_liters));
  const max = Math.max(1, ...futures.map((f) => f.network_unmet_liters));
  const stations = [...new Set(futures.flatMap((f) => Object.keys(f.unmet_by_station ?? {})))];
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="futs">
        {futures.map((f) => {
          const isChosen = f === chosen;
          return (
            <div key={f.candidate_id} className={`fut ${isChosen ? 'chosen' : ''}`}>
              <span className="fl">{f.candidate_id === 'noop' ? 'Do nothing' : f.candidate_id}{isChosen && <span style={{ color: 'var(--ok)' }}> · recommended</span>}</span>
              <span style={{ font: '600 14px var(--f-display)' }}>{futureLabel(f)}</span>
              <span className="fv" style={{ color: f.network_unmet_liters === best ? 'var(--ok)' : f.candidate_id === 'noop' ? 'var(--crit)' : 'var(--ink)' }}>{litres(f.network_unmet_liters)}</span>
              <span className="xsmall muted">projected unmet · {f.horizon_ticks} ticks</span>
              <div style={{ height: 6, background: 'var(--sunken)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ width: `${(f.network_unmet_liters / max) * 100}%`, height: '100%', background: f.candidate_id === 'noop' ? 'var(--crit)' : isChosen ? 'var(--ink)' : 'var(--ink-3)' }} />
              </div>
              {!compact && (
                <span className="xsmall muted">
                  Service {pct(f.service_level, 0)} · {f.first_stockout_tick != null ? `first stockout t${f.first_stockout_tick}` : 'no stockout'}
                  {futureNotes(f).length > 0 && <> · <b style={{ color: 'var(--warn)' }}>{futureNotes(f).join('; ')}</b></>}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {!compact && stations.length > 0 && (
        <div className="tbl">
          <table>
            <thead><tr><th>Projected unmet by station</th>{futures.map((f) => <th key={f.candidate_id} className="n">{futureLabel(f)}</th>)}</tr></thead>
            <tbody>
              {stations.map((sid) => (
                <tr key={sid}><td>{placeName(snap, sid)}</td>{futures.map((f) => <td key={f.candidate_id} className="n">{litres(f.unmet_by_station?.[sid] ?? 0)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function ImpactLine({ rec }: { rec: Recommendation }) {
  const noop = noopFuture(rec), chosen = chosenFuture(rec);
  if (!noop || !chosen || noop === chosen) return null;
  const avoided = Math.max(0, noop.network_unmet_liters - chosen.network_unmet_liters);
  return (
    <p className="small">
      <b className="num">{litres(avoided)}</b> projected unmet demand avoided vs the no-action counterfactual
      <span className="faint"> ({litres(noop.network_unmet_liters)} → {litres(chosen.network_unmet_liters)})</span> <span className="tag twin">Projected</span>
    </p>
  );
}

export function Explanation({ decisionId, question }: { decisionId: string | null; question?: string }) {
  const { source } = useLive();
  const [out, setOut] = useState<ExplainResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ask, setAsk] = useState('');
  const [busy, setBusy] = useState(false);

  const run = (q?: string) => {
    if (source === 'mock') { setOut(mockExplain); return; }
    setBusy(true); setErr(null);
    api.explain(decisionId, q).then(setOut).catch((e) => setErr(e instanceof ApiError ? e.message : 'failed')).finally(() => setBusy(false));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setOut(null); run(question); }, [decisionId, source]);

  return (
    <div className="stack">
      {busy && !out && <Skeleton lines={4} />}
      {err && <p className="note warn">Explanation unavailable: {err}</p>}
      {out && <RichText text={out.text} />}
      {out && (
        <div className="row xsmall faint" style={{ justifyContent: 'space-between' }}>
          <span>{out.source === 'llm' ? `Copilot (${out.llm_model}), checked against the facts` : 'Template explanation from structured facts'}</span>
          <details className="facts"><summary>{out.cited_facts.length} cited facts</summary><ul>{out.cited_facts.map((f, i) => <li key={i}>{f}</li>)}</ul></details>
        </div>
      )}
      {decisionId && source !== 'mock' && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (ask.trim()) run(ask.trim()); }}>
          <input className="field grow" value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="Ask the copilot about this decision, e.g. why not Tongi?" maxLength={500} />
          <button className="btn" disabled={busy || !ask.trim()}>{busy ? 'Thinking…' : 'Ask'}</button>
        </form>
      )}
    </div>
  );
}

export function GateSummary({ gate }: { gate: Gate }) {
  const tone = !gate.executable ? 'crit' : gate.requires_human ? 'warn' : 'ok';
  const label = !gate.executable ? 'Recommend only' : gate.requires_human ? 'Needs operator approval' : 'May auto-execute';
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row"><Chip tone={tone}>{label}</Chip><span className="xsmall faint">gate at confidence {gate.confidence.toFixed(2)}, mode {gate.mode.toLowerCase()}</span></div>
      {gate.reasons.length > 0 && <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{gate.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
    </div>
  );
}

/** Approve (as is or modified) or reject. Writes need the operator key; guardrails are re-checked server-side. */
export function Review({ rec, gate, stage, onDone }: { rec: Recommendation; gate: Gate | null; stage: string | null; onDone?: (r: DecisionRecord) => void }) {
  const { snap, source, refresh } = useLive();
  // Keyed on the id: polling replaces the object every 2 s and must not reset an edit in progress.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const base = useMemo(() => recLegs(rec), [rec.id]);
  const [legs, setLegs] = useState<AllocationLeg[]>(base);
  const [editing, setEditing] = useState(false);
  const [key, setKey] = useState(operatorKey.get());
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null);
  const [msg, setMsg] = useState<{ tone: 'ok' | 'crit' | 'warn'; text: string } | null>(null);
  const [result, setResult] = useState<DecisionRecord | null>(null);
  useEffect(() => { setLegs(base); setEditing(false); setResult(null); setMsg(null); }, [base]);

  const modified = JSON.stringify(legs) !== JSON.stringify(base);
  const blocked = new Set((gate?.blocked_legs ?? []).map((b) => b.index));
  const decided = result?.stage ?? (stage && stage !== 'gated' ? stage : null);
  const locked = source !== 'live' || !gate?.executable || !!decided;

  const act = async (kind: 'approve' | 'reject') => {
    if (kind === 'reject' && !reason.trim()) { setMsg({ tone: 'warn', text: 'Say why you are rejecting it; it goes into the audit record.' }); return; }
    operatorKey.set(key);
    setBusy(kind); setMsg(null);
    try {
      const r = kind === 'approve'
        ? await api.approve(rec.id, { by: 'operator', reason: reason.trim(), legs: modified ? legs.filter((l) => l.quantity > 0) : undefined })
        : await api.reject(rec.id, { by: 'operator', reason: reason.trim() });
      setResult(r);
      const acc = r.submissions.filter((s) => s.result === 'accepted').length;
      setMsg(kind === 'approve'
        ? { tone: acc === r.submissions.length ? 'ok' : 'warn', text: `Submitted: ${acc} of ${r.submissions.length} shipment(s) accepted by the simulator.` }
        : { tone: 'ok', text: 'Rejected and recorded.' });
      onDone?.(r);
      refresh();
    } catch (e) {
      const text = e instanceof ApiError
        ? e.status === 401 ? 'Operator key missing or wrong.' : e.status === 503 && e.code === 'WRITES_DISABLED' ? 'Writes are disabled: OPERATOR_KEY is not set on the backend.' : e.message
        : 'Request failed';
      setMsg({ tone: 'crit', text });
    } finally { setBusy(null); }
  };

  if (!base.length) return <p className="empty">Nothing to send this tick.</p>;
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="tbl">
        <table>
          <thead><tr><th>Route</th><th>Fuel</th><th className="n">Litres</th><th /></tr></thead>
          <tbody>
            {legs.map((l, i) => (
              <tr key={i} style={blocked.has(i) ? { background: 'var(--crit-bg)' } : undefined}>
                <td><div>{routeName(snap, l.route_id)}</div><div className="xsmall faint mono">{l.route_id}</div></td>
                <td>{fuelName(l.fuel_type)}</td>
                <td className="n">
                  {editing && !decided ? (
                    <input className="field num-in" type="number" min={0} step={100} value={l.quantity} aria-label={`Litres of ${fuelName(l.fuel_type).toLowerCase()} on ${l.route_id}`}
                      onChange={(e) => setLegs(legs.map((x, j) => (j === i ? { ...x, quantity: Math.max(0, Number(e.target.value) || 0) } : x)))} />
                  ) : litres(l.quantity)}
                </td>
                <td className="n">{blocked.has(i) && <Chip tone="crit">blocked</Chip>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {decided ? (
        <DecisionOutcome record={result} stage={decided} />
      ) : (
        <>
          <div className="g2" style={{ gap: 10 }}>
            <label className="stack" style={{ gap: 4 }}><span className="kicker">Operator key</span>
              <input className="field" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="X-Operator-Key" disabled={locked} /></label>
            <label className="stack" style={{ gap: 4 }}><span className="kicker">Note for the audit record</span>
              <input className="field" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Required to reject" maxLength={500} disabled={locked} /></label>
          </div>
          <div className="row">
            <button className="btn primary" disabled={locked || !!busy || !key} onClick={() => act('approve')}>
              {busy === 'approve' ? 'Sending…' : modified ? 'Approve modified plan' : 'Approve & send'}
            </button>
            <button className="btn" disabled={locked} onClick={() => { if (editing && modified) setLegs(base); setEditing(!editing); }}>
              {editing ? (modified ? 'Undo changes' : 'Done') : 'Modify'}
            </button>
            <button className="btn danger" disabled={locked || !!busy || !key} onClick={() => act('reject')}>{busy === 'reject' ? 'Rejecting…' : 'Reject'}</button>
            {source === 'mock' && <span className="xsmall faint">Mock data: connect the backend to approve.</span>}
            {source === 'offline' && <span className="xsmall faint">Backend unreachable: approvals paused.</span>}
            {source === 'live' && gate && !gate.executable && <span className="xsmall" style={{ color: 'var(--crit)' }}>Locked: this recommendation cannot be executed.</span>}
          </div>
        </>
      )}
      {msg && <p className={`note ${msg.tone}`}>{msg.text}</p>}
    </div>
  );
}

function DecisionOutcome({ record, stage }: { record: DecisionRecord | null; stage: string }) {
  if (!record) return <p className="note">This decision is already <b>{stage}</b>. See History for the full record.</p>;
  if (record.stage === 'rejected') return <p className="note">Rejected by {record.approval?.by}: {record.approval?.reason}</p>;
  return (
    <div className="tbl">
      <table>
        <thead><tr><th>Idempotency key</th><th>Result</th><th>Detail</th></tr></thead>
        <tbody>
          {record.submissions.map((s) => (
            <tr key={s.idempotency_key}>
              <td className="mono xsmall">{s.idempotency_key}</td>
              <td><Chip tone={s.result === 'accepted' ? 'ok' : s.result === 'held' ? 'warn' : 'crit'}>{s.result}</Chip></td>
              <td className="xsmall muted">{s.sim_allocation_id != null ? `allocation #${s.sim_allocation_id} ` : ''}{s.error_code ?? ''} {s.message ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
