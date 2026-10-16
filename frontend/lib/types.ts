/**
 * Shared response types.
 *
 * Kept deliberately loose at the leaf level: the backend's module payloads
 * are heterogeneous by design (a sanctions entity and a flight observation
 * share almost no fields), and generating a rigid type per module would mean
 * editing two languages every time a field is added for no real safety gain.
 * The envelope -- meta, severity, confidence, evidence -- IS typed strictly,
 * because that is the part every consumer relies on.
 */

export type Severity = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export type ModuleId =
  | 'news'
  | 'events'
  | 'signals'
  | 'threats'
  | 'sanctions'
  | 'flights'
  | 'maritime'
  | 'timeline'
  | 'watchlists'
  | 'evidence'
  | 'map'
  | 'entities'
  | 'backtest'
  | 'coverage';

export type ExportFormat = 'json' | 'csv';

/** Any module payload: a heterogeneous object with a strict meta envelope. */
export type Domain = Record<string, unknown>;

export interface DegradedSource {
  name: string;
  status: string;
  error: string;
}

/**
 * Operational context attached to every module response.
 *
 * This is the reason a reader can tell "nothing happened" apart from "we
 * cannot see". A payload without it would invite treating a partial view as
 * a complete one.
 */
export interface ResponseMeta {
  generated_at: string;
  data_built_at: string | null;
  sources_total: number;
  sources_usable: number;
  degraded: boolean;
  degraded_sources: DegradedSource[];
  warnings: string[];
  confidence_disclaimer: string;
  warmup_error?: string;
  [key: string]: unknown;
}

export interface ModuleResponse<T extends Domain = Domain> extends Domain {
  meta: ResponseMeta;
  /** The module's canonical row set, when it exposes one. */
  records?: T[];
  [key: string]: unknown;
}

export interface ServiceHealth {
  status: string;
  version: string;
  checked_at: string;
  datasets_built: boolean;
  dataset_count: number;
  sources: { registered: number; usable: number; degraded: boolean };
  last_run: Record<string, unknown>;
  note: string;
}

export interface SourceHealth {
  name: string;
  domain: string;
  status: string;
  is_usable: boolean;
  latency_ms?: number | null;
  record_count?: number;
  error?: string;
  last_success_at?: string | null;
  [key: string]: unknown;
}

export interface SourceReport {
  sources: SourceHealth[];
  total: number;
  usable: number;
  degraded: boolean;
  note?: string;
  store?: Record<string, unknown>;
  [key: string]: unknown;
}

/** A single evidence reference, the ground truth behind a signal. */
export interface EvidenceRef {
  source_name?: string;
  source_url?: string;
  published_at?: string | null;
  collected_at?: string;
  excerpt?: string;
  [key: string]: unknown;
}

export interface Signal {
  id: string;
  module: string;
  title: string;
  description: string;
  signal_type: string;
  severity: Severity;
  score: number;
  confidence: number;
  detected_at: string;
  window_start?: string | null;
  window_end?: string | null;
  countries: string[];
  continent?: string | null;
  metrics: Record<string, unknown>;
  caveats: string[];
  evidence: EvidenceRef[];
}
