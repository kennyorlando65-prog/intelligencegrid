'use client';

/**
 * Source health panel.
 *
 * This panel exists because a dashboard's most dangerous failure is a silent
 * one. When a feed dies, every module that depends on it keeps rendering
 * perfectly valid empty results, and an empty result is indistinguishable
 * from a quiet world unless the health of the feed is shown next to it.
 *
 * Error text is surfaced verbatim rather than summarised: "429 Too Many
 * Requests" and "connection reset" need completely different responses from
 * whoever is on call.
 */

import { DataState, Panel, Stat, Tag } from '@/components/ui';
import { count, number, relative, timestamp } from '@/lib/format';
import { useSources } from '@/lib/hooks';

export function SourcesPanel() {
  const state = useSources();
  const data = state.data;
  const sources = data?.sources ?? [];

  return (
    <Panel
      title="Source Health"
      subtitle="Every upstream feed, its current state, and the exact error when one fails."
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="sources registered" value={count(data?.total ?? 0)} />
          <Stat
            label="usable"
            value={count(data?.usable ?? 0)}
            tone={data && data.total > 0 && data.usable === data.total ? 'good' : 'alert'}
          />
          <Stat
            label="requiring attention"
            value={count(Math.max(0, (data?.total ?? 0) - (data?.usable ?? 0)))}
            tone={data && data.usable < data.total ? 'alert' : 'good'}
          />
          <Stat
            label="last collection"
            value={relative(String((data?.last_run as Record<string, unknown>)?.finished_at ?? ''))}
            hint="most recent completed run"
          />
        </div>

        <DataState
          loading={state.loading}
          error={state.error}
          isEmpty={sources.length === 0}
          onRetry={state.reload}
          emptyTitle="No source has reported health yet"
        >
          <div className="overflow-hidden rounded border border-surface-400/50">
            <table className="w-full text-left">
              <thead className="bg-surface-700">
                <tr>
                  {['source', 'domain', 'state', 'latency', 'records', 'last success'].map((head) => (
                    <th
                      key={head}
                      scope="col"
                      className="border-b border-surface-400/60 px-3 py-2 text-2xs font-semibold uppercase tracking-wider text-ink-faint"
                    >
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sources.map((source) => (
                  <tr key={source.name} className="hover:bg-surface-700/40">
                    <td className="border-b border-surface-400/25 px-3 py-2 font-mono text-2xs text-ink">
                      {source.name}
                    </td>
                    <td className="border-b border-surface-400/25 px-3 py-2 text-2xs text-ink-muted">
                      {source.domain ?? '—'}
                    </td>
                    <td className="border-b border-surface-400/25 px-3 py-2">
                      <Tag tone={source.is_usable ? 'good' : 'alert'}>{source.status}</Tag>
                    </td>
                    <td className="border-b border-surface-400/25 px-3 py-2 font-mono text-2xs text-ink-muted">
                      {source.latency_ms === null || source.latency_ms === undefined
                        ? '—'
                        : `${number(source.latency_ms, 0)} ms`}
                    </td>
                    <td className="border-b border-surface-400/25 px-3 py-2 font-mono text-2xs text-ink-muted">
                      {count(source.record_count)}
                    </td>
                    <td className="border-b border-surface-400/25 px-3 py-2 font-mono text-2xs text-ink-muted">
                      {source.last_success_at ? (
                        <span title={timestamp(source.last_success_at)}>
                          {relative(source.last_success_at)}
                        </span>
                      ) : (
                        <span className="text-ink-faint">never</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {sources.some((source) => source.error) && (
            <div className="rounded border border-critical/30 bg-critical/5 px-3 py-2.5">
              <div className="text-2xs font-semibold uppercase tracking-wider text-critical">
                Reported errors
              </div>
              <ul className="mt-1.5 space-y-1">
                {sources
                  .filter((source) => source.error)
                  .map((source) => (
                    <li key={source.name} className="text-2xs leading-relaxed">
                      <span className="font-mono text-ink">{source.name}</span>
                      <span className="mt-0.5 block font-mono text-critical">{source.error}</span>
                    </li>
                  ))}
              </ul>
            </div>
          )}

          <p className="rounded border border-surface-400/50 bg-surface-700/30 px-3 py-2 text-2xs leading-relaxed text-ink-muted">
            <span className="font-semibold text-ink">Why this panel matters.</span> When a feed
            fails, every module that depends on it continues to render valid-looking empty results.
            Those are indistinguishable from a genuinely quiet picture unless feed health is shown
            alongside them — which is why every module response carries this information in its own
            metadata rather than relying on this page being open.
          </p>
        </DataState>
      </div>
    </Panel>
  );
}
