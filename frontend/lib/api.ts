/**
 * API client.
 *
 * Two problems are solved here that are easy to get wrong and expensive to
 * debug in production:
 *
 * 1. **Relative URLs in a Server Component.** `fetch('/api/news')` on the
 *    server has no `document`, so there is no origin to resolve against and
 *    the WHATWG URL parser throws `Failed to parse URL from /api/news`
 *    *before any network call is made*. `resolveUrl` absolutises first, using
 *    the browser origin when available.
 *
 * 2. **Distinguishing "not deployed" from "deployed and erroring".** Those
 *    need opposite fixes, and the response content-type is what tells them
 *    apart: JSON means the Python service answered (so investigate the
 *    engine), HTML means the request fell through to Next.js and the service
 *    was never mounted (so investigate the deployment). Reporting the wrong
 *    one sends the reader down the wrong path entirely.
 */

import type {
  Domain,
  ExportFormat,
  ModuleId,
  ModuleResponse,
  ServiceHealth,
  SourceReport,
} from './types';

/**
 * Base URL of the Python service.
 *
 * Empty by default: the frontend calls same-origin `/api/*` and the root
 * `vercel.json` rewrite sends those to the backend service. In local dev run
 * `next dev` and let it proxy, or set
 * `NEXT_PUBLIC_API_BASE=http://localhost:8000` to talk to uvicorn directly.
 */
export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? '').replace(/\/$/, '');

/** Prefix the service rewrite listens on. */
const API_PREFIX = '/api';

/**
 * Absolute URL for an API path.
 *
 * `origin` is passed explicitly from the server side (where `window` does not
 * exist); in the browser it is read from `window.location.origin`.
 */
export function resolveUrl(path: string, origin?: string): string {
  const joined = `${API_BASE}${API_PREFIX}${path.startsWith('/') ? path : `/${path}`}`;
  if (/^https?:\/\//i.test(joined)) return joined;
  const base = origin ?? (typeof window !== 'undefined' ? window.location.origin : '');
  if (!base) return joined;
  return new URL(joined, base).toString();
}

export class ApiError extends Error {
  readonly status: number;
  readonly kind: 'service-missing' | 'service-error' | 'network' | 'unknown';
  readonly hint: string;

  constructor(
    message: string,
    status: number,
    kind: ApiError['kind'],
    hint: string,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.kind = kind;
    this.hint = hint;
  }
}

/** Turn a failed response into an error that says what to actually do. */
async function toApiError(response: Response): Promise<ApiError> {
  const contentType = response.headers.get('content-type') ?? '';
  const looksHtml = contentType.includes('text/html');
  let body = '';
  try {
    body = (await response.text()).slice(0, 400);
  } catch {
    body = '';
  }

  if (looksHtml) {
    return new ApiError(
      `The API returned an HTML page (HTTP ${response.status}) instead of JSON.`,
      response.status,
      'service-missing',
      'An HTML response means the request fell through to the Next.js app, so the ' +
        'Python service is not mounted on this deployment. Check that the Vercel ' +
        'project framework preset is "Services" (not "Next.js"), that Root Directory ' +
        'is the repository root, and that the root vercel.json declares the backend ' +
        'service. Locally, this means the dev proxy is not configured — start ' +
        'uvicorn and set NEXT_PUBLIC_API_BASE=http://localhost:8000.',
    );
  }

  if (response.status >= 500) {
    return new ApiError(
      `The API service is deployed but returned HTTP ${response.status}: ${body}`,
      response.status,
      'service-error',
      'The service is reachable, so this is a collection or code error rather than a ' +
        'routing problem. Query /api/health and /api/sources for the failing source.',
    );
  }

  return new ApiError(
    `Request failed with HTTP ${response.status}: ${body}`,
    response.status,
    'unknown',
    'Check the request parameters against /docs in the API service.',
  );
}

async function request<T>(path: string, origin?: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(resolveUrl(path, origin), {
      ...init,
      headers: { accept: 'application/json', ...(init?.headers ?? {}) },
      cache: 'no-store',
    });
  } catch (error) {
    throw new ApiError(
      error instanceof Error ? error.message : 'Network request failed',
      0,
      'network',
      'The request never reached a server. Confirm the API service is running and ' +
        'that NEXT_PUBLIC_API_BASE points at it.',
    );
  }

  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

/** Build a query string, skipping empty values. */
function qs(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      search.set(key, value.join(','));
    } else {
      search.set(key, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

// -- service -----------------------------------------------------------------

export const getHealth = (origin?: string) => request<ServiceHealth>('/health', origin);

export const getSources = (origin?: string) => request<SourceReport>('/sources', origin);

export const getModules = (origin?: string) =>
  request<{ count: number; modules: { id: string; name: string; description: string }[] }>(
    '/modules',
    origin,
  );

// -- the fourteen modules ----------------------------------------------------

export type ModuleParams = Record<string, unknown>;

export const getModule = <T extends Domain = Domain>(
  module: ModuleId,
  params: ModuleParams = {},
  origin?: string,
) =>
  request<ModuleResponse<T>>(`/${module}${qs(params)}`, origin);

export const getNewsDetail = (id: string, origin?: string) =>
  request<ModuleResponse<Domain>>(`/news/${encodeURIComponent(id)}`, origin);

export const getSignalDetail = (id: string, origin?: string) =>
  request<ModuleResponse<Domain>>(`/evidence${qs({ signal_id: id })}`, origin);

export const createWatchlist = (params: ModuleParams, origin?: string) =>
  request<ModuleResponse<Domain>>(`/watchlists${qs(params)}`, origin, { method: 'POST' });

export const deleteWatchlist = (id: string, origin?: string) =>
  request<ModuleResponse<Domain>>(`/watchlists/${encodeURIComponent(id)}`, origin, {
    method: 'DELETE',
  });

export const getDigest = (id: string, origin?: string) =>
  request<ModuleResponse<Domain>>(
    `/watchlists/${encodeURIComponent(id)}/digest`,
    origin,
  );

/** URL a browser can be pointed at to download an export. */
export const exportUrl = (module: ModuleId, format: ExportFormat = 'csv'): string =>
  resolveUrl(`/export${qs({ module, format })}`);
