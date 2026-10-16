'use client';

/**
 * Status bar.
 *
 * Deliberately the first thing on the page. The single most important fact
 * about any view built on live feeds is whether the feeds are currently
 * working, and burying that in a settings tab is how an operator ends up
 * reading a stale dashboard as a live one.
 */

import { ServerCrash, Server, RefreshCw, ShieldCheck, ShieldAlert } from 'lucide-react';

import { Tag } from '@/components/ui';
import { count, timestamp } from '@/lib/format';
import { useHealth } from '@/lib/hooks';

export function StatusBar() {
  const { health, error, loading } = useHealth();

  const degraded = health?.sources.degraded ?? false;

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-surface-400/60 bg-surface-800/70 px-4 py-3 shadow-panel">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded border border-signal/40 bg-signal/10">
          <Server className="h-4 w-4 text-signal" aria-hidden />
        </div>
        <div>
          <h1 className="text-sm font-semibold tracking-tight text-ink">IntelligenceGrid</h1>
          <p className="text-2xs text-ink-faint">
            Open global situational awareness · 14 modules · free key-less sources
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        {loading && (
          <span className="flex items-center gap-1.5 text-2xs text-ink-faint">
            <RefreshCw className="h-3 w-3 animate-spin" aria-hidden />
            contacting service…
          </span>
        )}

        {error && (
          <Tag tone="alert">
            <ServerCrash className="h-3 w-3" aria-hidden />
            service unreachable
          </Tag>
        )}

        {health && (
          <>
            <span className="text-2xs text-ink-faint">
              datasets <span className="font-mono text-ink-muted">{count(health.dataset_count)}</span>
            </span>
            <span className="text-2xs text-ink-faint">
              sources{' '}
              <span className={degraded ? 'font-mono text-alert' : 'font-mono text-good'}>
                {health.sources.usable}/{health.sources.registered}
              </span>
            </span>
            <Tag tone={degraded ? 'alert' : 'good'}>
              {degraded ? (
                <>
                  <ShieldAlert className="h-3 w-3" aria-hidden />
                  degraded
                </>
              ) : (
                <>
                  <ShieldCheck className="h-3 w-3" aria-hidden />
                  healthy
                </>
              )}
            </Tag>
            <span className="hidden font-mono text-2xs text-ink-faint sm:inline">
              {timestamp(health.checked_at)}
            </span>
          </>
        )}
      </div>

      {error && (
        <p className="w-full rounded border border-critical/40 bg-critical/10 px-3 py-2 text-2xs leading-relaxed text-critical">
          {error.message}
          {error.hint ? <span className="mt-1 block text-ink-muted">{error.hint}</span> : null}
        </p>
      )}
    </header>
  );
}
