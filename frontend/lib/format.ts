/**
 * Presentation formatting.
 *
 * The recurring theme: never let a number imply more precision than the data
 * supports. Confidence is shown as a band, counts are grouped, and a missing
 * timestamp renders as an explicit "not stated" rather than a blank that
 * could be read as a real zero.
 */

import type { Severity } from './types';

export const SEVERITY_ORDER: Severity[] = ['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

/** Colour token per severity, matching the two-hue rule in the Tailwind config. */
export function severityColor(severity?: string): string {
  switch ((severity ?? '').toUpperCase()) {
    case 'CRITICAL':
      return 'text-critical border-critical/40 bg-critical/10';
    case 'HIGH':
      return 'text-alert border-alert/40 bg-alert/10';
    case 'MEDIUM':
      return 'text-alert-dim border-alert-dim/50 bg-alert-dim/10';
    case 'LOW':
      return 'text-signal border-signal/40 bg-signal/10';
    default:
      return 'text-ink-faint border-surface-400 bg-surface-600/40';
  }
}

export function severityRank(severity?: string): number {
  const index = SEVERITY_ORDER.indexOf((severity ?? 'INFO').toUpperCase() as Severity);
  return index < 0 ? 0 : index;
}

/** A confidence value as a word, because a bare 0.63 overstates precision. */
export function confidenceBand(value?: number | null): string {
  if (value === null || value === undefined) return 'unknown';
  if (value >= 0.85) return 'very strong';
  if (value >= 0.65) return 'strong';
  if (value >= 0.45) return 'moderate';
  if (value >= 0.25) return 'weak';
  return 'very weak';
}

export function confidencePercent(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${Math.round(value * 100)}%`;
}

export function count(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toLocaleString('en-US');
}

export function number(value?: number | null, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toFixed(digits);
}

/** ISO timestamp as a compact UTC string. Never silently blanks. */
export function timestamp(value?: string | null): string {
  if (!value) return 'not stated';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'not stated';
  return `${date.toISOString().replace('T', ' ').slice(0, 16)}Z`;
}

/** Coarse relative age. Deliberately does not pretend to be exact. */
export function relative(value?: string | null): string {
  if (!value) return 'unknown age';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'unknown age';
  const ms = Date.now() - date.getTime();
  const future = ms < 0;
  const hours = Math.abs(ms) / 3_600_000;
  const label =
    hours < 1
      ? `${Math.max(1, Math.round(hours * 60))}m`
      : hours < 48
        ? `${Math.round(hours)}h`
        : `${Math.round(hours / 24)}d`;
  return future ? `in ${label}` : `${label} ago`;
}

/** Read a nested value without throwing on a partial payload. */
export function pick<T = unknown>(source: unknown, ...path: string[]): T | undefined {
  let cursor: unknown = source;
  for (const key of path) {
    if (cursor === null || typeof cursor !== 'object') return undefined;
    cursor = (cursor as Record<string, unknown>)[key];
  }
  return cursor as T | undefined;
}

/** Read an array field, defaulting to empty rather than undefined. */
export function list<T = Record<string, unknown>>(source: unknown, key: string): T[] {
  const value = pick(source, key);
  return Array.isArray(value) ? (value as T[]) : [];
}

/** Coerce a facet list ([{key,count}]) into recharts-friendly rows. */
export function facetRows(
  facets: unknown,
  limit = 10,
): { name: string; value: number }[] {
  if (!Array.isArray(facets)) return [];
  return facets
    .slice(0, limit)
    .map((entry) => ({
      name: String((entry as Record<string, unknown>)?.key ?? '—'),
      value: Number((entry as Record<string, unknown>)?.count ?? 0),
    }))
    .filter((row) => row.value > 0);
}

export function truncate(value: string, max = 140): string {
  if (value.length <= max) return value;
  const cut = value.slice(0, max);
  const lastSpace = cut.lastIndexOf(' ');
  return `${(lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}

export function classNames(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(' ');
}
