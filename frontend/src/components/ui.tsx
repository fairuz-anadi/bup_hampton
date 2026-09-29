import type { ReactNode } from 'react';
import type { Factor, Fuel, Litres, Mode } from '../api/types';
import { FUELS } from '../api/types';
import { fuelName, litres } from '../lib/format';

export type Tone = 'ok' | 'warn' | 'crit' | 'idle' | 'act';

export const Chip = ({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) => (
  <span className={`chip ${tone}`} title={title}>{children}</span>
);

export const healthTone = (s: string): Tone => (s === 'healthy' ? 'ok' : s === 'degraded' ? 'warn' : s === 'down' ? 'crit' : 'idle');
export const sevTone = (s: string): Tone => (s === 'crit' ? 'crit' : s === 'warn' ? 'warn' : 'idle');
export const modeTone = (m: Mode | string | null | undefined): Tone => (m === 'AUTONOMOUS' ? 'ok' : m === 'SUPERVISED' ? 'warn' : 'crit');
export const confTone = (c: number): Tone => (c >= 0.8 ? 'ok' : c >= 0.6 ? 'warn' : 'crit');
export const toneColor = (t: Tone) => `var(--${t === 'idle' ? 'ink-3' : t})`;

export function ModeChip({ mode }: { mode: Mode | null | undefined }) {
  const label = mode === 'AUTONOMOUS' ? 'Autonomous' : mode === 'SUPERVISED' ? 'Supervised' : mode === 'MANUAL' ? 'Manual · safe' : '—';
  return <Chip tone={modeTone(mode)} title="Autonomy mode">{label}</Chip>;
}

export function Card({ q, title, right, children, className = '' }: { q?: string; title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(q || title || right) && (
        <div className="hd">
          <div className="stack" style={{ gap: 4 }}>
            {q && <span className="q">{q}</span>}
            {title && <h3>{title}</h3>}
          </div>
          {right && <div className="row">{right}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export const fuelColor = (f: Fuel | string) => `var(--${f.toLowerCase()})`;

/** Inventory vs capacity per fuel; hatched extension = fuel in transit to it. */
export function InventoryBars({ inventory, capacity, inTransit }: { inventory: Litres; capacity: Litres; inTransit?: Litres }) {
  return (
    <div className="stack" style={{ gap: 6 }}>
      {FUELS.map((f) => {
        const cap = capacity[f] ?? 0;
        const inv = inventory[f] ?? 0;
        const tr = inTransit?.[f] ?? 0;
        const share = cap ? inv / cap : 0;
        const tone: Tone = share <= 0.001 ? 'crit' : share < 0.2 ? 'crit' : share < 0.4 ? 'warn' : 'ok';
        return (
          <div className="inv" key={f} title={`${fuelName(f)}: ${litres(inv)} of ${litres(cap)}${tr ? `, ${litres(tr)} in transit` : ''}`}>
            <span className="xsmall muted">{fuelName(f)}</span>
            <span className="t">
              <i style={{ width: `${Math.min(100, share * 100)}%`, background: toneColor(tone) }} />
              {tr > 0 && cap > 0 && <i className="transit" style={{ left: `${Math.min(100, share * 100)}%`, width: `${Math.min(100 - share * 100, (tr / cap) * 100)}%` }} />}
            </span>
            <span className="v">{inv <= 0.5 ? <b style={{ color: 'var(--crit)' }}>EMPTY</b> : `${Math.round(share * 100)}%`}</span>
          </div>
        );
      })}
    </div>
  );
}

export function FactorBars({ factors }: { factors: Factor[] }) {
  return (
    <div className="stack" style={{ gap: 7 }}>
      {factors.map((f) => {
        const v = f.value ?? 0;
        const tone: Tone = v < 0.6 ? 'crit' : v < 0.85 ? 'warn' : 'idle';
        return (
          <div className="fbar" key={f.key}>
            <span>{f.label} <span className="faint xsmall">×{f.weight.toFixed(2)}</span></span>
            <span className="t"><i style={{ width: `${v * 100}%`, background: tone === 'idle' ? 'var(--ink-2)' : toneColor(tone) }} /></span>
            <span className="mono xsmall num" style={{ textAlign: 'right' }}>{f.value == null ? '—' : v.toFixed(2)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Renders the copilot's "**Label.** text" paragraphs without a markdown dependency. */
export function RichText({ text }: { text: string }) {
  return (
    <div className="explain">
      {text.split(/\n{2,}/).map((para, i) => (
        <p key={i}>
          {para.split(/(\*\*[^*]+\*\*)/g).map((seg, j) =>
            seg.startsWith('**') && seg.endsWith('**') ? <strong key={j}>{seg.slice(2, -2)}</strong> : <span key={j}>{seg.replace(/^#+\s*/, '')}</span>)}
        </p>
      ))}
    </div>
  );
}

export const Skeleton = ({ lines = 3 }: { lines?: number }) => (
  <div className="stack" style={{ gap: 8 }}>{Array.from({ length: lines }, (_, i) => <div key={i} className="skeleton" style={{ width: `${90 - i * 15}%` }} />)}</div>
);

/** Header for drill-down pages: a way back to the main page they belong to. */
export function BackHead({ back, label, title, sub, right }: { back: string; label: string; title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <a href={back} className="link" style={{ marginBottom: 10 }}>← {label}</a>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {right}
    </div>
  );
}
