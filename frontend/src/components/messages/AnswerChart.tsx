import React from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts'

import type { ChartSpec } from './types'

// ── Palette ──────────────────────────────────────────────────

const SCHEME_COLORS: Record<string, string[]> = {
  pipeline: ['#3a6ea8', '#4d85c5', '#6099d0', '#74aed8', '#88c2e0'],
  finance:  ['#2f825a', '#3a9e6e', '#4db882', '#60cc96', '#73e0aa'],
  neutral:  ['#6b7280', '#7f8c9a', '#93a0ad', '#a8b4bf', '#bcc8d2'],
}

// ── Value formatting ─────────────────────────────────────────

function formatValue(value: number, fmt: ChartSpec['value_format']): string {
  if (fmt === 'currency') {
    if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`
    if (Math.abs(value) >= 1_000)     return `$${(value / 1_000).toFixed(0)}K`
    return `$${value.toFixed(0)}`
  }
  if (fmt === 'percent') return `${value.toFixed(1)}%`
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (Math.abs(value) >= 1_000)     return `${(value / 1_000).toFixed(0)}K`
  return String(value)
}

// ── Custom tooltip ───────────────────────────────────────────

type TooltipProps = {
  active?: boolean
  payload?: Array<{ value: number }>
  label?: string
  valueFormat: ChartSpec['value_format']
}

const ChartTooltip: React.FC<TooltipProps> = ({ active, payload, label, valueFormat }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="answer-chart-tooltip">
      <span className="answer-chart-tooltip-label">{label}</span>
      <strong>{formatValue(payload[0].value, valueFormat)}</strong>
    </div>
  )
}

// ── Main component ───────────────────────────────────────────

type Props = {
  chart: ChartSpec
}

export const AnswerChart: React.FC<Props> = ({ chart }) => {
  const colors = SCHEME_COLORS[chart.color_scheme] ?? SCHEME_COLORS.neutral

  return (
    <div className="answer-chart-block">
      {chart.title ? <span className="answer-chart-title">{chart.title}</span> : null}
      {chart.subtitle ? <p className="answer-chart-subtitle">{chart.subtitle}</p> : null}
      <ResponsiveContainer width="100%" height={160}>
        <BarChart
          data={chart.data}
          margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
          barCategoryGap="32%"
        >
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11, fill: 'var(--text-muted)', fontFamily: 'inherit' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tickFormatter={(v) => formatValue(v, chart.value_format)}
            tick={{ fontSize: 10, fill: 'var(--text-muted)', fontFamily: 'IBM Plex Mono, monospace' }}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <Tooltip content={<ChartTooltip valueFormat={chart.value_format} />} cursor={{ fill: 'var(--surface-hover, rgba(0,0,0,0.04))' }} />
          <Bar dataKey="value" maxBarSize={36} radius={[3, 3, 0, 0]}>
            {chart.data.map((_, i) => (
              <Cell key={i} fill={colors[i % colors.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
