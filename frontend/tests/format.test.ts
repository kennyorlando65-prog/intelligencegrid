/**
 * Tests for the presentation helpers.
 *
 * The rule these enforce is the platform's central claim: never let a number
 * imply more precision than the data supports, and never let a missing value
 * render as a blank that reads like a real zero.
 */

import { describe, expect, it } from 'vitest';

import {
  confidenceBand,
  confidencePercent,
  count,
  facetRows,
  list,
  pick,
  relative,
  severityColor,
  severityRank,
  timestamp,
  truncate,
} from '../lib/format';

describe('confidenceBand', () => {
  it('maps a value to a word rather than a bare number', () => {
    expect(confidenceBand(0.9)).toBe('very strong');
    expect(confidenceBand(0.7)).toBe('strong');
    expect(confidenceBand(0.5)).toBe('moderate');
    expect(confidenceBand(0.3)).toBe('weak');
    expect(confidenceBand(0.1)).toBe('very weak');
  });

  it('reports unknown rather than guessing at a missing value', () => {
    expect(confidenceBand(null)).toBe('unknown');
    expect(confidenceBand(undefined)).toBe('unknown');
  });
});

describe('missing values never render as zero', () => {
  it('count renders an em dash, not 0', () => {
    expect(count(null)).toBe('\u2014');
    expect(count(undefined)).toBe('\u2014');
    expect(count(0)).toBe('0');
  });

  it('confidencePercent renders an em dash for a missing value', () => {
    expect(confidencePercent(null)).toBe('\u2014');
    expect(confidencePercent(0.63)).toBe('63%');
  });

  it('timestamp says "not stated" instead of blanking', () => {
    expect(timestamp(null)).toBe('not stated');
    expect(timestamp('not-a-date')).toBe('not stated');
    expect(timestamp('2026-10-16T12:00:00Z')).toBe('2026-10-16 12:00Z');
  });

  it('relative says "unknown age" for a missing value', () => {
    expect(relative(null)).toBe('unknown age');
    expect(relative('nonsense')).toBe('unknown age');
  });
});

describe('severity', () => {
  it('orders severities so sorting is meaningful', () => {
    expect(severityRank('CRITICAL')).toBeGreaterThan(severityRank('LOW'));
    expect(severityRank('LOW')).toBeGreaterThan(severityRank('INFO'));
  });

  it('treats an unknown severity as the lowest rank, not the highest', () => {
    expect(severityRank('nonsense')).toBe(0);
    expect(severityRank(undefined)).toBe(0);
  });

  it('returns a class string for every severity', () => {
    for (const value of ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO', undefined]) {
      expect(severityColor(value)).toBeTruthy();
    }
  });
});

describe('payload readers tolerate partial data', () => {
  it('pick walks a nested path', () => {
    expect(pick({ a: { b: { c: 7 } } }, 'a', 'b', 'c')).toBe(7);
  });

  it('pick returns undefined instead of throwing on a broken path', () => {
    expect(pick({ a: null }, 'a', 'b')).toBeUndefined();
    expect(pick(undefined, 'a')).toBeUndefined();
  });

  it('list defaults to an empty array rather than undefined', () => {
    expect(list({ items: [1, 2] }, 'items')).toEqual([1, 2]);
    expect(list({ items: 'nope' }, 'items')).toEqual([]);
    expect(list({}, 'items')).toEqual([]);
  });
});

describe('facetRows', () => {
  it('maps facets to chart rows and drops empty ones', () => {
    const rows = facetRows([
      { key: 'NG', count: 5 },
      { key: 'KE', count: 0 },
    ]);
    expect(rows).toEqual([{ name: 'NG', value: 5 }]);
  });

  it('returns an empty array for a non-array input', () => {
    expect(facetRows(null)).toEqual([]);
  });
});

describe('truncate', () => {
  it('leaves a short string untouched', () => {
    expect(truncate('short', 20)).toBe('short');
  });

  it('cuts on a word boundary and marks the cut', () => {
    const result = truncate('the quick brown fox jumps over the lazy dog', 20);
    expect(result.endsWith('\u2026')).toBe(true);
    expect(result.length).toBeLessThanOrEqual(21);
  });
});
