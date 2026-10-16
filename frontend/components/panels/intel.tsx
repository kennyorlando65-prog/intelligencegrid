'use client';

/**
 * Core intelligence panels: news, events, signals, threats.
 *
 * Each panel's job is not only to show the data but to show what the data
 * cannot support. The syndication counter in News, the coverage caveat in
 * Events, the KEV/NVD split in ThreatPulse -- these are not addenda, they are
 * the reason a reader can act on the numbers without over-reading them.
 */

import { useState } from 'react';

import { FacetBar, SeverityDonut, TrendArea } from '@/components/charts';
import {
  Caveats,
  ConfidenceMeter,
  DataState,
  LinkOut,
  MetaFooter,
  Panel,
  RecordTable,
  SeverityBadge,
  Stat,
  Tag,
} from '@/components/ui';
import { exportUrl } from '@/lib/api';
import {
  count,
  facetRows,
  list,
  number,
  pick,
  relative,
  timestamp,
  truncate,
} from '@/lib/format';
import { useDebounced, useModule } from '@/lib/hooks';
import type { Signal } from '@/lib/types';

// -- helpers ----------------------------------------------------------------

function ExportLink({ module }: { module: string }) {
  return (
    <a
      href={exportUrl(module as never, 'csv')}
      className="rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
    >
      export csv
    </a>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        active
          ? 'rounded border border-signal/60 bg-signal/15 px-2 py-0.5 text-2xs text-signal'
          : 'rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-muted hover:border-surface-500 hover:text-ink'
      }
    >
      {children}
    </button>
  );
}

// -- 1. News ----------------------------------------------------------------

export function NewsPanel() {
  const [query, setQuery] = useState('');
  const [days, setDays] = useState(7);
  const [includeSyndicated, setIncludeSyndicated] = useState(true);
  const debouncedQuery = useDebounced(query);
  const state = useModule('news', {
    q: debouncedQuery,
    days,
    limit: 60,
    include_syndicated: includeSyndicated,
  });
  const data = state.data;
  const articles = list<Record<string, unknown>>(data, 'articles');
  const corpus = pick<Record<string, number>>(data, 'corpus_stats');

  return (
    <Panel
      title="News Intelligence"
      subtitle="Searchable news from regional and global packs, with syndication tracked separately from independent reporting."
      actions={<ExportLink module="news" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search headlines and summaries…"
            className="w-64 rounded border border-surface-400 bg-surface-700/60 px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink-faint focus:border-signal/60"
          />
          <div className="flex items-center gap-1">
            {[1, 7, 30].map((value) => (
              <FilterButton key={value} active={days === value} onClick={() => setDays(value)}>
                {value === 1 ? '24h' : `${value}d`}
              </FilterButton>
            ))}
          </div>
          <FilterButton active={includeSyndicated} onClick={() => setIncludeSyndicated((v) => !v)}>
            {includeSyndicated ? 'incl. syndicated' : 'independent only'}
          </FilterButton>
          <button
            type="button"
            onClick={state.reload}
            className="rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
          >
            refresh
          </button>
        </div>

        {corpus && (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="corpus" value={count(corpus.syndication_groups)} hint="syndication groups" />
            <Stat
              label="republications"
              value={count(corpus.syndicated_copies)}
              hint="copies of other outlets' stories"
            />
            <Stat label="duplicates" value={count(corpus.duplicates)} hint="exact URL matches" />
            <Stat
              label="undated"
              value={count(corpus.without_published_at)}
              hint="no publication time given upstream"
            />
          </div>
        )}

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta}
          isEmpty={articles.length === 0}
          onRetry={state.reload}
          emptyTitle="No articles match these filters"
        >
          <RecordTable
            columns={[
              {
                key: 'title',
                label: 'Headline',
                render: (row) => (
                  <div className="max-w-xl">
                    <a
                      href={String(row.url ?? '#')}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-xs font-medium text-ink hover:text-signal"
                    >
                      {truncate(String(row.title ?? ''), 110)}
                    </a>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-2xs text-ink-faint">
                      <span>{String(row.source_name ?? 'unknown outlet')}</span>
                      <span>·</span>
                      <span title={timestamp(row.published_at as string)}>
                        {relative(row.published_at as string)}
                      </span>
                      {row.continent ? <span>· {String(row.continent)}</span> : null}
                      {row.is_syndicated ? (
                        <Tag tone="alert">syndicated</Tag>
                      ) : (
                        <Tag tone="good">independent</Tag>
                      )}
                      {row.is_duplicate ? <Tag>duplicate</Tag> : null}
                    </div>
                  </div>
                ),
              },
              {
                key: 'countries',
                label: 'Territories',
                className: 'w-28 font-mono',
                render: (row) => {
                  const codes = Array.isArray(row.countries) ? (row.countries as string[]) : [];
                  return codes.length ? codes.join(', ') : <span className="text-ink-faint">—</span>;
                },
              },
              {
                key: 'sentiment',
                label: 'Tone',
                className: 'w-24',
                render: (row) => {
                  const value = Number(row.sentiment ?? 0);
                  const tone = value > 0.15 ? 'good' : value < -0.15 ? 'alert' : 'neutral';
                  return <Tag tone={tone}>{value.toFixed(2)}</Tag>;
                },
              },
              {
                key: 'confidence',
                label: 'Quality',
                className: 'w-40',
                render: (row) => <ConfidenceMeter value={Number(row.confidence ?? 0)} />,
              },
            ]}
            rows={articles}
          />
          <p className="mt-3 rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
            <span className="font-semibold text-ink">Reading the syndication count.</span> A story
            republished by six outlets is one report, not six. Counting copies as independent
            confirmations is the fastest way to manufacture false confidence, and it biases the map
            toward whichever region&apos;s outlets syndicate most widely.
          </p>
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 2. Events --------------------------------------------------------------

export function EventsPanel() {
  const [days, setDays] = useState(30);
  const state = useModule('events', { days, limit: 200 });
  const data = state.data;
  const events = list<Record<string, unknown>>(data, 'events');
  const clusters = list<Record<string, unknown>>(data, 'clusters');
  const timeline = list<Record<string, unknown>>(data, 'aggregates.timeline').map((row) => ({
    name: String(row.t ?? '').slice(0, 13),
    value: Number(row.count ?? 0),
  }));
  const spikes = list<Record<string, unknown>>(data, 'aggregates.spikes');

  return (
    <Panel
      title="Event Intelligence"
      subtitle="Geocoded events with spatial clustering and spike detection against a shifted baseline."
      actions={<ExportLink module="events" />}
    >
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          {[7, 30, 90].map((value) => (
            <FilterButton key={value} active={days === value} onClick={() => setDays(value)}>
              {value}d
            </FilterButton>
          ))}
          <button
            type="button"
            onClick={state.reload}
            className="rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
          >
            refresh
          </button>
        </div>

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta}
          isEmpty={events.length === 0}
          onRetry={state.reload}
          emptyTitle="No events in this window"
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Event volume" subtitle={`12-hour buckets over ${days} days`} dense>
              <div className="p-3">
                <TrendArea data={timeline} colour="#38bdf8" label="events" />
              </div>
            </Panel>
            <Panel title="Clusters" subtitle="Spatial groups where several events share a location" dense>
              <div className="p-3">
                {clusters.length === 0 ? (
                  <p className="py-8 text-center text-2xs text-ink-faint">
                    No spatial clusters at this threshold.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {clusters.slice(0, 6).map((cluster) => (
                      <li
                        key={String(cluster.cluster_id)}
                        className="flex items-center justify-between rounded border border-surface-400/50 bg-surface-700/40 px-2.5 py-1.5"
                      >
                        <div>
                          <div className="text-xs text-ink">
                            {String(cluster.count)} events within{' '}
                            {number(cluster.radius_km as number, 0)} km
                          </div>
                          <div className="font-mono text-2xs text-ink-faint">
                            {(Array.isArray(cluster.countries)
                              ? (cluster.countries as string[])
                              : []
                            ).join(', ') || 'territory unresolved'}
                          </div>
                        </div>
                        <Tag tone="alert">cluster</Tag>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </Panel>
          </div>

          {spikes.length > 0 && (
            <div className="rounded border border-alert/30 bg-alert/5 px-3 py-2">
              <div className="text-2xs font-semibold uppercase tracking-wider text-alert">
                Detected bursts
              </div>
              <ul className="mt-1 space-y-1">
                {spikes.slice(0, 4).map((spike, index) => (
                  <li key={index} className="flex items-center gap-2 text-2xs text-ink-muted">
                    <SeverityBadge severity={String(spike.severity ?? 'LOW')} />
                    <span className="font-mono">
                      z={number(spike.zscore as number)} · {String(spike.value)} in window
                    </span>
                    <span className="text-ink-faint">
                      vs baseline {number(spike.baseline_mean as number)} ·{' '}
                      {timestamp(spike.timestamp as string)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <RecordTable
            columns={[
              {
                key: 'event_date',
                label: 'Recorded',
                className: 'w-32',
                render: (row) => (
                  <span className="font-mono" title={timestamp(row.event_date as string)}>
                    {relative(row.event_date as string)}
                  </span>
                ),
              },
              {
                key: 'category',
                label: 'Category',
                className: 'w-32',
                render: (row) => <span>{String(row.category ?? 'uncategorised')}</span>,
              },
              {
                key: 'location',
                label: 'Location',
                render: (row) => {
                  const location = row.location as Record<string, unknown> | null;
                  if (!location) {
                    return <span className="text-ink-faint">not geocoded</span>;
                  }
                  return (
                    <span>
                      {String(location.place ?? 'unnamed')}
                      <span className="ml-1.5 font-mono text-ink-faint">
                        {number(location.lat as number, 2)}, {number(location.lon as number, 2)}
                        {location.precision ? ` · ${String(location.precision)}` : ''}
                      </span>
                    </span>
                  );
                },
              },
              {
                key: 'severity',
                label: 'Severity',
                className: 'w-24',
                render: (row) => <SeverityBadge severity={String(row.severity)} />,
              },
              {
                key: 'sources',
                label: 'Sources',
                className: 'w-32 font-mono',
                render: (row) => `${String(row.num_sources ?? 0)} outlet(s)`,
              },
              {
                key: 'link',
                label: 'Evidence',
                className: 'w-20',
                render: (row) => <LinkOut href={row.source_url as string}>open</LinkOut>,
              },
            ]}
            rows={events}
          />
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 3. Signals -------------------------------------------------------------

export function SignalsPanel() {
  const [days, setDays] = useState(7);
  const state = useModule('signals', { days, limit: 80 });
  const data = state.data;
  const signals = list<Signal>(data, 'signals');
  const counts = pick<Record<string, unknown>>(data, 'signal_counts');
  const bySeverity = facetRows(pick(counts, 'by_severity')).filter((row) =>
    ['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].includes(row.name),
  );
  const byContinent = list<Record<string, unknown>>(data, 'sentiment.by_continent');
  const trend = list<Record<string, unknown>>(data, 'sentiment.daily_trend').map((row) => ({
    name: String(row.date ?? ''),
    value: Number(row.count ?? 0),
  }));
  const method = pick<string>(data, 'sentiment.method');

  return (
    <Panel
      title="Signal Desk"
      subtitle="Detected signals, sentiment by territory, and the narrative terms in circulation."
      actions={<ExportLink module="signals" />}
    >
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          {[1, 7, 30].map((value) => (
            <FilterButton key={value} active={days === value} onClick={() => setDays(value)}>
              {value === 1 ? '24h' : `${value}d`}
            </FilterButton>
          ))}
          <button
            type="button"
            onClick={state.reload}
            className="rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
          >
            refresh
          </button>
        </div>

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta}
          isEmpty={signals.length === 0 && trend.length === 0}
          onRetry={state.reload}
          emptyTitle="No signals detected in this window"
        >
          <div className="grid gap-4 lg:grid-cols-3">
            <Panel title="Severity mix" dense>
              <div className="p-3">
                <SeverityDonut data={bySeverity} />
              </div>
            </Panel>
            <Panel title="Article volume" subtitle="Collected per day" dense>
              <div className="p-3">
                <TrendArea data={trend} colour="#38bdf8" label="articles" height={180} />
              </div>
            </Panel>
            <Panel title="Sentiment by territory" subtitle="Weighted lexicon polarity" dense>
              <div className="p-3">
                {byContinent.length === 0 ? (
                  <p className="py-8 text-center text-2xs text-ink-faint">Nothing to compare yet.</p>
                ) : (
                  <ul className="space-y-2">
                    {byContinent.slice(0, 7).map((row) => {
                      const mean = Number(row.mean_sentiment ?? 0);
                      return (
                        <li key={String(row.continent)} className="flex items-center gap-2">
                          <span className="w-20 truncate text-2xs text-ink-muted">
                            {String(row.continent_name ?? row.continent)}
                          </span>
                          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-500">
                            <div
                              className={mean >= 0 ? 'h-full bg-good' : 'h-full bg-alert'}
                              style={{
                                width: `${Math.min(100, Math.abs(mean) * 260)}%`,
                                marginLeft: mean < 0 ? 'auto' : undefined,
                              }}
                            />
                          </div>
                          <span className="w-24 text-right font-mono text-2xs text-ink-faint">
                            {mean.toFixed(2)} · {String(row.articles)} art.
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </Panel>
          </div>

          {method && (
            <p className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
              <span className="font-semibold text-ink">Sentiment method.</span> {method}
            </p>
          )}

          <div className="space-y-2">
            {signals.slice(0, 20).map((signal) => (
              <div
                key={signal.id}
                className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={signal.severity} />
                  <span className="text-xs font-semibold text-ink">{signal.title}</span>
                  <Tag tone="signal">{signal.signal_type.replace(/_/g, ' ')}</Tag>
                  {signal.countries.slice(0, 4).map((iso) => (
                    <Tag key={iso}>{iso}</Tag>
                  ))}
                </div>
                <p className="mt-1.5 text-2xs leading-relaxed text-ink-muted">
                  {truncate(signal.description, 260)}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-3">
                  <ConfidenceMeter value={signal.confidence} />
                  <span className="font-mono text-2xs text-ink-faint">score {signal.score}</span>
                  <span className="text-2xs text-ink-faint">
                    evidence: {signal.evidence.length} reference(s)
                  </span>
                  <span className="text-2xs text-ink-faint">{relative(signal.detected_at)}</span>
                </div>
                {signal.caveats.length > 0 && (
                  <div className="mt-2">
                    <Caveats items={signal.caveats} />
                  </div>
                )}
                {signal.evidence.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {signal.evidence.slice(0, 3).map((ref, index) => (
                      <LinkOut key={index} href={ref.source_url}>
                        {ref.source_name ?? 'open source'}
                      </LinkOut>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 4. ThreatPulse ---------------------------------------------------------

export function ThreatsPanel() {
  const [kevOnly, setKevOnly] = useState(false);
  const [ransomwareOnly, setRansomwareOnly] = useState(false);
  const [days, setDays] = useState(90);
  const state = useModule('threats', {
    kev_only: kevOnly,
    ransomware_only: ransomwareOnly,
    days,
    limit: 120,
  });
  const data = state.data;
  const alerts = list<Record<string, unknown>>(data, 'alerts');
  const vendors = facetRows(pick(data, 'aggregates.by_vendor'), 8);
  const bands = pick<Record<string, number>>(data, 'aggregates.cvss_bands');
  const kevNote = pick<string>(data, 'notes.kev_definition');

  return (
    <Panel
      title="ThreatPulse"
      subtitle="Exploited vulnerabilities and the wider CVE catalogue, kept deliberately distinct."
      actions={<ExportLink module="threats" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <FilterButton active={kevOnly} onClick={() => setKevOnly((v) => !v)}>
            KEV only
          </FilterButton>
          <FilterButton active={ransomwareOnly} onClick={() => setRansomwareOnly((v) => !v)}>
            ransomware-linked
          </FilterButton>
          {[30, 90, 365].map((value) => (
            <FilterButton key={value} active={days === value} onClick={() => setDays(value)}>
              {value === 365 ? '1y' : `${value}d`}
            </FilterButton>
          ))}
          <button
            type="button"
            onClick={state.reload}
            className="rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
          >
            refresh
          </button>
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="catalogue" value={count(pick<number>(data, 'total_cves'))} hint="CVEs observed" />
          <Stat
            label="exploited"
            value={count(pick<number>(data, 'kev_total'))}
            hint="confirmed in the wild (KEV)"
            tone="alert"
          />
          <Stat
            label="ransomware"
            value={count(pick<number>(data, 'ransomware_linked'))}
            hint="linked to ransomware campaigns"
            tone="alert"
          />
          <Stat
            label="critical band"
            value={count(bands?.critical_9_plus)}
            hint="CVSS 9.0 and above"
          />
        </div>

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta}
          isEmpty={alerts.length === 0}
          onRetry={state.reload}
          emptyTitle="No vulnerabilities match these filters"
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Most affected vendors" dense>
              <div className="p-3">
                <FacetBar data={vendors} />
              </div>
            </Panel>
            <Panel title="Severity distribution" dense>
              <div className="p-3">
                <FacetBar
                  data={[
                    { name: 'critical', value: bands?.critical_9_plus ?? 0 },
                    { name: 'high', value: bands?.high_7_to_9 ?? 0 },
                    { name: 'medium', value: bands?.medium_4_to_7 ?? 0 },
                    { name: 'low', value: bands?.low_under_4 ?? 0 },
                    { name: 'unscored', value: bands?.unscored ?? 0 },
                  ]}
                  colour="#ffb020"
                />
              </div>
            </Panel>
          </div>

          <RecordTable
            columns={[
              {
                key: 'cve_id',
                label: 'CVE',
                className: 'w-36',
                render: (row) => (
                  <a
                    href={String(row.source_url ?? '#')}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="font-mono text-signal hover:underline"
                  >
                    {String(row.cve_id)}
                  </a>
                ),
              },
              {
                key: 'title',
                label: 'Description',
                render: (row) => (
                  <div className="max-w-lg">
                    <div className="text-xs text-ink">{truncate(String(row.title ?? ''), 100)}</div>
                    <div className="mt-0.5 font-mono text-2xs text-ink-faint">
                      {String(row.vendor ?? 'vendor unknown')}
                      {row.product ? ` · ${String(row.product)}` : ''}
                    </div>
                  </div>
                ),
              },
              {
                key: 'cvss',
                label: 'CVSS',
                className: 'w-16 font-mono',
                render: (row) =>
                  row.cvss_score === null || row.cvss_score === undefined ? (
                    <span className="text-ink-faint">n/a</span>
                  ) : (
                    String(row.cvss_score)
                  ),
              },
              {
                key: 'severity',
                label: 'Severity',
                className: 'w-24',
                render: (row) => <SeverityBadge severity={String(row.severity)} />,
              },
              {
                key: 'flags',
                label: 'Status',
                className: 'w-40',
                render: (row) => (
                  <div className="flex flex-wrap gap-1">
                    {row.in_kev ? <Tag tone="alert">exploited</Tag> : <Tag>catalogue</Tag>}
                    {row.ransomware_used ? <Tag tone="alert">ransomware</Tag> : null}
                  </div>
                ),
              },
              {
                key: 'due',
                label: 'KEV due',
                className: 'w-28',
                render: (row) =>
                  row.kev_due_date ? (
                    <span className="font-mono">{timestamp(row.kev_due_date as string)}</span>
                  ) : (
                    <span className="text-ink-faint">—</span>
                  ),
              },
            ]}
            rows={alerts}
          />

          {kevNote && (
            <p className="rounded border border-alert/30 bg-alert/5 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
              <span className="font-semibold text-alert">KEV versus catalogue.</span> {kevNote} Most
              catalogue entries have no known exploitation, so absence from KEV is not evidence of
              safety.
            </p>
          )}
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}
