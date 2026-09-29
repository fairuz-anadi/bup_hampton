import { useEffect, useMemo, useState } from 'react';
import { api, ApiError, operatorKey } from '../api/client';
import { useLive } from '../api/live';
import type { AllocationLeg, DecisionRecord, ExplainResponse, Gate, NetworkSnapshot, Recommendation } from '../api/types';
import { mockExplain } from '../mocks';
import { planName, techName } from '../lib/copy';
import { chosenFuture, fuelName, futureLabel, futureNotes, humanize, litres, pct, placeName, recFutures, recLegs, routeName } from '../lib/format';
import { Chip, RichText, Skeleton } from './ui';

/** Every projected future side by side (technical view), plus unmet litres per station. Always labelled as projections. */
export function Futures({ rec, snap, compact = false }: { rec: Recommendation; snap: NetworkSnapshot | null; compact?: boolean }) {
  const futures = recFutures(rec);
  const chosen = chosenFuture(rec);
  if (!futures.length) return <p className="empty">The Decision Twin did not run for this recommendation.</p>;
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
              <span className="fl">{techName(f.candidate_id)}{isChosen && <span style={{ color: 'var(--ok)' }}> · recommended</span>}</span>
              <span style={{ font: '600 14px var(--f-display)' }}>{planName(f.candidate_id)}</span>
              <span className="fv" style={{ color: f.network_unmet_liters === best ? 'var(--ok)' : f.candidate_id === 'noop' ? 'var(--crit)' : 'var(--ink)' }}>{litres(f.network_unmet_liters)}</span>
              <span className="xsmall muted">projected unmet · horizon {f.horizon_ticks} steps</span>
              <div style={{ height: 6, background: 'var(--sunken)', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ width: `${(f.network_unmet_liters / max) * 100}%`, height: '100%', background: f.candidate_id === 'noop' ? 'var(--crit)' : isChosen ? 'var(--ink)' : 'var(--ink-3)' }} />
              </div>
              {!compact && (
                <span className="xsmall muted">
                  Service {pct(f.service_level, 0)} · {f.first_stockout_tick != null ? `first stockout at step ${f.first_stockout_tick}` : 'no stockout'}
                  {futureNotes(f).length > 0 && <> · <span className="faint">{futureNotes(f).join('; ')}</span></>}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {!compact && stations.length > 0 && (
        <div className="tbl">
          <table>
            <thead><tr><th>Projected unmet by station</th>{futures.map((f) => <th key={f.candidate_id} className="n">{planName(f.candidate_id)}</th>)}</tr></thead>
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

/** Plain list of the options the Twin compared, for "Other options considered". */
export function Alternatives({ rec }: { rec: Recommendation }) {
  const futures = recFutures(rec);
  const chosen = chosenFuture(rec);
  if (!futures.length) return <p className="empty">No alternatives were projected for this recommendation.</p>;
  return (
    <div className="alts">
      {futures.map((f) => {
        const legs = f.legs?.length ? f.legs : rec.candidates?.find((c) => c.id === f.candidate_id)?.legs ?? [];
        const sent = legs.reduce((s, l) => s + l.quantity, 0);
        return (
          <div key={f.candidate_id} className={`alt ${f === chosen ? 'on' : ''}`}>
            <div className="stack" style={{ gap: 2 }}>
              <b>{planName(f.candidate_id)}</b>
              <span className="xsmall faint">Technical: {techName(f.candidate_id)}{futureLabel(f) !== f.candidate_id && futureLabel(f) !== f.name ? ` · ${futureLabel(f)}` : ''}</span>
            </div>
            <span className="small muted">{f.candidate_id === 'noop' ? 'Sends nothing' : sent > 0 ? `Sends ${litres(sent)}` : 'Sends nothing'}</span>
            <span className="small">Expected shortage: <b className="num">{litres(f.network_unmet_liters)}</b></span>
            {f === chosen ? <Chip tone="act">Recommended</Chip> : <span />}
          </div>
        );
      })}
    </div>
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
          <span>{out.source === 'llm' ? `Written by the copilot (${out.llm_model}) and checked against the facts` : 'Written from the decision’s structured facts'}. The copilot explains decisions; it does not make them.</span>
          <details className="facts"><summary>{out.cited_facts.length} cited facts</summary><ul>{out.cited_facts.map((f, i) => <li key={i}>{f}</li>)}</ul></details>
        </div>
      )}
      {decisionId && source !== 'mock' && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (ask.trim()) run(ask.trim()); }}>
          <input className="field grow" value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="Ask about this decision, e.g. why not Tongi?" maxLength={500} />
          <button className="btn" disabled={busy || !ask.trim()}>{busy ? 'Thinking…' : 'Ask'}</button>
        </form>
      )}
    </div>
  );
}

export function GateSummary({ gate }: { gate: Gate }) {
  const tone = !gate.executable ? 'crit' : gate.requires_human ? 'warn' : 'ok';
  const label = !gate.executable ? 'Recommend only' : gate.requires_human ? 'Needs operator approval' : 'May run automatically';
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row"><Chip tone={tone}>{label}</Chip><span className="xsmall faint">confidence {Math.round(gate.confidence * 100)}%</span></div>
      {gate.reasons.length > 0 && <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{gate.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
    </div>
  );
}

/** Approve (as is or with adjusted amounts) or reject. Writes need the operator key; guardrails are re-checked server-side. */
export function Review({ rec, gate, stage, onDone }: { rec: Recommendation; gate: Gate | null; stage: string | null; onDone?: (r: DecisionRecord) => void }) {
  const { snap, source, refresh } = useLive();
  // Keyed on the id: polling replaces the object every 2 s and must not reset an edit in progress.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const base = useMemo(() => recLegs(rec), [rec.id]);
  const [legs, setLegs] = useState<AllocationLeg[]>(base);
  const [editing, setEditing] = useState(false);
  const [key, setKey] = useState(operatorKey.get());
  const [keyOpen, setKeyOpen] = useState(!operatorKey.get());
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null);
  const [msg, setMsg] = useState<{ tone: 'ok' | 'crit' | 'warn'; text: string } | null>(null);
  const [result, setResult] = useState<DecisionRecord | null>(null);
  useEffect(() => { setLegs(base); setEditing(false); setResult(null); setMsg(null); }, [base]);

  const modified = JSON.stringify(legs) !== JSON.stringify(base);
  const blocked = new Map((gate?.blocked_legs ?? []).map((b) => [b.index, b.reason]));
  const decided = result?.stage ?? (stage && stage !== 'gated' ? stage : null);
  const locked = source !== 'live' || !gate?.executable || !!decided;

  const act = async (kind: 'approve' | 'reject') => {
    if (kind === 'reject' && !reason.trim()) { setMsg({ tone: 'warn', text: 'Add a short note on why you are rejecting it; it goes into the audit record.' }); return; }
    operatorKey.set(key);
    setBusy(kind); setMsg(null);
    try {
      const r = kind === 'approve'
        ? await api.approve(rec.id, { by: 'operator', reason: reason.trim(), legs: modified ? legs.filter((l) => l.quantity > 0) : undefined })
        : await api.reject(rec.id, { by: 'operator', reason: reason.trim() });
      setResult(r);
      const acc = r.submissions.filter((s) => s.result === 'accepted').length;
      setMsg(kind === 'approve'
        ? { tone: acc === r.submissions.length ? 'ok' : 'warn', text: acc === r.submissions.length ? `Approved. The simulator accepted ${acc === 1 ? 'the shipment' : `all ${acc} shipments`}.` : `Approved, but only ${acc} of ${r.submissions.length} shipment(s) were accepted by the simulator.` }
        : { tone: 'ok', text: 'Rejected. Nothing was sent, and your note is in the audit record.' });
      onDone?.(r);
      refresh();
    } catch (e) {
      const text = e instanceof ApiError
        ? e.status === 401 ? 'Operator key missing or wrong.' : e.status === 503 && e.code === 'WRITES_DISABLED' ? 'Approvals are switched off on this backend (no operator key configured).' : humanize(snap, e.message)
        : 'Request failed';
      setMsg({ tone: 'crit', text });
    } finally { setBusy(null); }
  };

  if (!base.length) return null;
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="legs">
        {legs.map((l, i) => (
          <div key={i} className={`leg ${blocked.has(i) ? 'blocked' : ''}`}>
            <span className="grow">{routeName(snap, l.route_id)} · {fuelName(l.fuel_type).toLowerCase()}
              {blocked.has(i) && <span className="xsmall" style={{ display: 'block', color: 'var(--crit)' }}>Blocked by a safety check: {humanize(snap, blocked.get(i)!)}</span>}</span>
            {editing && !decided ? (
              <input className="field num-in" type="number" min={0} step={100} value={l.quantity} aria-label={`Litres of ${fuelName(l.fuel_type).toLowerCase()} on ${routeName(snap, l.route_id)}`}
                onChange={(e) => setLegs(legs.map((x, j) => (j === i ? { ...x, quantity: Math.max(0, Number(e.target.value) || 0) } : x)))} />
            ) : <b className="num">{litres(l.quantity)}</b>}
          </div>
        ))}
      </div>
      {decided ? (
        <DecisionOutcome record={result} stage={decided} />
      ) : (
        <>
          <div className="row" style={{ alignItems: 'flex-end' }}>
            <label className="stack grow" style={{ gap: 4, minWidth: 200 }}><span className="kicker">Note for the audit record</span>
              <input className="field" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Optional to approve, required to reject" maxLength={500} disabled={locked} /></label>
            {keyOpen ? (
              <label className="stack" style={{ gap: 4, width: 200 }}><span className="kicker">Operator key</span>
                <input className="field" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Needed to approve" disabled={locked} /></label>
            ) : <button className="link xsmall" onClick={() => setKeyOpen(true)}>Operator key saved · change</button>}
          </div>
          <div className="row">
            <button className="btn primary lg" disabled={locked || !!busy || !key} onClick={() => act('approve')}>
              {busy === 'approve' ? 'Sending…' : modified ? 'Approve adjusted plan' : 'Approve recommendation'}
            </button>
            <button className="btn danger lg" disabled={locked || !!busy || !key} onClick={() => act('reject')}>{busy === 'reject' ? 'Rejecting…' : 'Reject'}</button>
            <button className="btn ghost" disabled={locked} onClick={() => { if (editing && modified) setLegs(base); setEditing(!editing); }}>
              {editing ? (modified ? 'Undo changes' : 'Done adjusting') : 'Adjust amounts'}
            </button>
          </div>
          {source === 'mock' && <p className="xsmall faint">Example data: connect the backend to approve.</p>}
          {source === 'offline' && <p className="xsmall faint">Backend unreachable: approvals are paused.</p>}
          {source === 'live' && gate && !gate.executable && <p className="xsmall" style={{ color: 'var(--crit)' }}>This recommendation can’t be carried out right now (see why below). You can still read it.</p>}
        </>
      )}
      {msg && <p className={`note ${msg.tone}`}>{msg.text}</p>}
    </div>
  );
}

function DecisionOutcome({ record, stage }: { record: DecisionRecord | null; stage: string }) {
  if (!record) return <p className="note">{stage === 'rejected' ? 'An operator rejected this recommendation.' : `This decision was already handled (${stage}).`} <a href="#/history">View decision history</a></p>;
  if (record.stage === 'rejected') return <p className="note">Rejected by {record.approval?.by}: {record.approval?.reason}</p>;
  return (
    <details className="more">
      <summary>Technical details: what was sent to the simulator</summary>
      <div className="tbl" style={{ marginTop: 8 }}>
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
    </details>
  );
}
