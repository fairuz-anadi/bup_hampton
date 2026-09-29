import { useState } from 'react';
import { ApiError } from '../api/client';
import { useLive } from '../api/live';
import { ops } from '../api/ops';
import type { ExplainResponse } from '../api/types';
import { mockExplain } from '../mocks';
import { Card, Chip, RichText, Skeleton } from '../components/ui';
import { eventName } from '../lib/format';
import { EVENT_PRESETS, playbookFor } from '../lib/playbooks';
import { describeEvent } from './MissionControl';

/** Crisis response (brief §10): what is happening, the playbook for it, and an incident report on demand. */
export function CrisesScreen() {
  const { snap, source } = useLive();
  const [report, setReport] = useState<ExplainResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [from, setFrom] = useState<string>('');
  if (!snap) return <div className="card"><Skeleton lines={6} /></div>;

  const events = [...snap.events].sort((a, b) => (a.status === 'ACTIVE' ? -1 : 1) - (b.status === 'ACTIVE' ? -1 : 1) || b.start_tick - a.start_tick);
  const open = events.filter((e) => e.status !== 'RESOLVED');
  const resolved = events.filter((e) => e.status === 'RESOLVED').slice(0, 8);

  const generate = () => {
    if (source === 'mock') { setReport({ ...mockExplain, text: '**Summary.** Mock data: connect the backend to generate a report from real events and decisions.' }); return; }
    setBusy(true); setErr(null);
    const f = from.trim() === '' ? undefined : Math.max(0, Number(from));
    ops.incidentReport(f, snap.tick).then(setReport).catch((e) => setErr(e instanceof ApiError ? e.message : 'failed')).finally(() => setBusy(false));
  };

  return (
    <div className="mc">
      <div className="s7 stack" style={{ gap: 14 }}>
        {open.length === 0 && <Card q="Crises" title="No active or scheduled events"><p className="small muted">The network is running normally. Crises and faults can be injected from the Chaos Lab.</p></Card>}
        {open.map((e) => {
          const pb = playbookFor(e.type);
          return (
            <Card key={e.id} q={e.status === 'ACTIVE' ? 'Active crisis' : 'Scheduled'} title={eventName(e.type)}
              right={<><span className="mono xsmall faint">t{e.start_tick}–t{e.end_tick}</span><Chip tone={e.status === 'ACTIVE' ? 'crit' : 'warn'}>{e.status.toLowerCase()}</Chip></>}>
              <p className="small muted" style={{ marginBottom: 10 }}>{describeEvent(snap, e.parameters) || 'All entities of this type'}{e.status === 'ACTIVE' ? ` · ${e.end_tick - snap.tick} ticks left` : ` · starts in ${e.start_tick - snap.tick} ticks`}</p>
              {pb ? <div className="reaction">{pb.steps.map(([k, v]) => <div key={k} className="st"><b>{k}</b><span>{v}</span></div>)}</div>
                : <p className="empty">No playbook for this event type.</p>}
            </Card>
          );
        })}
        <Card q="Playbooks" title="Every crisis type">
          <div className="tbl"><table>
            <thead><tr><th>Crisis</th><th>How we respond</th></tr></thead>
            <tbody>{EVENT_PRESETS.map((p) => <tr key={p.id}><td><b>{p.name}</b></td><td className="small muted">{p.steps.find(([k]) => k === 'Respond')?.[1]}</td></tr>)}</tbody>
          </table></div>
        </Card>
      </div>
      <div className="s5 stack" style={{ gap: 14 }}>
        <Card q="Copilot" title="Incident report" right={<span className="tag">read-only</span>}>
          <div className="stack" style={{ gap: 10 }}>
            <form className="row" onSubmit={(ev) => { ev.preventDefault(); generate(); }}>
              <label className="row small" style={{ gap: 6 }}>From tick <input className="field num-in" type="number" min={0} value={from} placeholder={String(Math.max(0, snap.tick - 96))} onChange={(ev) => setFrom(ev.target.value)} /></label>
              <span className="small muted">to now (t{snap.tick})</span>
              <button className="btn primary" disabled={busy}>{busy ? 'Writing…' : 'Generate report'}</button>
            </form>
            {err && <p className="note warn">{err}</p>}
            {busy && !report && <Skeleton lines={5} />}
            {report && <RichText text={report.text} />}
            {report && <p className="xsmall faint">{report.source === 'llm' ? `Copilot (${report.llm_model}), checked against the facts` : 'Template report from events and decision records'}</p>}
          </div>
        </Card>
        <Card q="History" title="Resolved events">
          {resolved.length ? <div className="list">{resolved.map((e) => (
            <div key={e.id} className="row between small"><span>{eventName(e.type)} <span className="xsmall faint">{describeEvent(snap, e.parameters)}</span></span><span className="mono xsmall faint">t{e.start_tick}–t{e.end_tick}</span></div>))}</div>
            : <p className="empty">None yet.</p>}
        </Card>
      </div>
    </div>
  );
}
