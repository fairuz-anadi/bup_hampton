import { useEffect, useRef, useState } from 'react';
import { api, ApiError, operatorKey } from '../api/client';
import { useLive } from '../api/live';
import { control } from '../lib/copy';
import { StateMachine } from './StateMachine';
import { FactorBars, ModeChip } from './ui';

/** "Human review required" / "Human-supervised": who is in control, in one label. Click for the why and the technical factors. */
export function ControlBadge() {
  const { current, source, refresh } = useLive();
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close); document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [open]);

  const c = control(current);
  const a = current?.autonomy;
  const act = async (fn: () => Promise<unknown>) => {
    setMsg(null);
    try { await fn(); refresh(); } catch (e) { setMsg(e instanceof ApiError ? (e.status === 401 ? 'Operator key missing or wrong (enter it in Human review).' : e.message) : 'failed'); }
  };

  return (
    <div className="ctrl" ref={ref}>
      <button className={`ctrl-btn ${c.tone}`} onClick={() => setOpen(!open)} aria-expanded={open} aria-haspopup="dialog">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
        {c.label}
        {c.confidence != null && <span className="faint num">{Math.round(c.confidence * 100)}%</span>}
      </button>
      {open && (
        <div className="ctrl-pop fade-in" role="dialog" aria-label="Human control">
          <span className="kicker">Who is in control</span>
          <h3>{c.label}</h3>
          <p className="small">{c.line}</p>
          {c.confidence != null && <p className="small muted">Decision confidence: <b className="num" style={{ color: 'var(--ink)' }}>{Math.round(c.confidence * 100)}%</b>
            {a && <> · acts on its own only above {Math.round(a.thresholds.autonomous_min * 100)}%</>}</p>}
          <p className="xsmall muted">FuelGuard never moves fuel without passing its safety checks. Approvals always come from a person unless confidence is high and the shipment is routine.</p>
          {a && (
            <details className="more">
              <summary>Technical details: how confidence is calculated</summary>
              <div className="stack" style={{ gap: 12, marginTop: 10 }}>
                <div className="row between"><span className="xsmall muted">Internal mode</span><ModeChip mode={a.mode} /></div>
                <FactorBars factors={a.factors} />
                <StateMachine a={a} />
                <div className="row">
                  <button className="btn sm" disabled={source !== 'live' || !operatorKey.get() || a.mode === 'AUTONOMOUS'} onClick={() => act(api.rearm)} title="Needs confidence ≥ 80%, fresh data and no active disruption">Allow automatic mode</button>
                  <button className="btn sm" disabled={source !== 'live' || !operatorKey.get() || a.mode === 'MANUAL'} onClick={() => act(() => api.setMode('MANUAL'))}>Require review for everything</button>
                </div>
                {msg && <p className="note warn">{msg}</p>}
                {a.log.length > 0 && <div className="log">{a.log.slice(0, 5).map((l, i) => <div key={i}><b>step {l.tick ?? '—'}</b>{l.message.replace('->', '→')}</div>)}</div>}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
