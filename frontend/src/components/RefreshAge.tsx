import { useEffect, useState } from 'react';

export function RefreshAge({ timestamp }: { timestamp: string | null | undefined }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const tick = () => { if (document.visibilityState !== 'hidden') setNow(Date.now()); };
    const timer = window.setInterval(tick, 5000);
    document.addEventListener('visibilitychange', tick);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', tick); };
  }, []);
  const stamp = timestamp ? Date.parse(timestamp) : NaN;
  const valid = Number.isFinite(stamp) && stamp <= now + 60_000;
  const seconds = Math.max(0, Math.floor((now - stamp) / 1000));
  const unit = seconds < 60 ? 'second' : seconds < 3600 ? 'minute' : seconds < 86400 ? 'hour' : 'day';
  const amount = Math.floor(seconds / (unit === 'second' ? 1 : unit === 'minute' ? 60 : unit === 'hour' ? 3600 : 86400));
  const label = !valid ? 'Refresh not confirmed' : seconds < 10 ? 'Refreshed just now' : `Refreshed ${amount} ${unit}${amount === 1 ? '' : 's'} ago`;
  return <time className="refresh-age" dateTime={valid ? timestamp! : undefined} title={valid ? `Last completed scan: ${new Date(stamp).toLocaleString()}` : 'No completed scan has been reported'}>{label}</time>;
}
