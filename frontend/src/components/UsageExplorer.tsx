import { Fragment, useEffect, useMemo, useState } from 'react';
import type { ModelUsage, SessionUsage, UsageBreakdown, UsageTotals } from '../types';
import { formatMetric, MetricCard } from './MetricCard';
import { Icon } from './Icon';
import { usageDateRange, type UsagePeriod } from '../dateRange';
import { getUsagePage, getSessionModels } from '../api/client';
import { UsageComposition } from './UsageComposition';
import { UsageTrends } from './UsageTrends';
import { agents, agentName, modelName } from './usageLabels';

const percent = (value: number | null, total: number | null) => value === null || total === null || total === 0 ? '—' : `${(value / total * 100).toFixed(1)}%`;
const full = (value: number | null) => value === null ? 'Not reported' : value.toLocaleString('en-US');
const date = (value: string | null) => value ? new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value)) : 'Not reported';
type MetricSortKey = 'workload_tokens' | 'input_total_tokens' | 'output_total_tokens' | 'cache_read_tokens' | 'cache_write_tokens' | 'reasoning_tokens' | 'session_count' | 'model_count';
type SortKey = MetricSortKey | 'identity';
type SortDirection = 'ascending' | 'descending';
const tokenSortLabels: [MetricSortKey, string][] = [
  ['workload_tokens', 'Total tokens'], ['input_total_tokens', 'Input tokens'],
  ['output_total_tokens', 'Output tokens'], ['cache_read_tokens', 'Cache read'],
  ['cache_write_tokens', 'Cache write'], ['reasoning_tokens', 'Reasoning'],
];
const compareNames = (left: string, right: string) => left.localeCompare(right, 'en', { numeric: true, sensitivity: 'base' });

function compareCounts(left: number | null, right: number | null, direction: SortDirection) {
  // Unknown values remain last in either direction; zero is an observed count.
  if (left === null) return right === null ? 0 : 1;
  if (right === null) return -1;
  return direction === 'ascending' ? left - right : right - left;
}

function Counters({ row }: { row: UsageTotals }) {
  return <>{(['workload_tokens', 'input_total_tokens', 'output_total_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens'] as const).map((key) => (
    <td key={key} className={key === 'workload_tokens' ? 'usage-number usage-number--total' : 'usage-number'} title={full(row[key])}>{formatMetric(row[key])}</td>
  ))}</>;
}

function SortIndicator({ active, direction }: { active: boolean; direction: SortDirection }) {
  return <Icon name={active ? 'sortAscending' : 'sort'} className={`table-sort-icon${active && direction === 'descending' ? ' table-sort-icon--descending' : ''}`} />;
}

function TokenHeaders({ mode, sort, direction, onSort }: {
  mode: 'models' | 'sessions'; sort: SortKey; direction: SortDirection; onSort: (key: SortKey) => void;
}) {
  const columns: [SortKey, string][] = [
    ['identity', mode === 'models' ? 'Model / agent' : 'Session / models'],
    ['workload_tokens', 'Total tokens'], ['input_total_tokens', 'Input'],
    ['output_total_tokens', 'Output'], ['cache_read_tokens', 'Cache read'],
    ['cache_write_tokens', 'Cache write'], ['reasoning_tokens', 'Reasoning'],
    [mode === 'models' ? 'session_count' : 'model_count', mode === 'models' ? 'Sessions' : 'Models'],
  ];
  return <tr>{columns.map(([key, label]) => {
    const active = sort === key;
    const nextDirection = active ? (direction === 'ascending' ? 'descending' : 'ascending') : (key === 'identity' ? 'ascending' : 'descending');
    return <th key={key} scope="col" aria-sort={active ? direction : undefined}>
      <button type="button" className="table-sort" onClick={() => onSort(key)} aria-label={`Sort by ${label}, ${nextDirection}`} title={`Sort ${nextDirection}`}>
        <span>{label}</span><SortIndicator active={active} direction={direction} />
      </button>
    </th>;
  })}</tr>;
}

export function UsageExplorer({ data, period = 'all', selectedDay = '', onPeriodChange, onSelectedDayChange, accuracyNotice }: {
  data: UsageBreakdown;
  accuracyNotice?: { messages: string[]; warning: boolean };
  period?: UsagePeriod;
  selectedDay?: string;
  onPeriodChange?: (period: UsagePeriod) => void;
  onSelectedDayChange?: (day: string) => void;
}) {
  const [provider, setProvider] = useState('all');
  const [mode, setMode] = useState<'models' | 'sessions'>('models');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<SortKey>('workload_tokens');
  const [direction, setDirection] = useState<SortDirection>('descending');
  const [selected, setSelected] = useState<ModelUsage | null>(null);
  const [selectedSummary, setSelectedSummary] = useState<ModelUsage | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [limit, setLimit] = useState(25);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ items: (ModelUsage | SessionUsage)[]; total: number; mode: 'models' | 'sessions' }>({ items: [], total: 0, mode: 'models' });
  const [pageLoading, setPageLoading] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const remote = data.paging === true;
  const range = usageDateRange(period, selectedDay);
  const options = useMemo(() => ({
    ...usageDateRange(period, selectedDay),
    ...(provider === 'all' ? {} : { provider }), q: query.trim(), sort, direction,
    ...(selected ? { provider: selected.provider, ...(selected.model_name === null ? { unknown_model: 'true' } : { model: selected.model_name }) } : {}),
    offset: String(offset),
  }), [period, selectedDay, provider, query, sort, direction, selected, offset]);
  useEffect(() => { setOffset(0); setExpanded(null); }, [period, selectedDay, provider, query, sort, direction, selected]);
  useEffect(() => {
    if (!remote) return;
    const controller = new AbortController();
    setPageLoading(true); setPageError(null);
    const timer = window.setTimeout(() => {
      getUsagePage(mode, options, controller.signal).then((result) => {
        if (controller.signal.aborted) return;
        if (offset > 0 && offset >= result.total) setOffset(0);
        else setPage({ ...result, mode });
      }).catch(() => { if (!controller.signal.aborted) setPageError('Could not load this page. Your imported data is still available.'); })
        .finally(() => { if (!controller.signal.aborted) setPageLoading(false); });
    }, query.trim() ? 200 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [remote, mode, options, data.data_version, offset, retry]);
  const selectedProvider = selected?.provider, selectedName = selected?.model_name;
  useEffect(() => {
    if (!remote || !selectedProvider || selectedName === undefined) return;
    const controller = new AbortController();
    getUsagePage('models', { ...usageDateRange(period, selectedDay), provider: selectedProvider,
      ...(selectedName === null ? { unknown_model: 'true' } : { model: selectedName }) }, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setSelectedSummary(result.items[0] as ModelUsage ?? null); })
      .catch(() => { if (!controller.signal.aborted) setPageError('Could not update this model summary. Try refreshing data.'); });
    return () => controller.abort();
  }, [remote, selectedProvider, selectedName, period, selectedDay, data.data_version]);
  const summary = provider === 'all' ? data.totals : data.providers.find((row) => row.provider === provider);
  const selectedModel = selected ? remote ? selectedSummary : data.models.find((row) => row.provider === selected.provider && row.model_name === selected.model_name) : null;
  const search = query.trim().toLowerCase();
  const compare = (a: UsageTotals, b: UsageTotals) => sort === 'identity' ? 0 : compareCounts(a[sort], b[sort], direction);
  const models = useMemo(() => remote ? (page.mode === 'models' && mode === 'models' ? page.items : []) as ModelUsage[] : data.models.filter((row) =>
    (provider === 'all' || row.provider === provider) && `${modelName(row.model_name)} ${agentName(row.provider)}`.toLowerCase().includes(search)
  ).sort((a, b) => {
    const names = compareNames(modelName(a.model_name), modelName(b.model_name)) || compareNames(agentName(a.provider), agentName(b.provider));
    if (sort === 'identity') {
      if (a.model_name === null) return b.model_name === null ? names : 1;
      if (b.model_name === null) return -1;
      return direction === 'ascending' ? names : -names;
    }
    return compare(a, b) || names;
  }), [remote, page, mode, data.models, provider, search, sort, direction]);
  const sessions = useMemo(() => remote ? ((page.mode === 'sessions' && mode === 'sessions' ? page.items : []) as SessionUsage[]).map((row) => ({ row, metrics: row.contribution ?? row })) : data.sessions.filter((row) =>
    (provider === 'all' || row.provider === provider) &&
    (!selected || row.provider === selected.provider && row.models.some((model) => model.model_name === selected.model_name)) &&
    `${row.session_key} ${agentName(row.provider)} ${row.models.map((model) => modelName(model.model_name)).join(' ')}`.toLowerCase().includes(search)
  ).map((row) => ({ row, metrics: selected ? row.models.find((model) => model.model_name === selected.model_name)! : row })).sort((a, b) => {
    const names = compareNames(a.row.session_key, b.row.session_key) || compareNames(a.row.provider, b.row.provider);
    if (sort === 'identity') return direction === 'ascending' ? names : -names;
    // The Models column describes the entire session, even when its displayed
    // token counters are filtered to one model's contribution.
    return (sort === 'model_count' ? compare(a.row, b.row) : compare(a.metrics, b.metrics)) || names;
  }), [remote, page, mode, data.sessions, provider, selected, search, sort, direction]);
  const visibleSummary = selected ? selectedModel : summary;
  const selectedAgent = selected?.provider ?? provider;
  const hasHermesUsage = (selectedAgent === 'all' || selectedAgent === 'hermes') &&
    [...data.providers, ...data.models, ...data.sessions].some((row) => row.provider === 'hermes' && row.event_count > 0);
  const largest = models.reduce((value, row) => Math.max(value, row.workload_tokens ?? 0), 0);
  const unknownModels = remote ? Object.entries(data.unknown_model_events ?? {}).filter(([id]) => provider === 'all' || provider === id).reduce((total, [, value]) => total + value, 0) : data.models.filter((row) => row.model_name === null && (provider === 'all' || row.provider === provider)).reduce((count, row) => count + row.event_count, 0);

  const modelGroups = remote ? mode === 'models' && page.mode === 'models' ? page.total
    : provider === 'all' ? data.providers.reduce((total, row) => total + row.model_count + Number((data.unknown_model_events?.[row.provider] ?? 0) > 0), 0)
    : (summary?.model_count ?? 0) + Number((data.unknown_model_events?.[provider] ?? 0) > 0)
    : models.length;

  function changeSort(key: SortKey, toggle = true) {
    setDirection(toggle && sort === key ? (direction === 'ascending' ? 'descending' : 'ascending') : key === 'identity' ? 'ascending' : 'descending');
    setSort(key);
    setLimit(25); setOffset(0);
  }
  function switchMode(next: 'models' | 'sessions') {
    if (next === 'sessions' && sort === 'session_count') setSort('model_count');
    if (next === 'models' && sort === 'model_count') setSort('session_count');
    setMode(next); setLimit(25); setOffset(0); setExpanded(null);
  }
  function chooseAgent(id: string) {
    setProvider(id); setSelected(null); setQuery(''); setExpanded(null); setLimit(25);
  }
  function showModels() { setSelected(null); switchMode('models'); setQuery(''); setExpanded(null); }
  function drillInto(row: ModelUsage) { setSelectedSummary(row); setSelected(row); switchMode('sessions'); setQuery(''); setExpanded(null); }
  const sortLabels: [SortKey, string][] = [
    ['identity', mode === 'models' ? 'Model name' : 'Session'], ...tokenSortLabels,
    [mode === 'models' ? 'session_count' : 'model_count', mode === 'models' ? 'Sessions' : 'Models'],
  ];

  return <section id="usage-explorer" className="usage-explorer panel" aria-labelledby="explorer-heading">
    <div className="explorer-heading"><div><span className="eyebrow">THE TOKEN LEDGER</span><h2 id="explorer-heading">Agent breakdown</h2><p>Choose a date and agent to compare its models and sessions.</p></div><div className="date-filters"><label>Date range<select aria-label="Date range" value={period} onChange={(event) => onPeriodChange?.(event.target.value as UsagePeriod)}><option value="all">All time</option><option value="today">Today</option><option value="yesterday">Yesterday</option><option value="last7">Last 7 days</option><option value="last30">Last 30 days</option><option value="day">Choose a date</option></select></label>{period === 'day' ? <label>Usage date<input aria-label="Usage date" type="date" value={selectedDay} onChange={(event) => onSelectedDayChange?.(event.target.value)} /></label> : null}</div></div>
    <div className="agent-filters" aria-label="Filter by agent">
      <button className="agent-filter agent-filter--all" type="button" aria-pressed={provider === 'all'} onClick={() => chooseAgent('all')} aria-label="Show all agents">
        <span className="agent-filter__name"><Icon name="layers" />All agents</span><strong title={full(data.totals.workload_tokens)}>{formatMetric(data.totals.workload_tokens)}<small>tokens</small></strong><span className="agent-filter__detail">{data.totals.session_count} sessions · {data.totals.model_count} models</span><span className="agent-filter__bar"><span style={{ width: '100%' }} /></span>
      </button>
      {agents.map((agent) => {
        const row = data.providers.find((item) => item.provider === agent.id);
        const share = data.totals.workload_tokens ? (row?.workload_tokens ?? 0) / data.totals.workload_tokens * 100 : 0;
        return <button key={agent.id} type="button" className={`agent-filter agent-color--${agent.id}`} aria-pressed={provider === agent.id} onClick={() => chooseAgent(agent.id)} aria-label={`Filter by ${agent.name}`}>
          <span className="agent-filter__name"><Icon name={agent.icon} />{agent.name}{row && <span className="agent-filter__share">{percent(row.workload_tokens, data.totals.workload_tokens)}</span>}</span>
          <strong title={full(row?.workload_tokens ?? null)}>{formatMetric(row?.workload_tokens)}<small>tokens</small></strong><span className="agent-filter__detail">{row?.session_count ?? 0} sessions · {row?.model_count ?? 0} models</span><span className="agent-filter__bar"><span style={{ width: `${share}%` }} /></span>
        </button>;
      })}
    </div>
    <div className="section-heading"><h3>Token summary</h3><span className="section-meta">Selected date and agent</span></div>
    {accuracyNotice ? <div className={`usage-accuracy notice${accuracyNotice.warning ? ' notice--warning' : ''}`} role="note" aria-label="Usage freshness and completeness"><Icon name="info" /><div>{accuracyNotice.messages.map((message) => <p key={message}>{message}</p>)}</div></div> : null}
    {hasHermesUsage ? <p className="notice notice--warning" role="note" aria-label="Hermes session attribution"><Icon name="hermes" /><span>Hermes assigns the entire session’s usage to its end date, or its start date while active. Daily and weekly totals and the session-reported model lack per-request precision; usage across days and model switches cannot be separated.</span></p> : null}
    <dl className="metric-grid">
      <MetricCard label="Workload tokens" value={visibleSummary?.workload_tokens ?? null} icon="layers" note="Complete input + output records" featured />
      <MetricCard label="Input tokens" value={visibleSummary?.input_total_tokens ?? null} icon="input" note="Includes cached input" />
      <MetricCard label="Output tokens" value={visibleSummary?.output_total_tokens ?? null} icon="output" note="Includes reasoning" />
    </dl>
    <dl className="breakdown-grid">
      <MetricCard label="Cache read tokens" value={visibleSummary?.cache_read_tokens ?? null} icon="cache" note="Within input" />
      <MetricCard label="Cache write tokens" value={visibleSummary?.cache_write_tokens ?? null} icon="cache" note="Reported separately" />
      <MetricCard label="Reasoning tokens" value={visibleSummary?.reasoning_tokens ?? null} icon="reasoning" note="Within output" />
    </dl>
    <div className="overview-details"><UsageComposition dashboard={visibleSummary ?? { ...data.totals, workload_tokens: null, input_total_tokens: null, output_total_tokens: null, event_count: 0 }} /><aside className="privacy-card" id="privacy-note"><span className="privacy-card__icon"><Icon name="shield" /></span><h3>Your data stays yours.</h3><p>TokenHub parses approved local usage sources and retains only usage metadata. Prompts, transcripts, credentials, and provider accounts are never stored or sent anywhere.</p><span><Icon name="check" />No data leaves this machine</span></aside></div>
    {range || data.totals.event_count > 0 ? <UsageTrends range={range} provider={selected?.provider ?? (provider === 'all' ? undefined : provider)}
      model={selected?.model_name ?? undefined} unknownModel={selected?.model_name === null} version={data.data_version} /> : null}
    {data.totals.event_count === 0 ? range ? <div className="explorer-empty"><Icon name="sources" /><h3>No usage recorded for this date range</h3><p>Choose another date range to see models and sessions. The comparison above includes any previous-period usage.</p></div> : <div className="explorer-empty"><Icon name="sources" /><h3>Your usage explorer starts with an import</h3><p>Connect an agent’s local sources to see models and sessions here.</p><a className="button button--primary" href="#local-sources">Connect local sources<Icon name="arrow" /></a></div> : <>
      <div className="ledger-toolbar">
        {selected ? <div className="model-breadcrumb"><button type="button" onClick={showModels}><Icon name="arrow" />All models</button><span>/</span><h3>{modelName(selected.model_name)}</h3><span className={`agent-label agent-color--${selected.provider}`}>{agentName(selected.provider)}</span></div> : <div className="ledger-tabs" aria-label="Usage grouping"><button type="button" aria-pressed={mode === 'models'} onClick={showModels}>Models <span>{modelGroups} groups</span></button><button type="button" aria-pressed={mode === 'sessions'} onClick={() => { switchMode('sessions'); setQuery(''); }}>Sessions <span>{summary?.session_count ?? 0}</span></button></div>}
        <div className="ledger-controls"><label className="usage-search"><Icon name="search" /><input type="search" aria-label="Search usage" placeholder={mode === 'models' ? 'Find a model…' : 'Find a session or model…'} value={query} onChange={(event) => { setQuery(event.target.value); setLimit(25); }} /></label><label className="usage-sort">Sort by<select value={sort} onChange={(event) => changeSort(event.target.value as SortKey, false)}>{sortLabels.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button type="button" className="sort-direction" aria-label={`Sort ${direction === 'ascending' ? 'descending' : 'ascending'}`} title={`Sorted ${direction}; click to reverse`} onClick={() => changeSort(sort)}><SortIndicator active direction={direction} /></button></div>
      </div>
      <div className="ledger-summary"><span><strong>{formatMetric(visibleSummary?.workload_tokens)}</strong> tokens{selected ? ' in this model' : provider === 'all' ? ' across all agents' : ` in ${agentName(provider)}`}</span><span>{visibleSummary?.event_count.toLocaleString('en-US') ?? 0} usage records</span><span>{visibleSummary?.session_count ?? 0} sessions</span>{selectedModel?.attribution === 'session' ? <span className="attribution-note">Session-reported model</span> : null}</div>
      {selected && !selectedModel ? <p className="model-missing-note" role="status">No usage for this model in the current import.</p> : null}
      {pageError ? <p className="notice notice--error" role="alert">{pageError}<button className="button button--secondary" onClick={() => setRetry(retry + 1)}>Retry page</button></p> : null}
      {remote && pageLoading ? <p className="page-loading" role="status">Updating usage page…</p> : null}
      <div className="ledger-table-wrap" aria-busy={remote && pageLoading} tabIndex={0} aria-label="Token breakdown table; scroll to see all columns">
        {mode === 'models' ? <table className="ledger-table" aria-label="Model usage"><thead><TokenHeaders mode="models" sort={sort} direction={direction} onSort={changeSort} /></thead><tbody>
          {models.slice(0, remote ? 25 : limit).map((row, index) => <tr key={JSON.stringify([row.provider, row.model_name])} className={`agent-color--${row.provider}`}>
            <td className="model-cell"><div className="model-cell__top"><span className="model-rank">{String(index + (remote ? offset : 0) + 1).padStart(2, '0')}</span><button type="button" className="model-link" aria-label={`View sessions for ${modelName(row.model_name)} in ${agentName(row.provider)}`} onClick={() => drillInto(row)}>{modelName(row.model_name)}<Icon name="arrow" /></button></div><div className="model-cell__meta"><span className="agent-label"><i />{agentName(row.provider)}</span><span>{percent(row.workload_tokens, summary?.workload_tokens ?? null)} of tokens</span>{row.attribution === 'session' ? <span className="attribution-note">Session-reported</span> : null}</div><div className="model-usage-bar" aria-hidden="true"><span style={{ width: `${largest ? (row.workload_tokens ?? 0) / largest * 100 : 0}%` }} /></div></td>
            <Counters row={row} /><td className="usage-number">{row.session_count}</td>
          </tr>)}
        </tbody></table> : <table className="ledger-table" aria-label="Session usage"><thead><TokenHeaders mode="sessions" sort={sort} direction={direction} onSort={changeSort} /></thead><tbody>
          {sessions.slice(0, remote ? 25 : limit).map(({ row, metrics }) => <SessionRow remote={remote} version={data.data_version} range={usageDateRange(period, selectedDay)} key={`${row.provider}:${row.session_key}`} row={row} metrics={metrics} expanded={expanded === row.session_key} toggle={() => setExpanded(expanded === row.session_key ? null : row.session_key)} selected={selected?.model_name} />)}
        </tbody></table>}
        {(mode === 'models' ? models.length : sessions.length) === 0 ? <div className="ledger-no-results"><h3>{query ? 'No usage matches your search' : 'No imported usage for this agent'}</h3><p>{query ? 'Try another model name or session identifier.' : 'Approve its local sources to start collecting usage.'}</p>{query ? <button type="button" className="button button--secondary" onClick={() => setQuery('')}>Clear search</button> : <a href="#local-sources">View local sources</a>}</div> : null}
      </div>
      {remote ? <div className="ledger-bottom"><span>Showing {page.total ? offset + 1 : 0}–{Math.min(offset + 25, page.total)} of {page.total} {mode}</span><div className="page-controls"><button type="button" className="button button--secondary" disabled={pageLoading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous page</button><button type="button" className="button button--secondary" disabled={pageLoading || offset + 25 >= page.total} onClick={() => { setOffset(offset + 25); setExpanded(null); }}>Next page</button></div></div> : <div className="ledger-bottom"><span>Showing {Math.min(limit, mode === 'models' ? models.length : sessions.length)} of {mode === 'models' ? models.length : sessions.length} {mode}</span>{(mode === 'models' ? models.length : sessions.length) > limit ? <button type="button" className="button button--secondary" onClick={() => setLimit(limit + 25)}>Show 25 more</button> : <span>Hover a count for the exact number.</span>}</div>}
      <div className="ledger-notes"><p><Icon name="info" /><span>Total = input + output. Cache is included in input; reasoning is included in output. Unknown values appear as —.{visibleSummary?.incomplete_event_count ? ` ${visibleSummary.incomplete_event_count} records have incomplete input or output; totals include complete records only.` : ''}</span></p>{(provider === 'all' || provider === 'vscode_copilot') && data.providers.some((row) => row.provider === 'vscode_copilot') ? <p><Icon name="copilot" /><span>Copilot Chat totals include only saved requests with token counters. Inline suggestions and requests without counters are not included.</span></p> : null}{(provider === 'all' || provider === 'antigravity') && data.providers.some((row) => row.provider === 'antigravity') ? <p><Icon name="quality" /><span>Antigravity usage comes from saved conversation counters. Unrecognized generations are excluded rather than estimated.</span></p> : null}{unknownModels > 0 ? <p><Icon name="quality" /><span>{unknownModels.toLocaleString('en-US')} records have no recorded model. Their tokens remain in “Model not recorded”.</span></p> : null}</div>
    </>}
  </section>;
}

function SessionRow({ row, metrics, expanded, toggle, selected, remote = false, range, version }: { remote?: boolean; range?: { from: string; to: string }; version?: number; row: SessionUsage; metrics: UsageTotals; expanded: boolean; toggle: () => void; selected: string | null | undefined }) {
  const [detail, setDetail] = useState<{ items: ModelUsage[]; total: number } | null>(null);
  const [detailOffset, setDetailOffset] = useState(0);
  const [detailError, setDetailError] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const from = range?.from, to = range?.to;
  useEffect(() => { setDetailOffset(0); }, [from, to]);
  useEffect(() => {
    if (!remote || !expanded) return;
    const controller = new AbortController();
    setDetailLoading(true); setDetailError(false);
    getSessionModels(row.session_key, { ...(from && to ? { from, to } : {}), provider: row.provider, offset: String(detailOffset) }, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return;
        if (detailOffset > 0 && detailOffset >= next.total) setDetailOffset(0);
        else setDetail(next);
      })
      .catch(() => { if (!controller.signal.aborted) setDetailError(true); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [remote, expanded, row.session_key, row.provider, from, to, version, detailOffset, retry]);
  const models = [...(remote ? detail?.items ?? [] : row.models)].sort((a, b) => (b.workload_tokens ?? 0) - (a.workload_tokens ?? 0));
  return <Fragment><tr className={`agent-color--${row.provider} session-row`}>
    <td className="model-cell"><button type="button" className="session-link" onClick={toggle} aria-expanded={expanded} aria-label={`Show model breakdown for session ${row.session_key.slice(0, 8)}`}><Icon name="chevron" className={expanded ? 'is-expanded' : ''} /><span className="session-id">{row.session_key.slice(0, 8)}</span><span className="agent-label">{agentName(row.provider)}</span></button><div className="session-models">{selected !== undefined ? modelName(selected) : remote ? `${row.model_count} recorded models` : models.map((model) => modelName(model.model_name)).join(' · ')}</div><span className="session-date">Last usage {date(row.last_seen)}</span></td><Counters row={metrics} /><td className="usage-number">{row.model_count}</td>
  </tr>{expanded ? <tr className="session-detail-row"><td colSpan={8}><div className="session-details"><strong>Models in this session</strong><span className="session-details__note">All recorded models · session total {formatMetric(row.workload_tokens)} tokens</span>{detailLoading ? <span role="status">Loading model breakdown…</span> : null}{detailError ? <span role="alert">Could not load model breakdown.<button onClick={() => setRetry(retry + 1)}>Retry breakdown</button></span> : null}{models.map((model) => <div className="session-model" key={model.model_name ?? '__unknown'}><span>{modelName(model.model_name)}</span><span>{formatMetric(model.workload_tokens)} tokens</span><span>Input {formatMetric(model.input_total_tokens)} · Output {formatMetric(model.output_total_tokens)}</span><span>{percent(model.workload_tokens, row.workload_tokens)}</span></div>)}{remote && (detail?.total ?? 0) > 25 ? <div className="page-controls"><button disabled={detailLoading || detailOffset === 0} onClick={() => setDetailOffset(Math.max(0, detailOffset - 25))}>Previous models</button><button disabled={detailLoading || detailOffset + 25 >= detail!.total} onClick={() => setDetailOffset(detailOffset + 25)}>Next models</button></div> : null}</div></td></tr> : null}</Fragment>;
}
