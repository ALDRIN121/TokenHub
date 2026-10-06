import { localDay, usageDateRange, type UsagePeriod } from './dateRange';
import { agents } from './components/usageLabels';

export type DashboardSection = 'overview' | 'models' | 'sessions' | 'sources';
export type AnalysisMode = 'models' | 'sessions';
const periods: UsagePeriod[] = ['all', 'today', 'yesterday', 'last7', 'last30', 'day'];
export function readDashboardLocation() {
  const url = new URL(window.location.href);
  const candidate = url.searchParams.get('agent') ?? 'all';
  const provider = agents.find((agent) => agent.id === candidate || agent.name.toLowerCase() === candidate.toLowerCase())?.id ?? 'all';
  const requestedPeriod = url.searchParams.get('period') ?? (url.searchParams.get('days') === '7' ? 'last7' : url.searchParams.get('days') === '30' ? 'last30' : 'all');
  const period = periods.includes(requestedPeriod as UsagePeriod) ? requestedPeriod as UsagePeriod : 'all';
  const requestedDay = url.searchParams.get('day') ?? '';
  const validDay = Boolean(usageDateRange('day', requestedDay));
  const selectedDay = validDay ? requestedDay : localDay(new Date());
  const hash = url.hash.slice(1);
  const section: DashboardSection = hash === 'local-sources' || hash === 'sources' ? 'sources' : hash === 'models' ? 'models' : hash === 'sessions' || hash === 'analytics' ? 'sessions' : 'overview';
  return { provider, period, selectedDay, section, mode: section === 'models' ? 'models' as const : 'sessions' as const };
}
export function saveDashboardLocation(provider: string, period: UsagePeriod, selectedDay: string, section: DashboardSection) {
  const url = new URL(window.location.href);
  url.searchParams.delete('days');
  url.searchParams.set('agent', provider);
  url.searchParams.set('period', period);
  if (period === 'day') url.searchParams.set('day', selectedDay); else url.searchParams.delete('day');
  url.hash = section;
  if (url.href !== window.location.href) window.history.pushState({}, '', url);
}
let cancelPendingScroll: (() => void) | undefined;
export function scrollDashboard(section: DashboardSection, smooth = true) {
  cancelPendingScroll?.();
  const target = document.getElementById(section === 'sources' ? 'local-sources' : section === 'overview' ? 'overview' : 'analytics');
  if (!target) return;
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const scroll = () => target.scrollIntoView?.({ behavior: smooth && !reduced ? 'smooth' : 'auto', block: 'start' });
  const overview = document.getElementById('overview');
  const settling = () => section !== 'overview' && section !== 'sources' && overview?.querySelector('.usage-trends[aria-busy="true"]');
  if (!settling() || !overview) { scroll(); return; }
  // A new trend can change overview height. Anchor after it settles so an
  // agent change followed by Sessions still lands at the analysis context.
  const observer = new MutationObserver(() => { if (!settling()) finish(); });
  let timer: number;
  const cancel = () => { observer.disconnect(); window.clearTimeout(timer); cancelPendingScroll = undefined; };
  const finish = () => { cancel(); scroll(); };
  observer.observe(overview, { subtree: true, attributes: true, attributeFilter: ['aria-busy'] });
  timer = window.setTimeout(finish, 1500);
  cancelPendingScroll = cancel;
}
