import { useEffect, useState } from 'react';

/** Hash routes (#/network, #/station/station-mirpur) so the built app works from any static host. */
export const go = (path: string) => { location.hash = path; };

export function useRoute(): string[] {
  const read = () => location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  const [parts, setParts] = useState(read);
  useEffect(() => {
    const on = () => { setParts(read()); window.scrollTo({ top: 0 }); };
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return parts;
}
