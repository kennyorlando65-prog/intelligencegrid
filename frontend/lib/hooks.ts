'use client';

/**
 * Data hooks.
 *
 * `useModule` owns the whole lifecycle of one module's data: loading, error,
 * retry and unmount safety. Panels stay purely presentational as a result,
 * and every panel gets identical failure behaviour for free.
 *
 * The unmount guard matters more than it looks. A tab switch aborts nothing
 * at the network level, so a slow response from the tab you just left can
 * land after the new one and overwrite it with stale data. `alive` prevents
 * that specific, confusing bug.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError, getHealth, getModule, getSources } from './api';
import type { ModuleId, ModuleResponse, ServiceHealth } from './types';

export interface ModuleState {
  data: ModuleResponse | null;
  loading: boolean;
  error: { message: string; hint?: string } | null;
  reload: () => void;
}

function toViewError(error: unknown): { message: string; hint?: string } {
  if (error instanceof ApiError) return { message: error.message, hint: error.hint };
  if (error instanceof Error) return { message: error.message };
  return { message: 'Unknown error' };
}

export function useModule(
  module: ModuleId,
  params: Record<string, unknown> = {},
): ModuleState {
  const [data, setData] = useState<ModuleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const [nonce, setNonce] = useState(0);

  const serialised = JSON.stringify(params);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getModule(module, JSON.parse(serialised) as Record<string, unknown>)
      .then((payload) => {
        if (!cancelled && alive.current) setData(payload);
      })
      .catch((err: unknown) => {
        if (!cancelled && alive.current) setError(toViewError(err));
      })
      .finally(() => {
        if (!cancelled && alive.current) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [module, serialised, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return useMemo(() => ({ data, loading, error, reload }), [data, loading, error, reload]);
}

export interface HealthState {
  health: ServiceHealth | null;
  error: { message: string; hint?: string } | null;
  loading: boolean;
}

export function useHealth(): HealthState {
  const [health, setHealth] = useState<ServiceHealth | null>(null);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((payload) => {
        if (!cancelled) setHealth(payload);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(toViewError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { health, error, loading };
}

export interface SourcesState {
  data: import('./types').SourceReport | null;
  loading: boolean;
  error: { message: string; hint?: string } | null;
  reload: () => void;
}

/**
 * Feed health from the dedicated ``/sources`` endpoint.
 *
 * Fetched separately from any module payload: the point of this view is to
 * answer "are the feeds working?" even when a module is returning nothing,
 * so it cannot itself depend on a module's data succeeding.
 */
export function useSources(): SourcesState {
  const [data, setData] = useState<import('./types').SourceReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getSources()
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(toViewError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return useMemo(() => ({ data, loading, error, reload }), [data, loading, error, reload]);
}

/** Debounce a rapidly-changing value (free-text search inputs). */
export function useDebounced<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(handle);
  }, [value, delay]);
  return debounced;
}
