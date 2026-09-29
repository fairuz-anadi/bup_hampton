import type { Autonomy } from '../api/types';

const NODES: { mode: 'AUTONOMOUS' | 'SUPERVISED' | 'MANUAL'; x: number; label: string; sub: string; tone: string }[] = [
  { mode: 'AUTONOMOUS', x: 14, label: 'Autonomous', sub: 'routine actions run', tone: 'var(--ok)' },
  { mode: 'SUPERVISED', x: 222, label: 'Supervised', sub: 'big ones need a human', tone: 'var(--warn)' },
  { mode: 'MANUAL', x: 430, label: 'Manual · safe', sub: 'recommend only', tone: 'var(--crit)' },
];
const W = 160, H = 58, Y = 58;

/** Adaptive autonomy (blueprint section 06): where we are, where the factors point, and how to get back up. */
export function StateMachine({ a }: { a: Autonomy }) {
  const t = a.thresholds;
  const arrow = (x1: number, x2: number, y: number, color: string, label: string[], below = false) => (
    <g>
      <line x1={x1} y1={y} x2={x2} y2={y} stroke={color} strokeWidth={1.5} markerEnd={`url(#ah-${color.replace(/\W/g, '')})`} />
      {label.map((l, i) => <text key={i} x={(x1 + x2) / 2} y={below ? y + 15 + i * 12 : y - 8 - (label.length - 1 - i) * 12} textAnchor="middle"
        style={{ font: '500 9.5px var(--f-mono)', fill: color }}>{l}</text>)}
    </g>
  );
  const colors = ['var(--ink-3)', 'var(--ok)'];
  return (
    <svg viewBox="0 0 604 176" role="img" aria-label={`Autonomy state machine, current mode ${a.mode}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <defs>{colors.map((c) => (
        <marker key={c} id={`ah-${c.replace(/\W/g, '')}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill={c} /></marker>))}</defs>
      {NODES.map((n) => {
        const on = a.mode === n.mode, target = a.target_mode === n.mode && !on;
        return (
          <g key={n.mode} transform={`translate(${n.x},${Y})`}>
            <rect width={W} height={H} rx={10} fill={on ? n.tone : 'var(--surface)'} stroke={on || target ? n.tone : 'var(--line)'}
              strokeWidth={on ? 2 : 1.5} strokeDasharray={target ? '5 4' : undefined} />
            <text x={W / 2} y={25} textAnchor="middle" style={{ font: '700 14px var(--f-display)', fill: on ? '#fff' : 'var(--ink)' }}>{n.label}</text>
            <text x={W / 2} y={42} textAnchor="middle" style={{ font: '500 10px var(--f-mono)', fill: on ? '#fff' : 'var(--ink-3)' }}>{n.sub}</text>
            {n.mode === 'AUTONOMOUS' && !a.armed && <text x={W / 2} y={H + 14} textAnchor="middle" style={{ font: '600 9.5px var(--f-mono)', fill: 'var(--ink-3)' }}>not armed</text>}
          </g>
        );
      })}
      {arrow(176, 220, Y + 20, 'var(--ink-3)', [`conf < ${t.autonomous_min.toFixed(2)}`, 'or crisis'])}
      {arrow(384, 428, Y + 20, 'var(--ink-3)', [`conf < ${t.manual_below.toFixed(2)}`, 'or stale'])}
      {arrow(428, 384, Y + 42, 'var(--ok)', [`${t.healthy_ticks_to_climb} healthy`, 'ticks'], true)}
      {arrow(220, 176, Y + 42, 'var(--ok)', ['operator re-arm', `+ conf ≥ ${t.autonomous_min.toFixed(2)}`], true)}
      <text x={10} y={14} style={{ font: '600 9.5px var(--f-mono)', fill: 'var(--ink-3)', letterSpacing: '.06em' }}>
        DROPS AT ONCE · CLIMBS ONE LEVEL AT A TIME{a.healthy_ticks ? ` · ${a.healthy_ticks}/${t.healthy_ticks_to_climb} HEALTHY` : ''}
      </text>
    </svg>
  );
}
