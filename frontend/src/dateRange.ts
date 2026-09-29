/** Calendar-day filters are converted in the browser's local time zone. */
export type UsagePeriod = 'all' | 'today' | 'yesterday' | 'last7' | 'last30' | 'day';

export function localDay(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function usageDateRange(period: UsagePeriod, selectedDay: string, now = new Date()): { from: string; to: string } | undefined {
  if (period === 'all') return undefined;
  let start: Date;
  let end: Date;
  if (period === 'day') {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(selectedDay);
    if (!match) return undefined;
    const [, year, month, day] = match;
    start = new Date(Number(year), Number(month) - 1, Number(day));
    if (localDay(start) !== selectedDay) return undefined;
    end = new Date(Number(year), Number(month) - 1, Number(day) + 1);
  } else {
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const offset = period === 'yesterday' ? -1 : period === 'last7' ? -6 : period === 'last30' ? -29 : 0;
    start = new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset);
    end = new Date(today.getFullYear(), today.getMonth(), today.getDate() + (period === 'yesterday' ? 0 : 1));
  }
  return { from: start.toISOString(), to: end.toISOString() };
}
