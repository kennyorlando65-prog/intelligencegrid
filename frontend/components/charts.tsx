'use client';

/**
 * Chart wrappers.
 *
 * Two conventions applied throughout:
 *
 * * Charts are given a fixed aspect box so the layout does not jump when data
 *   arrives -- a dashboard that reflows as each panel resolves is unreadable
 *   while it is loading.
 * * Axis labels are short and the tooltip carries the units, because a
 *   truncated axis label is worse than no label.
 */

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

const AXIS = { stroke: '#5d7089', fontSize: 10 } as const;
const TOOLTIP_STYLE = {
  background: '#0b1119',
  border: '1px solid #2a3b4f',
  borderRadius: 6,
  fontSize: 11,
  color: '#e8eef7',
} as const;

export function FacetBar({
  data,
  height = 200,
  colour = '#38bdf8',
  layout = 'vertical',
}: {
  data: { name: string; value: number }[];
  height?: number;
  colour?: string;
  layout?: 'vertical' | 'horizontal';
}) {
  if (data.length === 0) return <ChartEmpty height={height} />;
  const vertical = layout === 'vertical';
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout={layout} margin={{ top: 4, right: 12, bottom: 4, left: 4 }}>
          {vertical ? (
            <>
              <XAxis type="number" {...AXIS} tickLine={false} axisLine={false} />
              <YAxis
                type="category"
                dataKey="name"
                width={110}
                {...AXIS}
                tickLine={false}
                axisLine={false}
              />
            </>
          ) : (
            <>
              <XAxis dataKey="name" {...AXIS} tickLine={false} axisLine={false} interval={0} />
              <YAxis {...AXIS} tickLine={false} axisLine={false} width={28} />
            </>
          )}
          <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: 'rgba(56,189,248,0.08)' }} />
          <Bar dataKey="value" fill={colour} radius={2} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TrendArea({
  data,
  dataKey = 'value',
  height = 180,
  colour = '#38bdf8',
  label = 'records',
}: {
  data: Record<string, unknown>[];
  dataKey?: string;
  height?: number;
  colour?: string;
  label?: string;
}) {
  if (data.length === 0) return <ChartEmpty height={height} />;
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -12 }}>
          <defs>
            <linearGradient id={`fill-${dataKey}-${colour.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colour} stopOpacity={0.35} />
              <stop offset="100%" stopColor={colour} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <XAxis dataKey="name" {...AXIS} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis {...AXIS} tickLine={false} axisLine={false} width={34} />
          <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(value: number) => [value, label]} />
          <Area
            type="monotone"
            dataKey={dataKey}
            stroke={colour}
            strokeWidth={1.5}
            fill={`url(#fill-${dataKey}-${colour.replace('#', '')})`}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export function MultiLine({
  data,
  series,
  height = 200,
}: {
  data: Record<string, unknown>[];
  series: { key: string; colour: string; label: string }[];
  height?: number;
}) {
  if (data.length === 0) return <ChartEmpty height={height} />;
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -12 }}>
          <XAxis dataKey="name" {...AXIS} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis {...AXIS} tickLine={false} axisLine={false} width={34} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Legend wrapperStyle={{ fontSize: 10, color: '#8fa3bd' }} />
          {series.map((line) => (
            <Line
              key={line.key}
              type="monotone"
              dataKey={line.key}
              name={line.label}
              stroke={line.colour}
              strokeWidth={1.5}
              dot={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

const SEVERITY_COLOURS: Record<string, string> = {
  CRITICAL: '#ff5470',
  HIGH: '#ffb020',
  MEDIUM: '#92400e',
  LOW: '#38bdf8',
  INFO: '#5d7089',
};

export function SeverityDonut({
  data,
  height = 180,
}: {
  data: { name: string; value: number }[];
  height?: number;
}) {
  const filtered = data.filter((row) => row.value > 0);
  if (filtered.length === 0) return <ChartEmpty height={height} />;
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={filtered}
            dataKey="value"
            nameKey="name"
            innerRadius="55%"
            outerRadius="82%"
            paddingAngle={2}
            stroke="#0b1119"
            strokeWidth={1}
          >
            {filtered.map((row) => (
              <Cell key={row.name} fill={SEVERITY_COLOURS[row.name] ?? '#5d7089'} />
            ))}
          </Pie>
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Legend wrapperStyle={{ fontSize: 10, color: '#8fa3bd' }} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

function ChartEmpty({ height }: { height: number }) {
  return (
    <div
      className="flex items-center justify-center rounded border border-dashed border-surface-400/60"
      style={{ height }}
    >
      <span className="text-2xs text-ink-faint">no data to plot</span>
    </div>
  );
}
