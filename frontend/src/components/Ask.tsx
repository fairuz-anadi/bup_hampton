import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { useLive } from '../api/live';
import type { ExplainResponse } from '../api/types';
import { RichText, Skeleton } from './ui';

/** A read-only copilot panel: runs `ask()` on mount and whenever `key` changes, with a free-text follow-up. */
export function Ask({ ask, deps, placeholder }: { ask: (q?: string) => Promise<ExplainResponse>; deps: unknown[]; placeholder: string }) {
  const { source } = useLive();
  const [out, setOut] = useState<ExplainResponse | null>(null);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = (question?: string) => {
    if (source !== 'live') return;
    setBusy(true); setErr(null);
    ask(question).then(setOut).catch((e) => setErr(e instanceof ApiError ? e.message : 'failed')).finally(() => setBusy(false));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setOut(null); run(); }, [source, ...deps]);
  if (source !== 'live') return <p className="empty">The copilot needs the live backend.</p>;
  return (
    <div className="stack">
      {busy && !out && <Skeleton lines={3} />}
      {err && <p className="note warn">Copilot unavailable: {err}</p>}
      {out && <RichText text={out.text} />}
      {out && <p className="xsmall faint">{out.source === 'llm' ? `Copilot (${out.llm_model}), checked against the facts` : 'Template answer from structured facts'}</p>}
      <form className="row" onSubmit={(e) => { e.preventDefault(); if (q.trim()) run(q.trim()); }}>
        <input className="field grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder} maxLength={500} />
        <button className="btn" disabled={busy || !q.trim()}>{busy ? 'Thinking…' : 'Ask'}</button>
      </form>
    </div>
  );
}
