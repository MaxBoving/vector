import React from 'react'
import { Loader2 } from '../icons'

import { FinanceCharts } from './FinanceChart'
import { InlineResult } from './InlineResult'
import { cleanRichText, getExecutiveLeadText, getModeCopy, getPresentationVariantLabel } from '../messagePresentation'
import { useInlineAction } from './useInlineAction'
import type { MessageRendererProps } from './types'

// ── KPI cell ────────────────────────────────────────────────

type KpiCellProps = {
  label: string
  value: string
  onInlineAction?: MessageRendererProps['onInlineAction']
}

const KpiCell: React.FC<KpiCellProps> = ({ label, value, onInlineAction }) => {
  const { status, result, trigger, dismiss } = useInlineAction(onInlineAction)
  return (
    <div className="finance-kpi-cell">
      <span className="finance-kpi-label">{label}</span>
      <strong className="finance-kpi-value">{value}</strong>
      {onInlineAction ? (
        <button
          type="button"
          className="finance-kpi-action"
          disabled={status === 'loading'}
          onClick={() => trigger('finance-scenario', `Run a scenario for the metric "${label}" currently at ${value}. What happens if it changes by 10%, 20%, or goes to worst case?`)}
        >
          {status === 'loading' ? <Loader2 size={10} className="spin" /> : 'Scenario'}
        </button>
      ) : null}
      {result ? <InlineResult label="Scenario" result={result} onDismiss={dismiss} className="finance-inline-result" /> : null}
    </div>
  )
}

// ── Signal row ──────────────────────────────────────────────

type SignalRowProps = {
  index: number
  item: string
  onInlineAction?: MessageRendererProps['onInlineAction']
}

const SignalRow: React.FC<SignalRowProps> = ({ index, item, onInlineAction }) => {
  const { status, result, trigger, dismiss } = useInlineAction(onInlineAction)
  return (
    <>
      <li className="finance-signal-item">
        <span className="finance-signal-row-num" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
        <span className="finance-signal-text">{item}</span>
        {onInlineAction ? (
          <button
            type="button"
            className="finance-signal-drill"
            disabled={status === 'loading'}
            onClick={() => trigger('finance-drill', `Drill into this financial signal: "${item}". What is driving it, what is the risk or opportunity, and what should the CEO do?`)}
          >
            {status === 'loading' ? <Loader2 size={10} className="spin" /> : 'Drill down'}
          </button>
        ) : null}
      </li>
      {result ? (
        <li className="finance-signal-inline-result">
          <InlineResult label="Analysis" result={result} onDismiss={dismiss} className="finance-inline-result" />
        </li>
      ) : null}
    </>
  )
}

// ── Main renderer ───────────────────────────────────────────

export const FinanceRenderer: React.FC<MessageRendererProps> = ({ message, onInlineAction }) => {
  const copy = getModeCopy(message)
  const variantLabel = getPresentationVariantLabel(message)
  const finance = message.presentation?.finance
  const digest = finance || message.metadata.finance_digest
  const primaryVisual = finance?.primary_visual || message.metadata.primary_visual

  const headline = finance?.headline || getExecutiveLeadText(message)
  const keyMetrics = finance?.key_metrics?.length ? finance.key_metrics : []
  const charts = finance?.charts?.length ? finance.charts : []
  const takeaways = digest?.takeaways?.length ? digest.takeaways : message.answer.sections[0]?.items || []
  const implications = digest?.implications?.length ? digest.implications : message.answer.sections[1]?.items || []
  const recommendation = digest?.recommendation || message.answer.sections[2]?.items?.[0] || null
  const nextSteps = digest?.next_steps?.length
    ? digest.next_steps
    : (message.answer.sections[2]?.items || []).slice(1, 3)
  const thresholdEvents = finance?.threshold_events?.length ? finance.threshold_events : []

  return (
    <div className="mode-renderer-shell mode-renderer-finance">
      <div className="finance-renderer">
        <section className="executive-block executive-block-highlight">
          <div className="executive-block-header">
            <span className="eyebrow">{copy.bottomLineLabel}</span>
            {variantLabel ? <span className="mode-variant-pill">{variantLabel}</span> : null}
            <h4>{headline || copy.emptyState}</h4>
          </div>
          {keyMetrics.length > 0 ? (
            <div className="finance-kpi-strip">
              {keyMetrics.slice(0, 5).map((metric) => (
                <KpiCell key={`${metric.label}-${metric.value}`} label={metric.label} value={metric.value} onInlineAction={onInlineAction} />
              ))}
            </div>
          ) : null}
        </section>

        {charts.length > 0 ? <FinanceCharts charts={charts} /> : null}

        {takeaways.length > 0 ? (
          <div className="finance-signal-block">
            <span className="finance-signal-section-label">{copy.implicationLabel}</span>
            <ul className="finance-signal-list">
              {takeaways.slice(0, 5).map((item, i) => (
                <SignalRow key={item} index={i} item={item} onInlineAction={onInlineAction} />
              ))}
            </ul>

            {implications.length > 0 ? (
              <div className="finance-implication-block">
                <span className="finance-signal-section-label finance-signal-section-label--dim">{copy.implicationLabel}</span>
                <ul className="finance-implication-list">
                  {implications.slice(0, 3).map((item) => (
                    <li key={item} className="finance-implication-item">{cleanRichText(item)}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        {recommendation ? (
          <div className="finance-recommendation-callout">
            <span className="eyebrow">{copy.actionLabel}</span>
            <strong>{cleanRichText(recommendation)}</strong>
            {nextSteps.length > 0 ? (
              <div className="finance-next-steps">
                {nextSteps.map((item) => (
                  <span key={item} className="finance-next-step-pill">{cleanRichText(item)}</span>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {thresholdEvents.length > 0 ? (
          <div className="finance-threshold-strip">
            <span className="finance-threshold-label">{copy.watchLabel}</span>
            <ul className="finance-threshold-list">
              {thresholdEvents.slice(0, 3).map((item) => (
                <li key={item} className="finance-threshold-item">{cleanRichText(item)}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {primaryVisual?.title ? (
          <div className="finance-visual-row">
            <span>{primaryVisual.label || 'Model lens'}</span>
            <span aria-hidden="true">·</span>
            <strong>{primaryVisual.title}</strong>
            {primaryVisual.description ? <span>— {cleanRichText(primaryVisual.description)}</span> : null}
          </div>
        ) : null}

      </div>
    </div>
  )
}
