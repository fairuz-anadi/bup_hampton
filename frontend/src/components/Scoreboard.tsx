import { useEffect, useState } from 'react';
import { useLive } from '../api/live';
import { ops, type Scoreboard as SB } from '../api/ops';
import { litres } from '../lib/format';
import { Card } from './ui';

const MOCK: SB = { executed: 1, projected_avoided_vs_noop_l: 1296, projected_vs_baseline_l: 350, verified: 1, mean_twin_error_l: 40,
  rows: [], note: 'Mock data.' };

/** Counterfactual scoreboard (P2). Projections are labelled as such; only the Twin error compares with reality. */
export function Scoreboard({ compact = false }: { compact?: boolean }) {
  const { source, snap } = useLive();
  const [sb, setSb] = useState<SB | null>(null);
  const tick = snap?.tick;
  useEffect(() => {
    if (source === 'mock') { setSb(MOCK); return; }
    if (source === 'live') ops.scoreboard().then(setSb).catch(() => undefined);
  }, [source, tick]);
  if (!sb) return null;
  return (
    <Card q="Counterfactual scoreboard" title={compact ? undefined : 'What our decisions changed, as projected'} right={<span className="tag twin">Projected</span>}>
      <div className="g3" style={{ gap: 10 }}>
        <div><p className="kicker">Unmet avoided vs no action</p><p className="mid" style={{ color: 'var(--ok)' }}>{litres(sb.projected_avoided_vs_noop_l)}</p>
          <p className="xsmall faint">{sb.executed} executed decision(s)</p></div>
        <div><p className="kicker">vs greedy baseline</p><p className="mid">{sb.projected_vs_baseline_l >= 0 ? '' : '−'}{litres(Math.abs(sb.projected_vs_baseline_l))}</p>
          <p className="xsmall faint">{sb.projected_vs_baseline_l >= 0 ? 'less projected unmet' : 'more projected unmet'}</p></div>
        <div><p className="kicker">Twin error (verified)</p><p className="mid" style={{ color: 'var(--warn)' }}>{sb.mean_twin_error_l == null ? '—' : litres(sb.mean_twin_error_l)}</p>
          <p className="xsmall faint">{sb.verified ? `mean over ${sb.verified} vs the simulator` : 'after the first horizon ends'}</p></div>
      </div>
      {!compact && sb.rows.length > 0 && (
        <div className="tbl" style={{ marginTop: 12 }}><table>
          <thead><tr><th>Tick</th><th>Decision</th><th>Policy</th><th>By</th><th className="n">Avoided vs no action</th><th className="n">Twin error</th></tr></thead>
          <tbody>{sb.rows.map((r) => (
            <tr key={r.decision_id}><td className="mono">t{r.tick}</td><td className="mono xsmall">{r.decision_id}</td><td>{r.policy}</td><td>{r.by ?? '—'}</td>
              <td className="n">{litres(r.avoided_vs_noop_l)}</td><td className="n">{r.twin_check ? litres(r.twin_check.error_l) : '—'}</td></tr>))}</tbody>
        </table></div>
      )}
      <p className="xsmall faint" style={{ marginTop: 10 }}>Projected by the Decision Twin, not outcomes. Only the Twin error compares a projection with what the simulator actually did.</p>
    </Card>
  );
}
