'use client';

/**
 * Map, entity, evidence, backtesting and watchlist panels.
 *
 * The backtesting panel is deliberately structured as an evaluation report
 * rather than a scoreboard. Precision and recall are shown with the matching
 * rules that produced them and with the threshold curve beside them, because
 * a single headline "accuracy" number for an anomaly detector is only ever
 * meaningful alongside the operating point it was measured at.
 */

import dynamic from 'next/dynamic';
import { useCallback, useMemo, useState } from 'react';

import { MultiLine, SeverityDonut } from '@/components/charts';
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
import { createWatchlist, deleteWatchlist, exportUrl, getDigest } from '@/lib/api';
import {
  count,
  list,
  number,
  pick,
  relative,
  timestamp,
  truncate,
} from '@/lib/format';
import { useModule } from '@/lib/hooks';
import type { ResponseMeta } from '@/lib/types';

import type { ClusterShape, CoverageCell, MapPoint } from '@/components/WorldMap';

// Leaflet touches `window` on import, so the map is client-only.
const WorldMap = dynamic(() => import('@/components/WorldMap'), {
  ssr: false,
  loading: () => (
    <div className="flex h-[480px] items-center justify-center rounded border border-surface-400/60 bg-surface-800">
      <span className="text-2xs text-ink-faint">initialising map…</span>
    </div>
  ),
});

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

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      aria-pressed={checked}
      className={
        checked
          ? 'rounded border border-signal/60 bg-signal/15 px-2 py-0.5 text-2xs text-signal'
          : 'rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-faint hover:text-ink'
      }
    >
      {label}
    </button>
  );
}

// -- 10. Global Map ---------------------------------------------------------

const CONTINENT_OPTIONS = [
  { code: '', label: 'world' },
  { code: 'AF', label: 'Africa' },
  { code: 'AS', label: 'Asia' },
  { code: 'EU', label: 'Europe' },
  { code: 'NA', label: 'N. America' },
  { code: 'SA', label: 'S. America' },
  { code: 'OC', label: 'Oceania' },
];

export function MapPanel() {
  const [days, setDays] = useState(30);
  const [continent, setContinent] = useState('');
  const [category, setCategory] = useState('');
  const [showCoverage, setShowCoverage] = useState(true);
  const [showPoints, setShowPoints] = useState(true);
  const [showClusters, setShowClusters] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  const state = useModule('map', { days, continent, category });
  const coverageState = useModule('coverage', { continent });
  const eventsState = useModule('events', { days, continent, category, limit: 200 });

  const data = state.data;
  const points = list<MapPoint>(data, 'points');
  const coverage = list<CoverageCell>(coverageState.data, 'countries');
  const clusters = list<ClusterShape>(eventsState.data, 'clusters');
  const categories = list<string>(data, 'filters.available_categories');

  const selectedCell = useMemo(
    () => coverage.find((cell) => cell.country_iso2 === selected) ?? null,
    [coverage, selected],
  );

  return (
    <Panel
      title="Global Map"
      subtitle="Event points over a coverage-confidence base layer. Shading encodes how well a territory is observed — never how much happened."
      actions={<ExportLink module="map" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1">
            {[7, 30, 90].map((value) => (
              <Toggle
                key={value}
                checked={days === value}
                onChange={() => setDays(value)}
                label={`${value}d`}
              />
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1">
            {CONTINENT_OPTIONS.map((option) => (
              <Toggle
                key={option.code || 'world'}
                checked={continent === option.code}
                onChange={() => setContinent(option.code)}
                label={option.label}
              />
            ))}
          </div>
          <div className="ml-auto flex items-center gap-1">
            <Toggle checked={showCoverage} onChange={setShowCoverage} label="coverage" />
            <Toggle checked={showPoints} onChange={setShowPoints} label="events" />
            <Toggle checked={showClusters} onChange={setShowClusters} label="clusters" />
          </div>
        </div>

        {categories.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-2xs text-ink-faint">category</span>
            <Toggle checked={category === ''} onChange={() => setCategory('')} label="all" />
            {categories.slice(0, 8).map((option) => (
              <Toggle
                key={option}
                checked={category === option}
                onChange={() => setCategory(option)}
                label={option}
              />
            ))}
          </div>
        )}

        <DataState
          loading={state.loading || coverageState.loading}
          error={state.error ?? coverageState.error}
          meta={data?.meta}
          isEmpty={points.length === 0 && coverage.length === 0}
          onRetry={state.reload}
          emptyTitle="Nothing to map with these filters"
        >
          <WorldMap
            points={points}
            coverage={coverage}
            clusters={clusters}
            showCoverage={showCoverage}
            showPoints={showPoints}
            showClusters={showClusters}
            onSelectCountry={setSelected}
          />

          <div className="grid gap-3 lg:grid-cols-4">
            <div className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2.5 lg:col-span-2">
              <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
                Legend
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full bg-[#34d399]" />
                  <span className="text-2xs text-ink-muted">well observed</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full bg-[#38bdf8]" />
                  <span className="text-2xs text-ink-muted">moderate</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full bg-[#164e63]" />
                  <span className="text-2xs text-ink-muted">thin</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full bg-[#334155]" />
                  <span className="text-2xs text-ink-muted">unobserved</span>
                </div>
              </div>
              <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
                {String(pick(data, 'legend_note') ?? '')}
              </p>
            </div>

            <Stat label="mapped events" value={count(points.length)} hint={`last ${days} days`} />
            <Stat
              label="territories"
              value={count(coverage.filter((c) => c.coverage_confidence > 0).length)}
              hint={`of ${count(coverage.length)} in reference`}
            />
          </div>

          {selectedCell && (
            <div className="rounded border border-signal/30 bg-signal/5 px-3 py-2.5">
              <div className="flex items-center justify-between">
                <div className="text-xs font-semibold text-ink">
                  {selectedCell.country_name}
                  <span className="ml-2 font-mono text-2xs text-ink-faint">
                    {selectedCell.country_iso2}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  className="text-2xs text-ink-faint hover:text-ink"
                >
                  clear
                </button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-4">
                <ConfidenceMeter value={selectedCell.coverage_confidence} />
                <span className="font-mono text-2xs text-ink-muted">
                  {count(selectedCell.events)} events · {count(selectedCell.news)} articles
                </span>
                <Tag tone={selectedCell.data_state === 'no_data' ? 'alert' : 'neutral'}>
                  {selectedCell.data_state.replace(/_/g, ' ')}
                </Tag>
              </div>
            </div>
          )}
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 11. Entity Explorer ----------------------------------------------------

export function EntitiesPanel() {
  const [kind, setKind] = useState('');
  const [query, setQuery] = useState('');
  const [nodeId, setNodeId] = useState<string | null>(null);

  const listState = useModule('entities', { kind, q: query, limit: 80 });
  const detailState = useModule('entities', nodeId ? { node_id: nodeId, depth: 1 } : {});

  const data = listState.data;
  const nodes = list<Record<string, unknown>>(data, 'nodes');
  const kinds = pick<Record<string, number>>(data, 'stats.nodes_by_kind');
  const guide = pick<Record<string, string>>(data, 'node_type_guide');
  const communities = list<Record<string, unknown>>(data, 'communities');

  const detail = nodeId ? detailState.data : null;
  const node = pick<Record<string, unknown>>(detail, 'node');
  const neighbours = list<Record<string, unknown>>(detail, 'neighbours');
  const edges = list<Record<string, unknown>>(detail, 'edges');

  const active = node ?? nodes.find((n) => n.id === nodeId);

  return (
    <Panel
      title="Entity Explorer"
      subtitle="People, organisations, territories, vessels, vulnerabilities and stories — and the links between them."
      actions={<ExportLink module="entities" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter entities…"
            className="w-56 rounded border border-surface-400 bg-surface-700/60 px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink-faint focus:border-signal/60"
          />
          <Toggle checked={kind === ''} onChange={() => setKind('')} label="all kinds" />
          {kinds &&
            Object.keys(kinds).map((option) => (
              <Toggle
                key={option}
                checked={kind === option}
                onChange={() => setKind(option)}
                label={`${option} (${kinds[option]})`}
              />
            ))}
          <button
            type="button"
            onClick={listState.reload}
            className="ml-auto rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
          >
            refresh
          </button>
        </div>

        <DataState
          loading={listState.loading}
          error={listState.error}
          meta={data?.meta}
          isEmpty={nodes.length === 0}
          onRetry={listState.reload}
          emptyTitle="No entities match this filter"
        >
          <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
            <RecordTable
              columns={[
                {
                  key: 'label',
                  label: 'Entity',
                  render: (row) => (
                    <button
                      type="button"
                      onClick={() => setNodeId(String(row.id))}
                      className="text-left text-xs text-ink hover:text-signal"
                    >
                      {String(row.label)}
                    </button>
                  ),
                },
                {
                  key: 'kind',
                  label: 'Kind',
                  className: 'w-24',
                  render: (row) => <Tag>{String(row.kind)}</Tag>,
                },
                {
                  key: 'degree',
                  label: 'Links',
                  className: 'w-16 font-mono',
                  render: (row) => String(row.degree ?? 0),
                },
                {
                  key: 'weight',
                  label: 'Strength',
                  className: 'w-32',
                  render: (row) => (
                    <ConfidenceMeter value={Math.min(1, Number(row.weight ?? 0) / 20)} showBand={false} />
                  ),
                },
              ]}
              rows={nodes}
            />

            <div className="space-y-3">
              {active ? (
                <Panel title={String(active.label ?? 'Entity')} subtitle={String(active.kind ?? '')} dense>
                  <div className="space-y-3 p-3">
                    <div className="grid grid-cols-2 gap-2">
                      <Stat label="connections" value={String(active.degree ?? '—')} />
                      <Stat label="weight" value={number(active.weight as number)} />
                    </div>
                    {edges.length > 0 && (
                      <div>
                        <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
                          Strongest links
                        </div>
                        <ul className="mt-1.5 space-y-1">
                          {edges.slice(0, 8).map((edge, index) => (
                            <li
                              key={index}
                              className="flex items-center justify-between rounded border border-surface-400/40 bg-surface-700/40 px-2 py-1"
                            >
                              <span className="truncate text-2xs text-ink-muted">
                                <span className="text-ink">{String(edge.relation)}</span>{' '}
                                {String(edge.source === active.id ? edge.target_label : edge.source_label ?? '')}
                              </span>
                              <span className="shrink-0 font-mono text-2xs text-ink-faint">
                                ×{String(edge.occurrences ?? 1)}
                                {Number(edge.distinct_sources ?? 0) > 1
                                  ? ` · ${String(edge.distinct_sources)} src`
                                  : ''}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {neighbours.length > 0 && (
                      <div>
                        <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
                          Neighbours
                        </div>
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {neighbours.slice(0, 20).map((neighbour, index) => (
                            <button
                              key={index}
                              type="button"
                              onClick={() => setNodeId(String(neighbour.id))}
                              className="rounded border border-surface-400 px-1.5 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
                            >
                              {String(neighbour.label)}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </Panel>
              ) : (
                <Panel title="Link strength" subtitle="Why diversity beats repetition" dense>
                  <div className="space-y-3 p-3 text-2xs leading-relaxed text-ink-muted">
                    <p>
                      Select an entity to inspect its neighbourhood. Edge strength is derived from
                      how many times a link appears <span className="text-ink">and how many
                      distinct outlets reported it</span>.
                    </p>
                    <p>
                      Twelve outlets mentioning a link once each is materially stronger evidence
                      than a single outlet repeating it twelve times. Averaging those two together
                      is how a lone outlet manufactures an apparent consensus.
                    </p>
                  </div>
                </Panel>
              )}

              {communities.length > 0 && (
                <Panel title="Dense clusters" subtitle="Groups of tightly linked entities" dense>
                  <div className="space-y-2 p-3">
                    {communities.slice(0, 4).map((community, index) => (
                      <div key={index}>
                        <div className="text-2xs text-ink-faint">
                          {String(community.size)} linked entities
                        </div>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {list<Record<string, unknown>>(community, 'members')
                            .slice(0, 8)
                            .map((member, memberIndex) => (
                              <button
                                key={memberIndex}
                                type="button"
                                onClick={() => setNodeId(String(member.id))}
                                className="rounded border border-surface-400 px-1.5 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
                              >
                                {String(member.label)}
                              </button>
                            ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </Panel>
              )}
            </div>
          </div>

          {guide && (
            <div className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2">
              <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
                Node kinds
              </div>
              <dl className="mt-1.5 grid gap-1 sm:grid-cols-2">
                {Object.entries(guide).map(([key, description]) => (
                  <div key={key} className="flex gap-2 text-2xs">
                    <dt className="shrink-0 font-mono text-signal">{key}</dt>
                    <dd className="text-ink-muted">{description}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 12. Evidence Explorer --------------------------------------------------

export function EvidencePanel() {
  const [signalId, setSignalId] = useState<string | null>(null);
  const listState = useModule('evidence', {});
  const detailState = useModule('evidence', signalId ? { signal_id: signalId } : {});

  const data = listState.data;
  const signals = list<Record<string, unknown>>(data, 'signals');
  const provenance = pick<Record<string, unknown>>(data, 'provenance');
  const chain = pick<Record<string, string>>(data, 'chain_of_custody');

  const detail = signalId ? detailState.data : null;
  const reasoning = pick<Record<string, unknown>>(detail, 'reasoning');
  const found = pick<boolean>(detail, 'found');

  return (
    <Panel
      title="Evidence Explorer"
      subtitle="Every signal's sources, timestamps and the reasoning chain that produced it."
      actions={<ExportLink module="evidence" />}
    >
      <div className="space-y-4">
        {chain && (
          <div className="grid gap-2 sm:grid-cols-3">
            {(['bronze', 'silver', 'gold'] as const).map((layer) => (
              <div
                key={layer}
                className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2.5"
              >
                <div className="text-2xs font-semibold uppercase tracking-wider text-signal">
                  {layer}
                </div>
                <p className="mt-1 text-2xs leading-relaxed text-ink-muted">{chain[layer]}</p>
              </div>
            ))}
          </div>
        )}

        <DataState
          loading={listState.loading}
          error={listState.error}
          meta={data?.meta}
          isEmpty={signals.length === 0}
          onRetry={listState.reload}
          emptyTitle="No signals to trace yet"
        >
          <div className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
            <div className="space-y-1.5">
              {signals.slice(0, 25).map((signal) => (
                <button
                  key={String(signal.id)}
                  type="button"
                  onClick={() => setSignalId(String(signal.id))}
                  className={
                    signalId === signal.id
                      ? 'w-full rounded border border-signal/60 bg-signal/10 px-2.5 py-2 text-left'
                      : 'w-full rounded border border-surface-400/50 bg-surface-700/30 px-2.5 py-2 text-left hover:border-surface-500'
                  }
                >
                  <div className="flex items-center gap-2">
                    <SeverityBadge severity={String(signal.severity)} />
                    <span className="truncate text-2xs text-ink">{truncate(String(signal.title), 60)}</span>
                  </div>
                  <div className="mt-1 flex items-center gap-3 font-mono text-2xs text-ink-faint">
                    <span>conf {number(Number(signal.confidence))}</span>
                    <span>
                      {(Array.isArray(signal.evidence) ? signal.evidence.length : 0)} refs
                    </span>
                    <span>{relative(signal.detected_at as string)}</span>
                  </div>
                </button>
              ))}
            </div>

            <div className="space-y-3">
              {detail && found ? (
                <>
                  <Panel
                    title={String(pick(detail, 'signal.title') ?? 'Signal')}
                    subtitle={String(pick(detail, 'signal.signal_type') ?? '')}
                    dense
                  >
                    <div className="space-y-3 p-3">
                      {Array.isArray(reasoning?.steps) && (
                        <ol className="space-y-2">
                          {(reasoning.steps as Record<string, unknown>[]).map((step, index) => (
                            <li key={index} className="flex gap-2.5">
                              <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-signal/50 font-mono text-2xs text-signal">
                                {String(step.step)}
                              </span>
                              <div>
                                <div className="text-2xs font-semibold uppercase tracking-wider text-ink">
                                  {String(step.action)}
                                </div>
                                <p className="text-2xs leading-relaxed text-ink-muted">
                                  {String(step.detail)}
                                </p>
                              </div>
                            </li>
                          ))}
                        </ol>
                      )}
                      <Caveats items={reasoning?.caveats} />
                    </div>
                  </Panel>

                  {Array.isArray(pick(detail, 'signal.evidence')) &&
                    (pick(detail, 'signal.evidence') as Record<string, unknown>[]).length > 0 && (
                      <Panel title="Source records" subtitle="The original reports behind this signal" dense>
                        <ul className="divide-y divide-surface-400/30">
                          {(pick(detail, 'signal.evidence') as Record<string, unknown>[]).map(
                            (ref, index) => (
                              <li key={index} className="px-3 py-2">
                                <div className="flex items-center justify-between gap-2">
                                  <span className="text-2xs text-ink">
                                    {String(ref.source_name ?? 'unnamed source')}
                                  </span>
                                  <span className="shrink-0 font-mono text-2xs text-ink-faint">
                                    {timestamp(ref.published_at as string)}
                                  </span>
                                </div>
                                {ref.excerpt ? (
                                  <p className="mt-1 text-2xs leading-relaxed text-ink-muted">
                                    {truncate(String(ref.excerpt), 200)}
                                  </p>
                                ) : null}
                                <div className="mt-1">
                                  <LinkOut href={ref.source_url as string}>open original</LinkOut>
                                </div>
                              </li>
                            ),
                          )}
                        </ul>
                      </Panel>
                    )}
                </>
              ) : (
                <Panel title="Trace a signal" dense>
                  <div className="space-y-2 p-3 text-2xs leading-relaxed text-ink-muted">
                    <p>
                      Select a signal to see the chain that produced it: which records were
                      collected, how duplicates and syndications were collapsed, which detector
                      fired, and how the resulting confidence was reached.
                    </p>
                    <p className="text-ink-faint">
                      Confidence expresses the strength of the evidence behind a finding. It is not
                      the probability that the finding is true.
                    </p>
                  </div>
                </Panel>
              )}
            </div>
          </div>

          {provenance && (
            <div className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2">
              <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
                Store provenance
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-6 gap-y-1 font-mono text-2xs text-ink-muted">
                {Object.entries((provenance.bronze as Record<string, unknown>) ?? {}).map(
                  ([key, value]) => (
                    <span key={key}>
                      {key}: {typeof value === 'number' ? count(value) : String(value)}
                    </span>
                  ),
                )}
              </div>
            </div>
          )}
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 13. Backtesting --------------------------------------------------------

export function BacktestPanel() {
  const [detector, setDetector] = useState('spike');
  const [days, setDays] = useState(30);
  const state = useModule('backtest', { detector, days });
  const data = state.data;
  const evaluation = pick<Record<string, unknown>>(data, 'evaluation');
  const sweep = list<Record<string, unknown>>(evaluation, 'sweep');
  const method = pick<Record<string, string>>(data, 'method');
  const howToRead = pick<string[]>(data, 'how_to_read_this');
  const caveats = pick<string[]>(evaluation, 'caveats');

  const curve = sweep.map((row) => ({
    name: String(row.threshold),
    precision: Number(((row.precision as number) ?? 0).toFixed?.(3) ?? row.precision ?? 0),
    recall: Number(((row.recall as number) ?? 0).toFixed?.(3) ?? row.recall ?? 0),
  }));

  const severityRows = useMemo(
    () =>
      Object.entries((pick<Record<string, number>>(evaluation, 'by_severity') as Record<string, number>) ?? {}).map(
        ([name, value]) => ({ name, value }),
      ),
    [evaluation],
  );

  return (
    <Panel
      title="Backtesting"
      subtitle="How the detectors actually perform against recorded events — with the rules that produced the numbers."
      actions={<ExportLink module="backtest" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-2xs text-ink-faint">detector</span>
          {['spike', 'sentiment', 'coverage'].map((option) => (
            <Toggle
              key={option}
              checked={detector === option}
              onChange={() => setDetector(option)}
              label={option}
            />
          ))}
          <span className="ml-2 text-2xs text-ink-faint">window</span>
          {[7, 30, 90].map((value) => (
            <Toggle key={value} checked={days === value} onChange={() => setDays(value)} label={`${value}d`} />
          ))}
          <button
            type="button"
            onClick={state.reload}
            className="ml-auto rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
          >
            re-evaluate
          </button>
        </div>

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta}
          isEmpty={!evaluation}
          onRetry={state.reload}
          emptyTitle="No evaluation window available"
        >
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat
              label="precision"
              value={number(evaluation?.precision as number, 3)}
              hint="of the alerts fired, how many had a real event"
            />
            <Stat
              label="recall"
              value={number(evaluation?.recall as number, 3)}
              hint="of the real events, how many were found"
            />
            <Stat
              label="coverage-adjusted recall"
              value={number(evaluation?.coverage_adjusted_recall as number, 3)}
              hint="discounts misses in thinly-observed territories"
            />
            <Stat
              label="median lead time"
              value={
                evaluation?.median_lead_time_hours === null ||
                evaluation?.median_lead_time_hours === undefined
                  ? '—'
                  : `${number(evaluation.median_lead_time_hours as number, 1)}h`
              }
              hint="how early the alert fired"
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel
              title="Threshold trade-off"
              subtitle="Every operating point, because a single accuracy figure hides the choice"
              dense
            >
              <div className="p-3">
                <MultiLine
                  data={curve}
                  series={[
                    { key: 'precision', colour: '#38bdf8', label: 'precision' },
                    { key: 'recall', colour: '#ffb020', label: 'recall' },
                  ]}
                  height={200}
                />
              </div>
            </Panel>
            <Panel title="Evaluated signals" subtitle="Severity mix across the window" dense>
              <div className="p-3">
                <SeverityDonut data={severityRows} height={200} />
              </div>
            </Panel>
          </div>

          <Panel title="Matching rules" subtitle="How a detection was counted as a hit" dense>
            <div className="space-y-2 p-3">
              {method &&
                Object.entries(method).map(([key, value]) => (
                  <div key={key} className="text-2xs leading-relaxed">
                    <span className="font-semibold uppercase tracking-wider text-ink-faint">
                      {key.replace(/_/g, ' ')}
                    </span>
                    <p className="text-ink-muted">{value}</p>
                  </div>
                ))}
              {howToRead && (
                <ul className="space-y-1 pt-1">
                  {howToRead.map((line, index) => (
                    <li key={index} className="text-2xs text-ink-faint">
                      · {line}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Panel>

          {caveats && <Caveats items={caveats} title="Known limits of this evaluation" />}

          <RecordTable
            columns={[
              {
                key: 'threshold',
                label: 'Threshold',
                className: 'w-24 font-mono',
                render: (row) => String(row.threshold),
              },
              {
                key: 'precision',
                label: 'Precision',
                className: 'w-28 font-mono',
                render: (row) => number(row.precision as number, 3),
              },
              {
                key: 'recall',
                label: 'Recall',
                className: 'w-24 font-mono',
                render: (row) => number(row.recall as number, 3),
              },
              {
                key: 'detections',
                label: 'Detections',
                className: 'w-24 font-mono',
                render: (row) => String(row.detections ?? '—'),
              },
              {
                key: 'hits',
                label: 'Hits',
                className: 'w-20 font-mono',
                render: (row) => String(row.true_positives ?? '—'),
              },
              {
                key: 'fp',
                label: 'False pos.',
                className: 'w-24 font-mono',
                render: (row) => String(row.false_positives ?? '—'),
              },
            ]}
            rows={sweep}
            emptyMessage="No threshold sweep available for this detector."
          />
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 14. Watchlists & Digests -----------------------------------------------

export function WatchlistsPanel() {
  const [name, setName] = useState('');
  const [countries, setCountries] = useState('NG,KE,ZA');
  const [frequency, setFrequency] = useState('daily');
  const [digest, setDigest] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const state = useModule('watchlists', {});
  const data = state.data;
  const watchlists = list<Record<string, unknown>>(data, 'watchlists');
  const storageNote = pick<string>(data, 'storage_note');

  const create = useCallback(async () => {
    if (!name.trim()) {
      setNotice('A watchlist needs a name.');
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const response = await createWatchlist({
        name: name.trim(),
        countries,
        frequency,
        modules: 'events,news',
      });
      const created = pick<Record<string, unknown>>(response, 'watchlist');
      setNotice(`Created "${String(created?.name ?? name)}".`);
      setName('');
      state.reload();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Could not create the watchlist.');
    } finally {
      setBusy(false);
    }
  }, [name, countries, frequency, state]);

  const remove = useCallback(
    async (id: string) => {
      setBusy(true);
      try {
        await deleteWatchlist(id);
        setNotice('Watchlist deleted.');
        if (digest) setDigest(null);
        state.reload();
      } catch (error) {
        setNotice(error instanceof Error ? error.message : 'Could not delete the watchlist.');
      } finally {
        setBusy(false);
      }
    },
    [digest, state],
  );

  const loadDigest = useCallback(async (id: string) => {
    setBusy(true);
    try {
      const response = await getDigest(id);
      setDigest(String(pick(response, 'digest.text_preview') ?? 'No digest text produced.'));
    } catch (error) {
      setDigest(error instanceof Error ? error.message : 'Could not build the digest.');
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <Panel
      title="Watchlists & Digests"
      subtitle="Monitoring scoped to the territories you care about, with a digest that states its own provenance."
      actions={<ExportLink module="watchlists" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-2 rounded border border-surface-400/50 bg-surface-700/30 p-3">
          <label className="flex flex-col gap-1">
            <span className="text-2xs uppercase tracking-wider text-ink-faint">name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Sahel monitoring"
              className="w-48 rounded border border-surface-400 bg-surface-700/60 px-2 py-1 text-xs text-ink outline-none placeholder:text-ink-faint focus:border-signal/60"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-2xs uppercase tracking-wider text-ink-faint">
              territories (ISO codes)
            </span>
            <input
              value={countries}
              onChange={(event) => setCountries(event.target.value)}
              placeholder="NG,KE,ZA"
              className="w-40 rounded border border-surface-400 bg-surface-700/60 px-2 py-1 font-mono text-xs text-ink outline-none placeholder:text-ink-faint focus:border-signal/60"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-2xs uppercase tracking-wider text-ink-faint">cadence</span>
            <div className="flex gap-1">
              {['daily', 'weekly'].map((option) => (
                <Toggle
                  key={option}
                  checked={frequency === option}
                  onChange={() => setFrequency(option)}
                  label={option}
                />
              ))}
            </div>
          </label>
          <button
            type="button"
            onClick={create}
            disabled={busy}
            className="rounded border border-signal/60 bg-signal/15 px-3 py-1.5 text-2xs text-signal hover:bg-signal/25 disabled:opacity-50"
          >
            {busy ? 'working…' : 'create watchlist'}
          </button>
        </div>

        {notice && (
          <p className="rounded border border-surface-400/50 bg-surface-700/40 px-3 py-2 text-2xs text-ink-muted">
            {notice}
          </p>
        )}

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta}
          isEmpty={watchlists.length === 0}
          onRetry={state.reload}
          emptyTitle="No watchlists yet"
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-2">
              {watchlists.map((watchlist) => {
                const codes = Array.isArray(watchlist.countries)
                  ? (watchlist.countries as string[])
                  : [];
                return (
                  <div
                    key={String(watchlist.id)}
                    className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2.5"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="text-xs font-semibold text-ink">{String(watchlist.name)}</div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-1">
                          {codes.slice(0, 8).map((iso) => (
                            <Tag key={iso}>{iso}</Tag>
                          ))}
                          {codes.length > 8 && <Tag>+{codes.length - 8}</Tag>}
                        </div>
                        <div className="mt-1 font-mono text-2xs text-ink-faint">
                          {String(watchlist.frequency)} · min severity{' '}
                          {String(watchlist.min_severity)}
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-col gap-1">
                        <button
                          type="button"
                          onClick={() => loadDigest(String(watchlist.id))}
                          className="rounded border border-signal/50 bg-signal/10 px-2 py-0.5 text-2xs text-signal hover:bg-signal/20"
                        >
                          digest
                        </button>
                        <button
                          type="button"
                          onClick={() => remove(String(watchlist.id))}
                          className="rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-faint hover:border-critical/50 hover:text-critical"
                        >
                          delete
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            <Panel title="Digest preview" subtitle="What would be delivered" dense>
              <div className="p-3">
                {digest ? (
                  <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-surface-400/50 bg-surface-900 p-3 font-mono text-2xs leading-relaxed text-ink-muted">
                    {digest}
                  </pre>
                ) : (
                  <p className="text-2xs leading-relaxed text-ink-muted">
                    Select <span className="text-signal">digest</span> on a watchlist to build its
                    report. The digest is scoped to that watchlist&apos;s territories, carries the
                    coverage confidence of each, and states the age of the underlying data.
                  </p>
                )}
              </div>
            </Panel>
          </div>

          {storageNote && (
            <p className="rounded border border-alert/25 bg-alert/5 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
              <span className="font-semibold text-alert">Storage.</span> {storageNote}
            </p>
          )}
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

export function MapPanelMeta({ meta }: { meta?: ResponseMeta }) {
  return <MetaFooter meta={meta} />;
}
