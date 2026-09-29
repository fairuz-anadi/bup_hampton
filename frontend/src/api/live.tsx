import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { api, ApiError } from './client';
import type { CurrentView, HealthReport, NetworkSnapshot } from './types';
import { mockCurrent, mockHealth, mockSnapshot } from '../mocks';

/**
 * One polling loop for the whole app (2 s; the backend serves cached state, so this never reaches the simulator).
 *  - live:    backend answered.
 *  - offline: backend stopped answering; keep showing the last snapshot with its age and a banner.
 *  - mock:    never reached a backend (or ?mock=1): fixtures, clearly labelled.
 */
export type Source = 'live' | 'offline' | 'mock';

interface Live {
  source: Source;
  snap: NetworkSnapshot | null;
  current: CurrentView | null;
  currentError: string | null;
  health: HealthReport | null;
  lastOkAt: number | null;
  /** Backend is up but has not synced with the simulator yet. */
  waiting: boolean;
  refresh: () => void;
}

const Ctx = createContext<Live | null>(null);
const POLL_MS = 2000;
const forceMock = () => new URLSearchParams(location.search).has('mock');

export function LiveProvider({ children }: { children: ReactNode }) {
  const [source, setSource] = useState<Source>(forceMock() ? 'mock' : 'live');
  const [snap, setSnap] = useState<NetworkSnapshot | null>(forceMock() ? mockSnapshot : null);
  const [current, setCurrent] = useState<CurrentView | null>(forceMock() ? mockCurrent : null);
  const [currentError, setCurrentError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthReport | null>(forceMock() ? mockHealth : null);
  const [lastOkAt, setLastOkAt] = useState<number | null>(null);
  const [waiting, setWaiting] = useState(false);
  const everLive = useRef(false);
  const busy = useRef(false);
  const n = useRef(0);

  const poll = useCallback(async () => {
    if (forceMock() || busy.current) return;
    busy.current = true;
    const i = n.current++;
    try {
      const s = await api.state();
      everLive.current = true;
      setWaiting(false);
      setSnap(s);
      setSource('live');
      setLastOkAt(Date.now());
      const [c, h] = await Promise.allSettled([api.current(), i % 3 === 0 ? api.health() : Promise.resolve(null)]);
      if (c.status === 'fulfilled') { setCurrent(c.value); setCurrentError(null); }
      else setCurrentError(c.reason instanceof ApiError ? `${c.reason.code}: ${c.reason.message}` : 'failed');
      if (h.status === 'fulfilled' && h.value) setHealth(h.value);
    } catch (e) {
      const unreachable = e instanceof ApiError && (e.status === 0 || e.status >= 500);
      if (e instanceof ApiError && e.code === 'NO_STATE_YET') { setWaiting(true); setSource('live'); everLive.current = true; }
      else if (everLive.current) setSource('offline');
      else if (unreachable) { setSource('mock'); setSnap(mockSnapshot); setCurrent(mockCurrent); setHealth(mockHealth); }
    } finally {
      busy.current = false;
    }
  }, []);

  useEffect(() => {
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => clearInterval(t);
  }, [poll]);

  return (
    <Ctx.Provider value={{ source, snap, current, currentError, health, lastOkAt, waiting, refresh: poll }}>{children}</Ctx.Provider>
  );
}

export function useLive(): Live {
  const v = useContext(Ctx);
  if (!v) throw new Error('useLive outside LiveProvider');
  return v;
}

/** Re-render every `ms` (for "12 s ago" labels). */
export function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}
