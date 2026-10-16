'use client';

/**
 * Headline ticker.
 *
 * A single horizontal strip of the most recent independent headlines. Its
 * purpose is orientation, not reading: it answers "has anything moved since I
 * last looked?" in one glance, so the reader can decide whether to open a
 * module rather than opening all of them in sequence.
 *
 * Syndicated copies are excluded. A ticker that repeats one wire story four
 * times because four outlets carry it conveys the opposite of the truth about
 * how much is happening.
 */

import { useMemo } from 'react';

import { Tag } from '@/components/ui';
import { count, relative, truncate } from '@/lib/format';
import { useModule } from '@/lib/hooks';

interface Article {
  id: string;
  title: string;
  url: string;
  source_name: string;
  published_at: string | null;
  countries: string[];
  is_syndicated: boolean;
}

export function EventFeed() {
  const state = useModule('news', { days: 2, limit: 40, include_syndicated: false });

  const articles = useMemo<Article[]>(() => {
    const raw = state.data?.articles;
    return Array.isArray(raw) ? (raw as unknown as Article[]) : [];
  }, [state.data]);

  if (state.error) {
    return (
      <div className="rounded border border-surface-400/50 bg-surface-800/50 px-3 py-2 text-2xs text-ink-faint">
        Headline feed unavailable — {state.error.message}
      </div>
    );
  }

  if (state.loading) {
    return (
      <div className="rounded border border-surface-400/50 bg-surface-800/50 px-3 py-2">
        <div className="h-3 w-2/3 animate-pulse rounded bg-surface-600/60" />
      </div>
    );
  }

  if (articles.length === 0) {
    return (
      <div className="rounded border border-surface-400/50 bg-surface-800/50 px-3 py-2 text-2xs text-ink-faint">
        No independent headlines in the last 48 hours. Syndicated copies are excluded from this
        strip, so this can differ from the raw article count.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded border border-surface-400/50 bg-surface-800/50">
      <div className="flex items-center gap-3 border-b border-surface-400/40 px-3 py-1.5">
        <span className="text-2xs font-semibold uppercase tracking-wider text-ink-faint">
          Latest independent
        </span>
        <span className="font-mono text-2xs text-ink-faint">
          {count(articles.length)} in 48h
        </span>
      </div>
      <ul className="max-h-32 divide-y divide-surface-400/20 overflow-y-auto">
        {articles.slice(0, 12).map((article) => (
          <li key={article.id} className="flex items-start gap-3 px-3 py-1.5 hover:bg-surface-700/40">
            <span className="w-16 shrink-0 font-mono text-2xs text-ink-faint">
              {relative(article.published_at)}
            </span>
            <a
              href={article.url}
              target="_blank"
              rel="noreferrer noopener"
              className="min-w-0 flex-1 text-2xs text-ink-muted hover:text-signal"
            >
              {truncate(article.title, 130)}
            </a>
            <span className="hidden shrink-0 items-center gap-1 sm:flex">
              {(article.countries ?? []).slice(0, 3).map((iso) => (
                <Tag key={iso}>{iso}</Tag>
              ))}
            </span>
            <span className="w-28 shrink-0 truncate text-right text-2xs text-ink-faint">
              {article.source_name}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
