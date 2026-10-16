'use client';

/**
 * Global map.
 *
 * ## Why centroids rather than country polygons
 *
 * A true choropleth needs country boundary geometry. The options were a
 * several-hundred-kilobyte GeoJSON fetched from a third-party CDN at runtime,
 * or a bundled copy of the same. Both add a hard external dependency and a
 * cold-start download to answer a question -- "which territories can we
 * actually see?" -- that centroid markers answer completely. The map renders
 * one marker per territory, sized and coloured by coverage confidence, with
 * event points layered over it.
 *
 * The colouring rule is deliberate and stated in the legend: shading encodes
 * **coverage confidence**, never event volume. A bright cell always means
 * "well observed". Volume is a separate, explicitly labelled encoding, so a
 * busy country and a well-covered country cannot be confused at a glance.
 *
 * ## Why Leaflet and not MapLibre
 *
 * Leaflet over raster tiles needs no GPU acceleration, works on any browser
 * without WebGL, and renders vector markers from the data directly. For
 * thousands of point markers that is the right trade; MapLibre's advantage
 * (vector-tile styling) buys nothing here.
 */

import { useMemo } from 'react';
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, Tooltip } from 'react-leaflet';

import 'leaflet/dist/leaflet.css';

import { classNames, count, relative, severityColor, truncate } from '@/lib/format';

export interface MapPoint {
  id: string;
  lat: number;
  lon: number;
  place?: string | null;
  country_iso2?: string | null;
  category?: string | null;
  severity?: string | null;
  confidence?: number | null;
  event_date?: string;
  source_url?: string | null;
}

export interface CoverageCell {
  country_iso2: string;
  country_name: string;
  events: number;
  news: number;
  coverage_confidence: number;
  data_state: string;
  centroid?: { lat: number; lon: number } | null;
}

export interface ClusterShape {
  cluster_id: number;
  count: number;
  centroid?: { lat: number; lon: number } | null;
  radius_km?: number;
  countries?: string[];
}

const SEVERITY_FILL: Record<string, string> = {
  CRITICAL: '#ff5470',
  HIGH: '#ffb020',
  MEDIUM: '#f59e0b',
  LOW: '#38bdf8',
  INFO: '#8fa3bd',
};

/** Confidence ramp: dim blue = unobserved, bright = well observed. */
function coverageFill(confidence: number): string {
  if (confidence >= 0.75) return '#34d399';
  if (confidence >= 0.5) return '#38bdf8';
  if (confidence >= 0.25) return '#0e7490';
  if (confidence > 0) return '#164e63';
  return '#334155';
}

export default function WorldMap({
  points,
  coverage,
  clusters,
  showCoverage = true,
  showPoints = true,
  showClusters = true,
  onSelectCountry,
  height = 520,
}: {
  points: MapPoint[];
  coverage: CoverageCell[];
  clusters?: ClusterShape[];
  showCoverage?: boolean;
  showPoints?: boolean;
  showClusters?: boolean;
  onSelectCountry?: (iso2: string) => void;
  height?: number;
}) {
  const cells = useMemo(
    () => coverage.filter((cell) => cell.centroid && cell.coverage_confidence >= 0),
    [coverage],
  );
  const placed = useMemo(
    () => points.filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon)),
    [points],
  );

  return (
    <div className="overflow-hidden rounded border border-surface-400/60" style={{ height }}>
      <MapContainer
        center={[12, 18]}
        zoom={2}
        minZoom={2}
        maxZoom={12}
        worldCopyJump
        style={{ height: '100%', width: '100%', background: '#070b11' }}
        preferCanvas
      >
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
          subdomains="abcd"
          maxZoom={19}
        />

        {showCoverage &&
          cells.map((cell) => {
            const coords = cell.centroid as { lat: number; lon: number };
            const radius = 4 + Math.min(16, Math.sqrt(cell.events + cell.news) * 1.4);
            return (
              <CircleMarker
                key={`cov-${cell.country_iso2}`}
                center={[coords.lat, coords.lon]}
                radius={radius}
                pathOptions={{
                  color: coverageFill(cell.coverage_confidence),
                  fillColor: coverageFill(cell.coverage_confidence),
                  fillOpacity: 0.35,
                  weight: 1,
                }}
                eventHandlers={{
                  click: () => onSelectCountry?.(cell.country_iso2),
                }}
              >
                <Tooltip direction="top" offset={[0, -4]} opacity={1}>
                  <span className="font-mono text-2xs">
                    {cell.country_name} · coverage {Math.round(cell.coverage_confidence * 100)}%
                  </span>
                </Tooltip>
                <Popup>
                  <div className="w-52 text-xs">
                    <div className="font-semibold">{cell.country_name}</div>
                    <dl className="mt-1 space-y-0.5 font-mono text-2xs">
                      <div className="flex justify-between">
                        <dt>coverage confidence</dt>
                        <dd>{Math.round(cell.coverage_confidence * 100)}%</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt>events</dt>
                        <dd>{count(cell.events)}</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt>articles</dt>
                        <dd>{count(cell.news)}</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt>state</dt>
                        <dd>{cell.data_state}</dd>
                      </div>
                    </dl>
                    <p className="mt-1 text-2xs leading-relaxed text-zinc-500">
                      Marker colour shows how well this territory is observed, not how much
                      happened in it.
                    </p>
                  </div>
                </Popup>
              </CircleMarker>
            );
          })}

        {showClusters &&
          (clusters ?? []).map((cluster) =>
            cluster.centroid ? (
              <CircleMarker
                key={`cluster-${cluster.cluster_id}`}
                center={[cluster.centroid.lat, cluster.centroid.lon]}
                radius={8 + Math.min(22, cluster.count * 1.5)}
                pathOptions={{
                  color: '#ffb020',
                  fillColor: '#ffb020',
                  fillOpacity: 0.12,
                  weight: 1,
                  dashArray: '3 3',
                }}
              >
                <Tooltip direction="top" opacity={1}>
                  <span className="font-mono text-2xs">
                    cluster of {cluster.count} · {Math.round(cluster.radius_km ?? 0)} km radius
                  </span>
                </Tooltip>
              </CircleMarker>
            ) : null,
          )}

        {showPoints &&
          placed.map((point) => (
            <CircleMarker
              key={point.id}
              center={[point.lat, point.lon]}
              radius={3.5}
              pathOptions={{
                color: SEVERITY_FILL[(point.severity ?? 'INFO').toUpperCase()] ?? '#8fa3bd',
                fillColor: SEVERITY_FILL[(point.severity ?? 'INFO').toUpperCase()] ?? '#8fa3bd',
                fillOpacity: 0.85,
                weight: 1,
              }}
            >
              <Popup>
                <div className="w-60 text-xs">
                  <div className="flex items-center gap-2">
                    <span
                      className={classNames(
                        'rounded border px-1 font-mono text-2xs',
                        severityColor(point.severity ?? undefined),
                      )}
                    >
                      {point.severity ?? 'INFO'}
                    </span>
                    <span className="text-2xs text-zinc-500">{point.category ?? 'uncategorised'}</span>
                  </div>
                  <div className="mt-1 font-medium">{point.place ?? 'location not stated'}</div>
                  <div className="mt-1 font-mono text-2xs text-zinc-500">
                    {point.country_iso2 ?? '—'} · {point.lat.toFixed(3)}, {point.lon.toFixed(3)}
                  </div>
                  <div className="text-2xs text-zinc-500">
                    recorded {relative(point.event_date)}
                    {point.confidence !== null && point.confidence !== undefined
                      ? ` · confidence ${Math.round(point.confidence * 100)}%`
                      : ''}
                  </div>
                  {point.source_url && (
                    <a
                      className="mt-1 inline-block text-2xs text-sky-400 underline"
                      href={point.source_url}
                      target="_blank"
                      rel="noreferrer noopener"
                    >
                      {truncate(point.source_url.replace(/^https?:\/\//, ''), 40)}
                    </a>
                  )}
                </div>
              </Popup>
            </CircleMarker>
          ))}

        {/* Cluster linkage drawn last so it sits above the coverage shading. */}
        {showClusters &&
          (clusters ?? [])
            .filter((c) => c.centroid)
            .slice(0, 12)
            .map((cluster, index, all) => {
              const next = all[(index + 1) % all.length];
              if (!cluster.centroid || !next?.centroid || all.length < 2) return null;
              return (
                <Polyline
                  key={`link-${cluster.cluster_id}`}
                  positions={[
                    [cluster.centroid.lat, cluster.centroid.lon],
                    [next.centroid.lat, next.centroid.lon],
                  ]}
                  pathOptions={{ color: '#ffb020', opacity: 0.22, weight: 1 }}
                />
              );
            })}
      </MapContainer>
    </div>
  );
}
