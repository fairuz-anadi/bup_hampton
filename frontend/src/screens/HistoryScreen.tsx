import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useLive } from '../api/live';
import type { DecisionRecord } from '../api/types';
import { mockDecisions } from '../mocks';
import { BackHead, Card, Chip, ModeChip, Skeleton, confTone, type Tone } from '../components/ui';
import { Futures, GateSummary } from '../components/Decision';
import { Replay } from '../components/Replay';
import { Scoreboard } from '../components/Scoreboard';
import { fuelName, litres, recLegs, recPolicy, routeName } from '../lib/format';

// Stages from the backend's decision lifecycle: projected/gated -> approved -> submitted -> outcome -> verified, or rejected.
const stageTone = (s: string): Tone =>
  ['approved', 'submitted', 'outcome', 'verified'].includes(s) ? 'ok' : s === 'rejected' ? 'crit' : s === 'gated' || s === 'projected' ? 'warn' : 'idle';
const stageLabel = (s: string) => (s === 'gated' || s === 'projected' ? 'awaiting review' : s);

/** Decision audit history (blueprint section 12): every important recommendation through its lifecycle. */
export function HistoryScreen() {
  const { source, snap } = useLive();
  const [rows, setRows] = useState<DecisionRecord[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const tick = snap?.tick;
  useEffect(() => {
    if (source === 'mock') { setRows(mockDecisions); return; }
    if (source !== 'live') return;
    api.decisions(200).then(setRows).catch(() => setRows((r) => r ?? []));
  }, [source, tick]);

  if (!rows) return <div className="card"><Skeleton lines={6} /></div>;
  const chosen = rows.find((r) => r.decision_id === sel) ?? rows[0];
  const counts = rows.reduce<Record<string, number>>((m, r) => ({ ...m, [r.stage]: (m[r.stage] ?? 0) + 1 }), {});

  return (
    <>
    <BackHead back="#/decisions" label="Decision Center" title="Decision history" sub="Every recommendation through its lifecycle. Pick one and replay it stage by stage." />
    <div className="mc">
      <Card className="s7" q="Audit" title="Decision history" right={<span className="xsmall faint">{Object.entries(counts).map(([k, v]) => `${v} ${stageLabel(k)}`).join(' · ')}</span>}>
        {rows.length === 0 ? <p className="empty">No decisions yet. A record appears when the engine recommends a shipment.</p> : (
          <div className="tbl"><table>
            <thead><tr><th>Step</th><th>Decision</th><th>Plan</th><th className="n">Conf.</th><th>Stage</th></tr></thead>
            <tbody>{rows.map((r) => {
              const legs = r.recommendation ? recLegs(r.recommendation) : [];
              const conf = r.gate?.confidence ?? r.recommendation?.confidence;
              return (
                <tr key={r.decision_id} className={`click ${chosen?.decision_id === r.decision_id ? 'sel' : ''}`} onClick={() => setSel(r.decision_id)}>
                  <td className="mono">t{r.sim_tick}</td>
                  <td className="mono xsmall">{r.decision_id}<div className="faint">{r.mode?.toLowerCase()}</div></td>
                  <td className="small">{legs.length ? `${litres(legs.reduce((s, l) => s + l.quantity, 0))} · ${legs.length} leg(s)` : '—'}</td>
                  <td className="n">{conf != null ? <span style={{ color: `var(--${confTone(conf) === 'ok' ? 'ok' : confTone(conf)})` }}>{conf.toFixed(2)}</span> : '—'}</td>
                  <td><Chip tone={stageTone(r.stage)}>{stageLabel(r.stage)}</Chip></td>
                </tr>);
            })}</tbody>
          </table></div>
        )}
      </Card>
      <div className="s5">{chosen && <RecordDetail r={chosen} />}</div>
      <div className="s12"><Scoreboard /></div>
    </div>
    </>
  );
}

function RecordDetail({ r }: { r: DecisionRecord }) {
  const { snap } = useLive();
  const rec = r.recommendation;
  return (
    <Card q={`Decision · step ${r.sim_tick}`} title={r.decision_id} right={<ModeChip mode={r.mode as never} />}>
      <div className="stack" style={{ gap: 14 }}>
        <Replay record={r} />
        {rec && (
          <div className="list">
            {recLegs(rec).map((l, i) => <div key={i} className="small">{litres(l.quantity)} {fuelName(l.fuel_type).toLowerCase()} · {routeName(snap, l.route_id)}</div>)}
          </div>
        )}
        {r.gate && <GateSummary gate={r.gate} />}
        {r.approval && (
          <p className={`note ${r.approval.decision === 'approved' ? 'ok' : 'crit'}`}>
            <b>{r.approval.decision === 'approved' ? 'Approved' : 'Rejected'}</b> by {r.approval.by}{r.approval.modified ? ' (modified)' : ''}{r.approval.reason ? `: ${r.approval.reason}` : ''}
          </p>
        )}
        {r.submissions.length > 0 && (
          <div className="tbl"><table>
            <thead><tr><th>Key</th><th>Result</th></tr></thead>
            <tbody>{r.submissions.map((s) => <tr key={s.idempotency_key}><td className="mono xsmall">{s.idempotency_key}</td>
              <td><Chip tone={s.result === 'accepted' ? 'ok' : s.result === 'held' ? 'warn' : 'crit'}>{s.result}</Chip> <span className="xsmall faint">{s.error_code ?? ''}</span></td></tr>)}</tbody>
          </table></div>
        )}
        {r.twin_check && (
          <div className="g3" style={{ gap: 8 }}>
            <div><p className="kicker">Twin projected</p><p className="mid">{litres(r.twin_check.predicted_l)}</p></div>
            <div><p className="kicker">Simulator actual</p><p className="mid">{litres(r.twin_check.actual_l)}</p></div>
            <div><p className="kicker">Twin error</p><p className="mid" style={{ color: 'var(--warn)' }}>{litres(r.twin_check.error_l)}</p></div>
          </div>
        )}
        {rec && <Futures rec={rec} snap={snap} compact />}
        <p className="xsmall faint">Versions: policy {rec ? recPolicy(rec) : '—'}{Object.entries(r.versions).filter(([k]) => k !== 'policy').map(([k, v]) => ` · ${k} ${v}`).join('')}</p>
        <details className="facts"><summary>Full audit record (JSON)</summary>
          <pre style={{ fontSize: 11, maxHeight: 320, overflow: 'auto', background: 'var(--sunken)', padding: 10, borderRadius: 6 }}>{JSON.stringify(r, null, 1)}</pre>
        </details>
      </div>
    </Card>
  );
}
