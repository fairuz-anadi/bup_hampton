import { useEffect, useState } from 'react';
import type { DecisionRecord } from '../api/types';
import { litres } from '../lib/format';

interface Step { stage: string; reached: boolean; say: string; data: unknown }

function steps(r: DecisionRecord): Step[] {
  const rec = r.recommendation;
  const futures = rec ? (rec.futures?.length ? rec.futures : rec.twin_futures ?? []) : [];
  const out = r.outcome as { actual_network_unmet_l?: number; allocation_statuses?: Record<string, string>; complete?: boolean } | null;
  return [
    { stage: 'Observed', reached: true, say: `Tick ${r.sim_tick}: ${rec?.signals.length ?? 0} signal(s) in the network snapshot.`,
      data: { tick: r.sim_tick, signals: rec?.signals.map((s) => s.message) } },
    { stage: 'Predicted', reached: !!rec, say: `${rec?.risks.length ?? 0} station × fuel pair(s) at risk.`,
      data: rec?.risks.slice(0, 4).map((x) => ({ station: x.station_id, fuel: x.fuel_type, hours: x.hours_to_stockout, p: x.p_stockout })) },
    { stage: 'Twin futures', reached: futures.length > 0, say: futures.map((f) => `${f.label || f.name || f.candidate_id}: ${litres(f.network_unmet_liters)}`).join(' · ') || 'Twin did not run.',
      data: futures.map((f) => ({ candidate: f.candidate_id, unmet_l: f.network_unmet_liters, service: f.service_level })) },
    { stage: 'Gated', reached: !!r.gate, say: r.gate ? `${r.gate.requires_human ? 'Needed a human' : 'Cleared for auto-execution'} at confidence ${r.gate.confidence?.toFixed?.(2) ?? '—'} in ${r.mode?.toLowerCase() ?? '—'} mode.` : 'No gate result.',
      data: r.gate },
    { stage: 'Reviewed', reached: !!r.approval, say: r.approval ? `${r.approval.decision} by ${r.approval.by}${r.approval.modified ? ' (modified)' : ''}${r.approval.reason ? `: ${r.approval.reason}` : ''}.` : 'Waiting for a decision.',
      data: r.approval },
    { stage: 'Submitted', reached: r.submissions.length > 0, say: r.submissions.length ? `${r.submissions.filter((s) => s.result === 'accepted').length} of ${r.submissions.length} leg(s) accepted by the simulator.` : 'Nothing sent.',
      data: r.submissions.map((s) => ({ key: s.idempotency_key, result: s.result, allocation: s.sim_allocation_id, code: s.error_code })) },
    { stage: 'Outcome', reached: !!out, say: out ? (out.complete === false ? 'Horizon ended but history no longer covers it.' : `Actual network unmet over the horizon: ${litres(out.actual_network_unmet_l)}.`) : 'Horizon not over yet.',
      data: out },
    { stage: 'Twin verified', reached: !!r.twin_check, say: r.twin_check ? `Projected ${litres(r.twin_check.predicted_l)} vs actual ${litres(r.twin_check.actual_l)}: error ${litres(r.twin_check.error_l)}, fed back into confidence.` : 'Not verified (still open, modified or rejected).',
      data: r.twin_check },
  ];
}

/** Step through a decision record the way it grew (blueprint section 12: decision audit + replay). */
export function Replay({ record }: { record: DecisionRecord }) {
  const all = steps(record);
  const last = Math.max(0, all.map((s) => s.reached).lastIndexOf(true));
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => { setI(0); setPlaying(false); }, [record.decision_id]);
  useEffect(() => {
    if (!playing) return;
    if (i >= last) { setPlaying(false); return; }
    const t = setTimeout(() => setI((x) => x + 1), 1100);
    return () => clearTimeout(t);
  }, [playing, i, last]);
  const s = all[i];
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 4 }} role="tablist" aria-label="Decision stages">
        {all.map((st, k) => (
          <button key={st.stage} role="tab" aria-selected={k === i} disabled={!st.reached}
            className={`chip plain ${k === i ? 'act' : st.reached ? 'ok' : 'idle'}`} style={{ border: 0, cursor: st.reached ? 'pointer' : 'default' }}
            onClick={() => { setPlaying(false); setI(k); }}>{k + 1}. {st.stage}</button>
        ))}
      </div>
      <p className="small"><b>{s.stage}.</b> {s.say}</p>
      {s.data != null && <pre className="code" style={{ maxHeight: 200, overflow: 'auto' }}>{JSON.stringify(s.data, null, 1)}</pre>}
      <div className="row">
        <button className="btn sm" disabled={i === 0} onClick={() => { setPlaying(false); setI(i - 1); }}>← Back</button>
        <button className="btn sm" disabled={i >= last} onClick={() => { setPlaying(false); setI(i + 1); }}>Next →</button>
        <button className="btn sm primary" disabled={last === 0} onClick={() => { if (i >= last) setI(0); setPlaying(!playing); }}>{playing ? 'Pause' : 'Replay'}</button>
        <span className="xsmall faint">stage {i + 1} of {last + 1} reached</span>
      </div>
    </div>
  );
}
