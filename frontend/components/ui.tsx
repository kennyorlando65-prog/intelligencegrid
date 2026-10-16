'use client';

/**
 * Presentational primitives.
 *
 * The one design decision worth stating: `DataState` distinguishes four
 * outcomes -- loading, error, empty, ready -- and the empty state always
 * says *why* it is empty by reading the response's own `meta` block. A
 * dashboard that renders a blank panel for both "no signals" and "the feed
 * is down" teaches its reader to trust a blank panel, which is the failure
 * this platform exists to avoid.
 */

import type { ReactNode } from 'react';

import { classNames, confidenceBand, confidencePercent, severityColor, timestamp } from '@/lib/format';
import type { ResponseMeta, Severity } from '@/lib/types';

export function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  dense,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  dense?: boolean;
}) {
  return (
    <section
      className={classNames(
        'rounded-lg border border-surface-400/60 bg-surface-800/70 shadow-panel',
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-surface-400/50 px-4 py-3">
          <div className="min-w-0">
            {title && (
              <h2 className="truncate text-sm font-semibold tracking-tight text-ink">{title}</h2>
            )}
            {subtitle && (
              <p className="mt-0.5 text-2xs leading-relaxed text-ink-muted">{subtitle}</p>
            )}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={dense ? 'p-0' : 'p-4'}>{children}</div>
    </section>
  );
}

export function SeverityBadge({ severity }: { severity?: string }) {
  const label = (severity ?? 'INFO').toUpperCase();
  return (
    <span
      className={classNames(
        'inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-2xs uppercase tracking-wider',
        severityColor(label),
      )}
    >
      {label}
    </span>
  );
}

export function Tag({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'signal' | 'alert' | 'good';
}) {
  const tones: Record<string, string> = {
    neutral: 'border-surface-400 bg-surface-600/40 text-ink-muted',
    signal: 'border-signal/40 bg-signal/10 text-signal',
    alert: 'border-alert/40 bg-alert/10 text-alert',
    good: 'border-good/40 bg-good/10 text-good',
  };
  return (
    <span
      className={classNames(
        'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-2xs',
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: 'default' | 'alert' | 'good';
}) {
  const valueTone =
    tone === 'alert' ? 'text-alert' : tone === 'good' ? 'text-good' : 'text-ink';
  return (
    <div className="rounded border border-surface-400/50 bg-surface-700/40 px-3 py-2">
      <div className="text-2xs uppercase tracking-wider text-ink-faint">{label}</div>
      <div className={classNames('mt-1 font-mono text-lg leading-none', valueTone)}>{value}</div>
      {hint && <div className="mt-1 text-2xs leading-snug text-ink-muted">{hint}</div>}
    </div>
  );
}

export function ConfidenceMeter({ value, showBand = true }: { value?: number | null; showBand?: boolean }) {
  const pct = value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value));
  const tone =
    pct >= 0.85 ? 'bg-good' : pct >= 0.65 ? 'bg-signal' : pct >= 0.45 ? 'bg-signal-dim' : 'bg-alert-dim';
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-500">
        <div className={classNames('h-full rounded-full', tone)} style={{ width: `${pct * 100}%` }} />
      </div>
      <span className="font-mono text-2xs text-ink-muted">{confidencePercent(value)}</span>
      {showBand && <span className="text-2xs text-ink-faint">{confidenceBand(value)}</span>}
    </div>
  );
}

/** Renders the caveats a payload carries, because they are part of the data. */
export function Caveats({ items, title = 'Constraints' }: { items?: unknown; title?: string }) {
  const list = Array.isArray(items) ? items.filter((c) => typeof c === 'string') : [];
  if (list.length === 0) return null;
  return (
    <div className="rounded border border-alert/25 bg-alert/5 px-3 py-2">
      <div className="mb-1 text-2xs font-semibold uppercase tracking-wider text-alert">{title}</div>
      <ul className="space-y-1">
        {list.map((caveat, index) => (
          <li key={index} className="text-2xs leading-relaxed text-ink-muted">
            {caveat}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The meta envelope, rendered as the reader-facing honesty footer. */
export function MetaFooter({ meta }: { meta?: ResponseMeta }) {
  if (!meta) return null;
  const degraded = meta.degraded;
  return (
    <div className="space-y-2 border-t border-surface-400/40 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-ink-faint">
        <span>
          generated <span className="font-mono text-ink-muted">{timestamp(meta.generated_at)}</span>
        </span>
        <span>
          data built{' '}
          <span className="font-mono text-ink-muted">{timestamp(meta.data_built_at)}</span>
        </span>
        <span>
          sources{' '}
          <span className={classNames('font-mono', degraded ? 'text-alert' : 'text-good')}>
            {meta.sources_usable}/{meta.sources_total} usable
          </span>
        </span>
        {degraded && (
          <Tag tone="alert">
            degraded: {meta.degraded_sources.map((s) => s.name).join(', ') || 'see sources'}
          </Tag>
        )}
      </div>
      {meta.warmup_error && (
        <div className="rounded border border-critical/40 bg-critical/10 px-2 py-1.5 text-2xs text-critical">
          Collection failed this request: <span className="font-mono">{meta.warmup_error}</span>. The
          figures below are whatever the store already held, which may be empty — that is not the
          same as no activity.
        </div>
      )}
      {meta.warnings && meta.warnings.length > 0 && (
        <ul className="space-y-0.5">
          {meta.warnings.slice(0, 4).map((warning, index) => (
            <li key={index} className="text-2xs text-ink-faint">
              · {warning}
            </li>
          ))}
        </ul>
      )}
      <p className="text-2xs italic leading-relaxed text-ink-faint">{meta.confidence_disclaimer}</p>
    </div>
  );
}

/**
 * Four-state renderer. The empty branch consults `meta` before speaking.
 */
export function DataState({
  loading,
  error,
  meta,
  isEmpty,
  onRetry,
  emptyTitle = 'No records in this view',
  children,
}: {
  loading?: boolean;
  error?: { message: string; hint?: string } | null;
  meta?: ResponseMeta;
  isEmpty?: boolean;
  onRetry?: () => void;
  emptyTitle?: string;
  children: ReactNode;
}) {
  if (loading) {
    return (
      <div className="space-y-2" aria-busy="true" aria-live="polite">
        {[0, 1, 2, 3].map((row) => (
          <div key={row} className="h-9 animate-pulse rounded bg-surface-600/50" />
        ))}
        <p className="text-2xs text-ink-faint">Collecting from upstream sources…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-3">
        <div className="rounded border border-critical/40 bg-critical/10 px-3 py-2">
          <div className="text-xs font-semibold text-critical">This panel could not load</div>
          <p className="mt-1 text-2xs leading-relaxed text-ink-muted">{error.message}</p>
        </div>
        {error.hint && (
          <div className="rounded border border-surface-400 bg-surface-700/50 px-3 py-2">
            <div className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
              What this usually means
            </div>
            <p className="mt-1 text-2xs leading-relaxed text-ink-muted">{error.hint}</p>
          </div>
        )}
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="rounded border border-signal/50 bg-signal/10 px-2.5 py-1 text-2xs text-signal hover:bg-signal/20"
          >
            Retry
          </button>
        )}
      </div>
    );
  }

  if (isEmpty) {
    const warnings = meta?.warnings ?? [];
    const degraded = meta?.degraded;
    return (
      <div className="rounded border border-surface-400/60 bg-surface-700/30 px-3 py-4">
        <div className="text-xs font-semibold text-ink-muted">{emptyTitle}</div>
        <p className="mt-1 text-2xs leading-relaxed text-ink-muted">
          {degraded
            ? 'Sources are currently degraded, so this is most likely a collection problem rather than a quiet picture.'
            : 'The sources answered and returned nothing matching the current filters. Read this as "nothing in view", not as "nothing happened".'}
        </p>
        {warnings.length > 0 && (
          <ul className="mt-2 space-y-0.5">
            {warnings.slice(0, 3).map((warning, index) => (
              <li key={index} className="text-2xs text-ink-faint">
                · {warning}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return <>{children}</>;
}

/** Scrollable record table with a monospace numeric column style. */
export function RecordTable({
  columns,
  rows,
  emptyMessage = 'Nothing to list',
}: {
  columns: { key: string; label: string; className?: string; render?: (row: Record<string, unknown>) => ReactNode }[];
  rows: Record<string, unknown>[];
  emptyMessage?: string;
}) {
  if (rows.length === 0) {
    return <p className="px-4 py-6 text-center text-2xs text-ink-faint">{emptyMessage}</p>;
  }
  return (
    <div className="max-h-[26rem] overflow-auto">
      <table className="w-full border-collapse text-left">
        <thead className="sticky top-0 z-10 bg-surface-700">
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={classNames(
                  'border-b border-surface-400/60 px-3 py-2 text-2xs font-semibold uppercase tracking-wider text-ink-faint',
                  column.className,
                )}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={String(row.id ?? index)} className="hover:bg-surface-700/40">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={classNames(
                    'border-b border-surface-400/25 px-3 py-2 align-top text-2xs text-ink-muted',
                    column.className,
                  )}
                >
                  {column.render ? column.render(row) : defaultCell(row[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function defaultCell(value: unknown): ReactNode {
  if (value === null || value === undefined || value === '') return <span className="text-ink-faint">—</span>;
  if (Array.isArray(value)) {
    return value.length === 0 ? <span className="text-ink-faint">—</span> : value.join(', ');
  }
  if (typeof value === 'object') return <span className="font-mono">{JSON.stringify(value)}</span>;
  if (typeof value === 'number') return <span className="font-mono">{value}</span>;
  return String(value);
}

export function LinkOut({ href, children }: { href?: string | null; children?: ReactNode }) {
  if (!href) return <span className="text-ink-faint">no source link</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="text-signal underline decoration-signal/40 underline-offset-2 hover:decoration-signal"
    >
      {children ?? 'source'}
    </a>
  );
}

export function SeverityLegend() {
  const levels: Severity[] = ['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {levels.map((level) => (
        <SeverityBadge key={level} severity={level} />
      ))}
    </div>
  );
}
