'use client';

/**
 * Sanctions, flight, maritime, timeline and coverage panels.
 *
 * The maritime panel is the clearest illustration of the platform's stance:
 * the honest thing to build was a module that reports what it *cannot* see.
 * A blank vessel map is indistinguishable from an empty ocean, so it states
 * the availability limitation in the response itself rather than leaving the
 * reader to infer it from an absence.
 */

import { useState } from 'react';

import { FacetBar } from '@/components/charts';
import {
  Caveats,
  ConfidenceMeter,
  DataState,
  LinkOut,
  MetaFooter,
  Panel,
  RecordTable,
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
import { useModule } from '@/lib/hooks';
import type { ResponseMeta } from '@/lib/types';

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

// -- 5. SanctionsGraph ------------------------------------------------------

export function SanctionsPanel() {
  const [entityType, setEntityType] = useState<string>('');
  const state = useModule('sanctions', { limit: 150, entity_type: entityType });
  const data = state.data;
  const entities = list<Record<string, unknown>>(data, 'entities');
  const byList = facetRows(pick(data, 'aggregates.by_list'), 6);
  const byProgram = facetRows(pick(data, 'aggregates.by_program'), 8);
  const crossMatches = list<Record<string, unknown>>(data, 'cross_list_matches');
  const graphStats = pick<Record<string, number>>(data, 'graph.stats');
  const listsNote = pick<string>(data, 'sources.note');

  return (
    <Panel
      title="SanctionsGraph"
      subtitle="Sanctioned entities across three jurisdictions, with the relationship graph built around them."
      actions={<ExportLink module="sanctions" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          {[
            { value: '', label: 'all types' },
            { value: 'individual', label: 'individuals' },
            { value: 'entity', label: 'entities' },
            { value: 'vessel', label: 'vessels' },
          ].map((option) => (
            <FilterButton
              key={option.value || 'all'}
              active={entityType === option.value}
              onClick={() => setEntityType(option.value)}
            >
              {option.label}
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
          <Stat label="listed entities" value={count(pick<number>(data, 'total_entities'))} />
          <Stat label="shown" value={count(pick<number>(data, 'returned'))} />
          <Stat
            label="cross-listed"
            value={count(crossMatches.length)}
            hint="named by more than one jurisdiction"
            tone={crossMatches.length > 0 ? 'alert' : 'default'}
          />
          <Stat label="graph nodes" value={count(graphStats?.nodes)} hint="relationships mapped" />
        </div>

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta}
          isEmpty={entities.length === 0}
          onRetry={state.reload}
          emptyTitle="No sanctioned entities match these filters"
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="By jurisdiction" dense>
              <div className="p-3">
                <FacetBar data={byList} colour="#ffb020" />
              </div>
            </Panel>
            <Panel title="By programme" dense>
              <div className="p-3">
                <FacetBar data={byProgram} colour="#38bdf8" />
              </div>
            </Panel>
          </div>

          {crossMatches.length > 0 && (
            <div className="rounded border border-alert/30 bg-alert/5 px-3 py-2">
              <div className="text-2xs font-semibold uppercase tracking-wider text-alert">
                Named by more than one jurisdiction
              </div>
              <ul className="mt-1.5 space-y-1">
                {crossMatches.slice(0, 5).map((match, index) => (
                  <li key={index} className="text-2xs text-ink-muted">
                    <span className="text-ink">{String(match.name)}</span>
                    <span className="ml-2 font-mono text-ink-faint">
                      {Array.isArray(match.list_sources)
                        ? (match.list_sources as string[]).join(' + ')
                        : String(match.list_source ?? '')}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <RecordTable
            columns={[
              {
                key: 'name',
                label: 'Entity',
                render: (row) => (
                  <div className="max-w-md">
                    <div className="text-xs font-medium text-ink">{String(row.name)}</div>
                    {Array.isArray(row.aliases) && (row.aliases as string[]).length > 0 && (
                      <div className="mt-0.5 text-2xs text-ink-faint">
                        also: {truncate((row.aliases as string[]).slice(0, 3).join('; '), 80)}
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: 'entity_type',
                label: 'Type',
                className: 'w-24',
                render: (row) => <Tag>{String(row.entity_type)}</Tag>,
              },
              {
                key: 'list_source',
                label: 'Jurisdiction',
                className: 'w-28 font-mono',
              },
              {
                key: 'programmes',
                label: 'Programmes',
                className: 'w-40',
                render: (row) => {
                  const programs = Array.isArray(row.programs) ? (row.programs as string[]) : [];
                  return programs.length ? (
                    <span className="font-mono">{programs.slice(0, 2).join(', ')}</span>
                  ) : (
                    <span className="text-ink-faint">—</span>
                  );
                },
              },
              {
                key: 'countries',
                label: 'Associated',
                className: 'w-24 font-mono',
                render: (row) => {
                  const codes = Array.isArray(row.countries) ? (row.countries as string[]) : [];
                  return codes.length ? codes.join(', ') : <span className="text-ink-faint">—</span>;
                },
              },
              {
                key: 'link',
                label: 'Listing',
                className: 'w-20',
                render: (row) => <LinkOut href={row.source_url as string}>source</LinkOut>,
              },
            ]}
            rows={entities}
          />

          {listsNote && (
            <p className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
              <span className="font-semibold text-ink">Why listings are kept separate.</span>{' '}
              {listsNote}
            </p>
          )}
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 6. FlightWatch ---------------------------------------------------------

export function FlightsPanel() {
  const [threshold, setThreshold] = useState(0.5);
  const state = useModule('flights', { threshold });
  const data = state.data;
  const anomalies = list<Record<string, unknown>>(data, 'anomalies');
  const observations = list<Record<string, unknown>>(data, 'observations');
  const caveats = pick<string[]>(data, 'caveats');
  const coverageBoxes = list<Record<string, unknown>>(data, 'coverage_by_box');

  return (
    <Panel
      title="FlightWatch"
      subtitle="Aircraft movement anomalies from open ADS-B, scored against each aircraft's own origin cohort."
      actions={<ExportLink module="flights" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-2xs text-ink-faint">anomaly threshold</span>
          {[0.3, 0.5, 0.8].map((value) => (
            <FilterButton key={value} active={threshold === value} onClick={() => setThreshold(value)}>
              {value}
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
          <Stat label="observations" value={count(pick<number>(data, 'total_observations'))} />
          <Stat label="distinct aircraft" value={count(pick<number>(data, 'distinct_aircraft'))} />
          <Stat
            label="anomalies"
            value={count(anomalies.length)}
            hint="above threshold"
            tone={anomalies.length > 0 ? 'alert' : 'default'}
          />
          <Stat
            label="no callsign"
            value={count(pick<number>(data, 'aggregates.without_callsign'))}
            hint="routine for state aircraft"
          />
        </div>

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta}
          isEmpty={observations.length === 0}
          onRetry={state.reload}
          emptyTitle="No aircraft observations yet"
        >
          {caveats && caveats.length > 0 && <Caveats items={caveats} title="Interpretation limits" />}

          {coverageBoxes.length > 0 && (
            <Panel title="Watch coverage" subtitle="Observations per monitored region" dense>
              <div className="p-3">
                <FacetBar
                  data={coverageBoxes.map((box) => ({
                    name: String(box.label ?? box.box ?? 'region'),
                    value: Number(box.count ?? box.observations ?? 0),
                  }))}
                />
              </div>
            </Panel>
          )}

          <RecordTable
            columns={[
              {
                key: 'identifier',
                label: 'Aircraft',
                className: 'w-40 font-mono',
                render: (row) => String(row.identifier ?? '—'),
              },
              {
                key: 'callsign',
                label: 'Callsign',
                className: 'w-28 font-mono',
                render: (row) =>
                  row.callsign ? (
                    String(row.callsign)
                  ) : (
                    <span className="text-ink-faint">not broadcasting</span>
                  ),
              },
              {
                key: 'position',
                label: 'Position',
                className: 'w-40 font-mono',
                render: (row) => `${number(row.lat as number, 2)}, ${number(row.lon as number, 2)}`,
              },
              {
                key: 'altitude',
                label: 'Altitude',
                className: 'w-24 font-mono',
                render: (row) =>
                  row.altitude_m === null || row.altitude_m === undefined ? (
                    <span className="text-ink-faint">—</span>
                  ) : (
                    `${count(Math.round(Number(row.altitude_m)))} m`
                  ),
              },
              {
                key: 'anomaly',
                label: 'Anomaly',
                className: 'w-28',
                render: (row) => <ConfidenceMeter value={Number(row.anomaly_score ?? 0)} showBand={false} />,
              },
              {
                key: 'flags',
                label: 'Flags',
                className: 'w-40',
                render: (row) => {
                  const flags = Array.isArray(row.quality_flags) ? (row.quality_flags as string[]) : [];
                  return flags.length ? (
                    <div className="flex flex-wrap gap-1">
                      {flags.slice(0, 2).map((flag) => (
                        <Tag key={flag}>{flag.replace(/_/g, ' ')}</Tag>
                      ))}
                    </div>
                  ) : (
                    <span className="text-ink-faint">—</span>
                  );
                },
              },
            ]}
            rows={observations}
          />
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 7. MaritimeWatch -------------------------------------------------------

export function MaritimePanel() {
  const state = useModule('maritime');
  const data = state.data;
  const vessels = list<Record<string, unknown>>(data, 'sanctioned_vessels');
  const status = pick<Record<string, unknown>>(data, 'status');
  const capability = pick<Record<string, string[]>>(data, 'what_this_module_can_and_cannot_show');

  return (
    <Panel
      title="MaritimeWatch"
      subtitle="Sanctioned vessel exposure, with the live-tracking limitation stated rather than implied."
      actions={<ExportLink module="maritime" />}
    >
      <div className="space-y-4">
        <DataState loading={state.loading} error={state.error} meta={data?.meta} onRetry={state.reload}>
          {status && (
            <div className="rounded border border-alert/30 bg-alert/5 px-3 py-3">
              <div className="flex items-center gap-2">
                <Tag tone={status.live_feed_available ? 'good' : 'alert'}>
                  live AIS: {status.live_feed_available ? 'available' : 'unavailable'}
                </Tag>
                {status.status ? <Tag>{String(status.status)}</Tag> : null}
              </div>
              {status.reason ? (
                <p className="mt-2 text-2xs leading-relaxed text-ink-muted">{String(status.reason)}</p>
              ) : null}
            </div>
          )}

          {capability && (
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="rounded border border-good/25 bg-good/5 px-3 py-2.5">
                <div className="text-2xs font-semibold uppercase tracking-wider text-good">
                  This module does show
                </div>
                <ul className="mt-1.5 space-y-1">
                  {(capability.can_show ?? []).map((item, index) => (
                    <li key={index} className="text-2xs leading-relaxed text-ink-muted">
                      · {item}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="rounded border border-alert/25 bg-alert/5 px-3 py-2.5">
                <div className="text-2xs font-semibold uppercase tracking-wider text-alert">
                  This module does not show
                </div>
                <ul className="mt-1.5 space-y-1">
                  {(capability.cannot_show ?? []).map((item, index) => (
                    <li key={index} className="text-2xs leading-relaxed text-ink-muted">
                      · {item}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}

          {capability?.why && (
            <p className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
              <span className="font-semibold text-ink">Why.</span> {capability.why}
            </p>
          )}

          <RecordTable
            columns={[
              {
                key: 'name',
                label: 'Vessel',
                render: (row) => (
                  <div>
                    <div className="text-xs font-medium text-ink">{String(row.name)}</div>
                    {Array.isArray(row.aliases) && (row.aliases as string[]).length > 0 && (
                      <div className="text-2xs text-ink-faint">
                        also: {(row.aliases as string[]).slice(0, 2).join(', ')}
                      </div>
                    )}
                  </div>
                ),
              },
              { key: 'list_source', label: 'Jurisdiction', className: 'w-28 font-mono' },
              {
                key: 'programs',
                label: 'Programmes',
                className: 'w-40 font-mono',
                render: (row) => {
                  const programs = Array.isArray(row.programs) ? (row.programs as string[]) : [];
                  return programs.length ? programs.slice(0, 2).join(', ') : '—';
                },
              },
              {
                key: 'flags',
                label: 'Flag state',
                className: 'w-24 font-mono',
                render: (row) => {
                  const codes = Array.isArray(row.countries) ? (row.countries as string[]) : [];
                  return codes.length ? codes.join(', ') : <span className="text-ink-faint">—</span>;
                },
              },
              {
                key: 'link',
                label: 'Listing',
                className: 'w-20',
                render: (row) => <LinkOut href={row.source_url as string}>source</LinkOut>,
              },
            ]}
            rows={vessels}
            emptyMessage="No vessels appear on the sanction lists currently loaded."
          />
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 8. Timeline & Replay ---------------------------------------------------

export function TimelinePanel() {
  const [days, setDays] = useState(14);
  const [bucket, setBucket] = useState(12);
  const [frame, setFrame] = useState(0);
  const state = useModule('timeline', { days, bucket_hours: bucket });
  const data = state.data;
  const frames = list<Record<string, unknown>>(data, 'frames');
  const totals = pick<Record<string, number>>(data, 'totals');
  const notes = pick<string>(data, 'note');

  const safeFrame = frames.length ? Math.min(frame, frames.length - 1) : 0;
  const current = frames[safeFrame] as Record<string, unknown> | undefined;
  const series = frames.map((row, index) => ({
    name: `#${index + 1}`,
    value: Number(row.event_count ?? 0),
  }));

  return (
    <Panel
      title="Timeline & Replay"
      subtitle="Historical reconstruction in fixed buckets, scrubbable frame by frame."
      actions={<ExportLink module="timeline" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          {[7, 14, 30].map((value) => (
            <FilterButton key={value} active={days === value} onClick={() => setDays(value)}>
              {value}d
            </FilterButton>
          ))}
          <span className="ml-2 text-2xs text-ink-faint">bucket</span>
          {[6, 12, 24].map((value) => (
            <FilterButton key={value} active={bucket === value} onClick={() => setBucket(value)}>
              {value}h
            </FilterButton>
          ))}
          <button
            type="button"
            onClick={state.reload}
            className="ml-auto rounded border border-surface-400 px-2 py-0.5 text-2xs text-ink-muted hover:border-signal/50 hover:text-signal"
          >
            refresh
          </button>
        </div>

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta}
          isEmpty={frames.length === 0}
          onRetry={state.reload}
          emptyTitle="No historical frames in this window"
        >
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="frames" value={count(frames.length)} hint={`${bucket}h each`} />
            <Stat label="events" value={count(totals?.events)} />
            <Stat label="articles" value={count(totals?.news)} />
            <Stat label="territories" value={count(totals?.countries)} />
          </div>

          <Panel title="Volume across the window" dense>
            <div className="p-3">
              <FacetBar data={series} colour="#38bdf8" layout="horizontal" height={160} />
            </div>
          </Panel>

          <div className="rounded border border-surface-400/50 bg-surface-700/30 p-3">
            <div className="flex flex-wrap items-center gap-3">
              <input
                type="range"
                min={0}
                max={Math.max(0, frames.length - 1)}
                value={safeFrame}
                onChange={(event) => setFrame(Number(event.target.value))}
                className="h-1 flex-1 accent-sky-400"
                aria-label="Timeline frame"
              />
              <span className="font-mono text-2xs text-ink-muted">
                frame {safeFrame + 1}/{Math.max(1, frames.length)}
              </span>
            </div>
            {current && (
              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                <div>
                  <div className="text-2xs uppercase tracking-wider text-ink-faint">window</div>
                  <div className="font-mono text-2xs text-ink">
                    {timestamp(current.window_start as string)}
                  </div>
                  <div className="font-mono text-2xs text-ink-faint">
                    to {timestamp(current.window_end as string)}
                  </div>
                </div>
                <div>
                  <div className="text-2xs uppercase tracking-wider text-ink-faint">activity</div>
                  <div className="font-mono text-lg text-ink">{String(current.event_count ?? 0)}</div>
                  <div className="text-2xs text-ink-faint">
                    events · {String(current.news_count ?? 0)} articles
                  </div>
                </div>
                <div>
                  <div className="text-2xs uppercase tracking-wider text-ink-faint">
                    territories in frame
                  </div>
                  <div className="flex flex-wrap gap-1 pt-1">
                    {Object.keys(
                      (current.by_country as Record<string, number>) ?? {},
                    ).slice(0, 8).map((iso) => (
                      <Tag key={iso}>{iso}</Tag>
                    ))}
                    {Object.keys((current.by_country as Record<string, number>) ?? {}).length ===
                      0 && <span className="text-2xs text-ink-faint">none</span>}
                  </div>
                </div>
              </div>
            )}
          </div>

          {notes && (
            <p className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
              {notes}
            </p>
          )}
        </DataState>
      </div>
      <MetaFooter meta={data?.meta} />
    </Panel>
  );
}

// -- 9. Coverage ------------------------------------------------------------

export function CoveragePanel({ meta }: { meta?: ResponseMeta }) {
  const [continent, setContinent] = useState('');
  const state = useModule('coverage', { continent });
  const data = state.data;
  const continents = list<Record<string, unknown>>(data, 'continents');
  const countries = list<Record<string, unknown>>(data, 'countries');
  const summary = pick<Record<string, number>>(data, 'summary');
  const africa = pick<Record<string, unknown>>(data, 'africa_check');
  const scoring = pick<Record<string, unknown>>(data, 'scoring');
  const focus = list<Record<string, unknown>>(data, 'focus_regions');
  const interpretation = pick<string>(data, 'interpretation');

  return (
    <Panel
      title="Coverage Dashboard"
      subtitle="How well each territory is observed — the measure that makes a quiet map readable."
      actions={<ExportLink module="coverage" />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <FilterButton active={continent === ''} onClick={() => setContinent('')}>
            all continents
          </FilterButton>
          {continents.map((row) => (
            <FilterButton
              key={String(row.continent)}
              active={continent === String(row.continent)}
              onClick={() => setContinent(String(row.continent))}
            >
              {String(row.continent_name ?? row.continent)}
            </FilterButton>
          ))}
        </div>

        <DataState
          loading={state.loading}
          error={state.error}
          meta={data?.meta ?? meta}
          onRetry={state.reload}
        >
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="territories" value={count(summary?.territories)} />
            <Stat label="with data" value={count(summary?.with_data)} tone="good" />
            <Stat
              label="no data"
              value={count(summary?.no_data)}
              hint="unobserved, not necessarily quiet"
              tone="alert"
            />
            <Stat
              label="mean confidence"
              value={`${Math.round((summary?.mean_coverage_confidence ?? 0) * 100)}%`}
              hint="across all territories"
            />
          </div>

          {interpretation && (
            <p className="rounded border border-signal/25 bg-signal/5 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
              <span className="font-semibold text-signal">How to read this.</span> {interpretation}
            </p>
          )}

          <Panel
            title="Continental observation"
            subtitle="The share of each continent's territories currently returning records"
            dense
          >
            <div className="p-3">
              <FacetBar
                data={continents.map((row) => ({
                  name: String(row.continent_name ?? row.continent),
                  value: Math.round(Number(row.observation_rate ?? 0) * 100),
                }))}
                colour="#38bdf8"
                height={220}
              />
            </div>
          </Panel>

          {africa && (
            <div className="rounded border border-alert/25 bg-alert/5 px-3 py-2.5">
              <div className="text-2xs font-semibold uppercase tracking-wider text-alert">
                Africa pack integrity check
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-2xs text-ink-muted">
                <span>
                  {String(africa.countries_in_reference)} states in reference
                </span>
                <span>
                  {String(africa.countries_with_records)} returning records
                </span>
              </div>
              {africa.note ? (
                <p className="mt-1.5 text-2xs leading-relaxed text-ink-muted">{String(africa.note)}</p>
              ) : null}
            </div>
          )}

          {focus.length > 0 && (
            <Panel
              title="Largest blind spots"
              subtitle="Unobserved territories ranked by population — where thin coverage matters most"
              dense
            >
              <div className="p-3">
                <FacetBar
                  data={focus.map((row) => ({
                    name: String(row.country_name ?? row.country_iso2),
                    value: Number(row.population_millions ?? 0),
                  }))}
                  colour="#ffb020"
                />
              </div>
            </Panel>
          )}

          {Boolean(scoring?.why_population_is_excluded) && (
            <p className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
              <span className="font-semibold text-ink">Why population is excluded.</span>{' '}
              {String(scoring?.why_population_is_excluded ?? '')}
            </p>
          )}

          <RecordTable
            columns={[
              {
                key: 'country_name',
                label: 'Territory',
                className: 'w-44',
                render: (row) => (
                  <span title={String(row.country_iso2)}>
                    {String(row.country_name)}
                    <span className="ml-1.5 font-mono text-2xs text-ink-faint">
                      {String(row.country_iso2)}
                    </span>
                  </span>
                ),
              },
              {
                key: 'coverage',
                label: 'Coverage',
                className: 'w-44',
                render: (row) => <ConfidenceMeter value={Number(row.coverage_confidence ?? 0)} />,
              },
              {
                key: 'events',
                label: 'Events',
                className: 'w-20 font-mono',
                render: (row) => count(Number(row.events ?? 0)),
              },
              {
                key: 'news',
                label: 'Articles',
                className: 'w-20 font-mono',
                render: (row) => count(Number(row.news ?? 0)),
              },
              {
                key: 'sources',
                label: 'Sources',
                className: 'w-20 font-mono',
                render: (row) => count(Number(row.distinct_sources ?? 0)),
              },
              {
                key: 'state',
                label: 'State',
                className: 'w-24',
                render: (row) => {
                  const value = String(row.data_state ?? '');
                  const tone =
                    value === 'no_data' ? 'alert' : value === 'good' ? 'good' : 'neutral';
                  return <Tag tone={tone}>{value.replace(/_/g, ' ')}</Tag>;
                },
              },
              {
                key: 'last_seen',
                label: 'Last seen',
                className: 'w-28',
                render: (row) => (
                  <span className="font-mono">{relative(row.last_seen_at as string)}</span>
                ),
              },
            ]}
            rows={countries}
          />
        </DataState>
      </div>
      <MetaFooter meta={data?.meta ?? meta} />
    </Panel>
  );
}
