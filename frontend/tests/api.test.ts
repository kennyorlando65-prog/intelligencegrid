/**
 * Tests for the API client's URL construction.
 *
 * These exist because of a specific production failure: `fetch('/api/news')`
 * executed in a Server Component threw `Failed to parse URL from /api/news`.
 * The server has no `document`, so a relative URL has no origin to resolve
 * against and the WHATWG parser throws *before any network request is made*.
 *
 * The regression that matters is therefore not "does it return the right
 * string" but "does the result survive `new URL()`" -- a relative string that
 * looks correct still throws at the call site.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, resolveUrl } from '../lib/api';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe('resolveUrl', () => {
  it('absolutises a relative path against an explicit origin', () => {
    expect(resolveUrl('/news', 'https://grid.example')).toBe(
      'https://grid.example/api/news',
    );
  });

  it('produces a string that new URL() accepts', () => {
    // The actual regression: this must not throw.
    const resolved = resolveUrl('/assets', 'https://grid.example');
    expect(() => new URL(resolved)).not.toThrow();
  });

  it('keeps the /api prefix exactly once', () => {
    expect(resolveUrl('/news', 'https://grid.example')).toContain('/api/news');
    expect(resolveUrl('/news', 'https://grid.example')).not.toContain('/api/api');
  });

  it('tolerates a path given without a leading slash', () => {
    expect(resolveUrl('news', 'https://grid.example')).toBe(
      'https://grid.example/api/news',
    );
  });

  it('preserves a query string', () => {
    expect(resolveUrl('/news?limit=5', 'https://grid.example')).toBe(
      'https://grid.example/api/news?limit=5',
    );
  });

  it('returns a relative path when there is no origin at all', () => {
    // Documents the server-side hazard rather than pretending it is safe:
    // with no origin the caller must supply one, or the fetch will throw.
    const resolved = resolveUrl('/news');
    expect(resolved).toBe('/api/news');
    expect(() => new URL(resolved)).toThrow();
  });

  it('uses an absolute API_BASE verbatim, ignoring the origin', async () => {
    vi.resetModules();
    vi.stubEnv('NEXT_PUBLIC_API_BASE', 'https://api.example.test/');
    const mod = await import('../lib/api');
    // Trailing slash is normalised away so the join cannot double up.
    expect(mod.API_BASE).toBe('https://api.example.test');
    expect(mod.resolveUrl('/news', 'https://grid.example')).toBe(
      'https://api.example.test/api/news',
    );
  });
});

describe('ApiError', () => {
  it('carries a kind and a hint so the UI can advise correctly', () => {
    const error = new ApiError('boom', 404, 'service-missing', 'check the preset');
    expect(error.kind).toBe('service-missing');
    expect(error.hint).toBe('check the preset');
    expect(error).toBeInstanceOf(Error);
  });
});
