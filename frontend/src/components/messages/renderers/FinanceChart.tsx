import React from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from 'recharts'

// ── Types ────────────────────────────────────────────────────

type ChartDataPoint = {
  metric: string
  actual?: number | null
  budget?: number | null
  forecast?: number | null
}

type FinanceChartSpec = {
  title: string
  type: string
  group?: string
  data: ChartDataPoint[]
}

type Props = {
  charts: FinanceChartSpec[]
}

// ── Palette ──────────────────────────────────────────────────

const GROUP_COLORS: Record<string, { actual: string; forecast: string; budget: string }> = {
  revenue: {
    actual: '#2f825a',
    forecast: 'rgba(47,130,90,0.28)',
    budget: 'rgba(47,130,90,0.14)',
  },
  cost: {
    actual: '#c0652b',
    forecast: 'rgba(192,101,43,0.28)',
    budget: 'rgba(192,101,43,0.14)',
  },
  capital: {
    actual: '#3a6ea8',
    forecast: 'rgba(58,110,168,0.28)',
    budget: 'rgba(58,110,168,0.14)',
  },
}

const DEFAULT_COLORS = GROUP_COLORS.revenue

// ── Helpers ──────────────────────────────────────────────────

function shortLabel(metric: string): string {
  return metric
    .replace(/\s+revenue$/i, '')
    .replace(/\s+cost$/i, '')
    .replace(/\s+spend$/i, '')
    .replace(/North America/i, 'NA')
    .replace(/Asia Pacific/i, 'APAC')
    .trim()
}

function formatValue(val: number): string {
  if (Math.abs(val) >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`
  if (Math.abs(val) >= 1_000) return `$${(val / 1_000).toFixed(0)}K`
  return `$${val.toFixed(1)}`
}

// ── Custom tooltip ───────────────────────────────────────────

type TooltipProps = {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: string
}

const ChartTooltip: React.FC<TooltipProps> = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="finance-chart-tooltip">
      <span className="finance-chart-tooltip-label">{label}</span>
      {payload.map((entry) => (
        <div key={entry.name} className="finance-chart-tooltip-row">
          <span className="finance-chart-tooltip-dot" style={{ background: entry.color }} />
          <span>{entry.name}</span>
          <strong>{formatValue(entry.value)}</strong>
        </div>
      ))}
    </div>
  )
}

// ── Single chart ─────────────────────────────────────────────

const SingleChart: React.FC<{ spec: FinanceChartSpec }> = ({ spec }) => {
  const palette = GROUP_COLORS[spec.group ?? ''] ?? DEFAULT_COLORS

  // Compute forecast_delta = max(0, forecast - actual) for stacked overlay
  const chartData = spec.data.map((d) => ({
    ...d,
    metric: shortLabel(d.metric),
    actual: d.actual ?? 0,
    budget: d.budget ?? 0,
    forecast_delta: Math.max(0, (d.forecast ?? 0) - (d.actual ?? 0)),
  }))

  const hasForecasts = chartData.some((d) => d.forecast_delta > 0)
  const hasBudget = chartData.some((d) => d.budget > 0)

  return (
    <div className="finance-chart-block">
      <span className="finance-chart-title">{spec.title}</span>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart
          data={chartData}
          margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
          barCategoryGap="28%"
          barGap={3}
        >
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="metric"
            tick={{ fontSize: 11, fill: 'var(--text-muted)', fontFamily: 'inherit' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tickFormatter={formatValue}
            tick={{ fontSize: 10, fill: 'var(--text-muted)', fontFamily: 'IBM Plex Mono, monospace' }}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: 'var(--surface-hover, rgba(0,0,0,0.04))' }} />
          <Legend
            iconType="square"
            iconSize={8}
            wrapperStyle={{ fontSize: '11px', color: 'var(--text-muted)', paddingTop: '6px' }}
          />

          {/* Actual — bottom of the stacked pair */}
          <Bar dataKey="actual" name="Actual" stackId="actforecast" fill={palette.actual} radius={[0, 0, 2, 2]} maxBarSize={32} />

          {/* Forecast delta — stacked on top of actual */}
          {hasForecasts ? (
            <Bar dataKey="forecast_delta" name="Forecast ↑" stackId="actforecast" fill={palette.forecast} radius={[2, 2, 0, 0]} maxBarSize={32} />
          ) : null}

          {/* Budget — separate adjacent bar */}
          {hasBudget ? (
            <Bar dataKey="budget" name="Budget" fill={palette.budget} stroke={palette.actual} strokeWidth={1} radius={[2, 2, 2, 2]} maxBarSize={32} />
          ) : null}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Main export ──────────────────────────────────────────────

export const FinanceCharts: React.FC<Props> = ({ charts }) => {
  if (!charts.length) return null
  return (
    <div className="finance-charts-row">
      {charts.map((spec) => (
        <SingleChart key={spec.title} spec={spec} />
      ))}
    </div>
  )
}
