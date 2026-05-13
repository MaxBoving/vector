import React from 'react'
import { Loader2 } from '../icons'

import { InlineResult } from './InlineResult'
import { useInlineAction } from './useInlineAction'
import type { MessageRendererProps } from './types'

// ── Hero metric ─────────────────────────────────────────────

type HeroMetricProps = {
  label: string
  value: string
  delta?: string | null
}

const HeroMetric: React.FC<HeroMetricProps> = ({ label, value, delta }) => (
  <div className="canvas-renderer-hero">
    <span className="canvas-renderer-hero-label">{label}</span>
    <span className="canvas-renderer-hero-value">{value}</span>
    {delta ? <span className="canvas-renderer-hero-delta">{delta}</span> : null}
  </div>
)

// ── Section card ─────────────────────────────────────────────

type SectionCardProps = {
  label: string
  bullets?: string[]
  content?: string | null
  highlight?: boolean
  onInlineAction?: MessageRendererProps['onInlineAction']
}

const SectionCard: React.FC<SectionCardProps> = ({ label, bullets, content, highlight, onInlineAction }) => {
  const { status, result, trigger, dismiss } = useInlineAction(onInlineAction)

  return (
    <div className={`canvas-renderer-section${highlight ? ' canvas-renderer-section--highlight' : ''}`}>
      <div className="canvas-renderer-section-header">
        <h4 className="canvas-renderer-section-label">{label}</h4>
        {onInlineAction ? (
          <button
            type="button"
            className="canvas-renderer-section-action"
            disabled={status === 'loading'}
            onClick={() =>
              trigger(
                'canvas-expand',
                `Expand the "${label}" section of this executive one-pager with deeper analysis. Current content: ${
                  content || (bullets || []).join('. ')
                }`,
              )
            }
          >
            {status === 'loading' ? <Loader2 size={10} className="spin" /> : 'Expand'}
          </button>
        ) : null}
      </div>

      {content ? <p className="canvas-renderer-section-content">{content}</p> : null}
      {bullets && bullets.length > 0 ? (
        <ul className="canvas-renderer-bullets">
          {bullets.map((b) => (
            <li key={b} className="canvas-renderer-bullet">
              {b}
            </li>
          ))}
        </ul>
      ) : null}

      {result ? (
        <InlineResult label="Expanded" result={result} onDismiss={dismiss} className="canvas-renderer-inline" />
      ) : null}
    </div>
  )
}

// ── Main renderer ────────────────────────────────────────────

export const CanvasRenderer: React.FC<MessageRendererProps> = ({ message, onInlineAction }) => {
  const canvas = message.presentation?.canvas
  const summary = message.presentation?.summary || message.answer.summary

  // Fall back to answer sections when no canvas payload is present
  if (!canvas) {
    const sections = message.answer.sections || []
    return (
      <div className="mode-renderer-shell mode-renderer-canvas">
        {summary ? <p className="canvas-renderer-summary">{summary}</p> : null}
        <div className="canvas-renderer-grid">
          {sections.map((s) => (
            <SectionCard
              key={s.label}
              label={s.label}
              bullets={s.items}
              content={s.content}
              onInlineAction={onInlineAction}
            />
          ))}
        </div>
      </div>
    )
  }

  const { title, subtitle, hero_metric, sections = [], source_credit } = canvas

  return (
    <div className="mode-renderer-shell mode-renderer-canvas">
      {/* Header */}
      <div className="canvas-renderer-header">
        {title ? <h2 className="canvas-renderer-title">{title}</h2> : null}
        {subtitle ? <p className="canvas-renderer-subtitle">{subtitle}</p> : null}
      </div>

      {/* Hero metric */}
      {hero_metric ? (
        <HeroMetric
          label={hero_metric.label}
          value={hero_metric.value}
          delta={hero_metric.delta}
        />
      ) : null}

      {/* Summary */}
      {summary ? <p className="canvas-renderer-summary">{summary}</p> : null}

      {/* Sections grid */}
      {sections.length > 0 ? (
        <div className="canvas-renderer-grid">
          {sections.map((s) => (
            <SectionCard
              key={s.label}
              label={s.label}
              bullets={s.bullets}
              content={s.content}
              highlight={s.highlight}
              onInlineAction={onInlineAction}
            />
          ))}
        </div>
      ) : null}

      {/* Footer */}
      {source_credit ? (
        <div className="canvas-renderer-footer">
          <span className="canvas-renderer-source">Source: {source_credit}</span>
        </div>
      ) : null}
    </div>
  )
}
