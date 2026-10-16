'use client';

/**
 * Dashboard shell.
 *
 * One page with a tab per module rather than fourteen routes. The modules
 * share a data layer and are meant to be read against each other -- coverage
 * next to the map, evidence next to signals -- so routing each one to its own
 * URL would add navigation cost without adding reachability.
 *
 * Tab state is driven by the URL hash, which means a panel is linkable and
 * the browser's back button works, without pulling in a router.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { StatusBar } from '@/components/StatusBar';
import { SourcesPanel } from '@/components/SourcesPanel';
import { EventFeed } from '@/components/EventFeed';
import { NewsPanel, EventsPanel, SignalsPanel, ThreatsPanel } from '@/components/panels/intel';
import {
  CoveragePanel,
  FlightsPanel,
  MaritimePanel,
  SanctionsPanel,
  TimelinePanel,
} from '@/components/panels/geo';
import {
  BacktestPanel,
  EntitiesPanel,
  EvidencePanel,
  MapPanel,
  WatchlistsPanel,
} from '@/components/panels/analysis';

interface TabDefinition {
  id: string;
  label: string;
  group: 'Intelligence' | 'Domain' | 'Geography' | 'Analysis';
  blurb: string;
}

const TABS: TabDefinition[] = [
  { id: 'news', label: 'News', group: 'Intelligence', blurb: 'Searchable reporting with syndication tracked separately' },
  { id: 'events', label: 'Events', group: 'Intelligence', blurb: 'Geocoded events, clustering and burst detection' },
  { id: 'signals', label: 'Signals', group: 'Intelligence', blurb: 'Detected signals and narrative sentiment' },
  { id: 'threats', label: 'Threats', group: 'Domain', blurb: 'Exploited vulnerabilities versus the wider catalogue' },
  { id: 'sanctions', label: 'Sanctions', group: 'Domain', blurb: 'Three jurisdictions, kept deliberately separate' },
  { id: 'flights', label: 'Flights', group: 'Domain', blurb: 'ADS-B movement anomalies with spoofing caveats' },
  { id: 'maritime', label: 'Maritime', group: 'Domain', blurb: 'Sanctioned vessels, and what live tracking cannot show' },
  { id: 'map', label: 'Map', group: 'Geography', blurb: 'Events over a coverage-confidence base layer' },
  { id: 'timeline', label: 'Timeline', group: 'Geography', blurb: 'Scrubbable reconstruction of the window' },
  { id: 'coverage', label: 'Coverage', group: 'Geography', blurb: 'How well each territory is observed' },
  { id: 'entities', label: 'Entities', group: 'Analysis', blurb: 'People, organisations and the links between them' },
  { id: 'evidence', label: 'Evidence', group: 'Analysis', blurb: 'Sources and reasoning behind every signal' },
  { id: 'backtest', label: 'Backtesting', group: 'Analysis', blurb: 'Precision, recall and lead time, with the rules' },
  { id: 'watchlists', label: 'Watchlists', group: 'Analysis', blurb: 'Scoped monitoring and digests' },
  { id: 'sources', label: 'Sources', group: 'Analysis', blurb: 'Per-feed health, including what failed and why' },
];

const GROUPS: TabDefinition['group'][] = ['Intelligence', 'Domain', 'Geography', 'Analysis'];

export default function DashboardPage() {
  const [active, setActive] = useState('news');
  const [ready, setReady] = useState(false);

  // Read the initial tab from the hash so a panel is directly linkable.
  useEffect(() => {
    const fromHash = window.location.hash.replace('#', '');
    if (TABS.some((tab) => tab.id === fromHash)) setActive(fromHash);
    setReady(true);
  }, []);

  const select = useCallback((id: string) => {
    setActive(id);
    if (typeof window !== 'undefined') {
      window.history.replaceState(null, '', `#${id}`);
    }
  }, []);

  const activeTab = useMemo(() => TABS.find((tab) => tab.id === active) ?? TABS[0], [active]);

  const byGroup = useMemo(
    () =>
      GROUPS.map((group) => ({
        group,
        tabs: TABS.filter((tab) => tab.group === group),
      })),
    [],
  );

  return (
    <div className="mx-auto flex min-h-screen max-w-[1800px] flex-col gap-4 px-4 py-4 lg:px-6">
      <StatusBar />

      <EventFeed />

      <nav aria-label="Modules" className="space-y-2">
        {byGroup.map(({ group, tabs }) => (
          <div key={group} className="flex flex-wrap items-center gap-1.5">
            <span className="w-24 shrink-0 text-2xs uppercase tracking-wider text-ink-faint">
              {group}
            </span>
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => select(tab.id)}
                aria-current={active === tab.id ? 'page' : undefined}
                title={tab.blurb}
                className={
                  active === tab.id
                    ? 'rounded border border-signal/60 bg-signal/15 px-2.5 py-1 text-2xs font-medium text-signal'
                    : 'rounded border border-surface-400/60 px-2.5 py-1 text-2xs text-ink-muted hover:border-surface-500 hover:text-ink'
                }
              >
                {tab.label}
              </button>
            ))}
          </div>
        ))}
      </nav>

      <p className="text-2xs text-ink-faint">{activeTab.blurb}</p>

      <main className="flex-1" aria-live="polite">
        {ready && <ActivePanel id={active} />}
      </main>

      <footer className="border-t border-surface-400/40 pt-3 text-2xs leading-relaxed text-ink-faint">
        <p>
          Every figure here is derived from free, key-less public sources and is presented with the
          coverage it was drawn from. Where a territory is thinly observed, the platform reports
          that rather than reporting calm. Confidence values express the strength of available
          evidence; they are never a statement of certainty.
        </p>
      </footer>
    </div>
  );
}

function ActivePanel({ id }: { id: string }) {
  switch (id) {
    case 'news':
      return <NewsPanel />;
    case 'events':
      return <EventsPanel />;
    case 'signals':
      return <SignalsPanel />;
    case 'threats':
      return <ThreatsPanel />;
    case 'sanctions':
      return <SanctionsPanel />;
    case 'flights':
      return <FlightsPanel />;
    case 'maritime':
      return <MaritimePanel />;
    case 'map':
      return <MapPanel />;
    case 'timeline':
      return <TimelinePanel />;
    case 'coverage':
      return <CoveragePanel />;
    case 'entities':
      return <EntitiesPanel />;
    case 'evidence':
      return <EvidencePanel />;
    case 'backtest':
      return <BacktestPanel />;
    case 'watchlists':
      return <WatchlistsPanel />;
    case 'sources':
      return <SourcesPanel />;
    default:
      return <NewsPanel />;
  }
}
