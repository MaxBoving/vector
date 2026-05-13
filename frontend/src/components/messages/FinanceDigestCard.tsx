import React from 'react'

import type { AssistantMessage } from './types'

type FinanceDigestCardProps = {
  message: AssistantMessage
}

export const FinanceDigestCard: React.FC<FinanceDigestCardProps> = ({ message }) => {
  const finance = message.presentation?.finance
  const digest = finance || message.metadata.finance_digest
  const primaryVisual = finance?.primary_visual || message.metadata.primary_visual
  const takeaways = digest?.takeaways?.length ? digest.takeaways : message.answer.sections[0]?.items || []
  const implications = digest?.implications?.length ? digest.implications : message.answer.sections[1]?.items || []
  const recommendation = digest?.recommendation || message.answer.sections[2]?.items?.[0] || null
  const nextSteps = digest?.next_steps?.length ? digest.next_steps : (message.answer.sections[2]?.items || []).slice(1, 3)
  const keyMetrics = finance?.key_metrics?.length ? finance.key_metrics : []
  const thresholdEvents = finance?.threshold_events?.length ? finance.threshold_events : []

  return (
    <div className="finance-digest-card">
      {keyMetrics.length > 0 ? (
        <section className="executive-block">
          <div className="executive-block-header">
            <span className="eyebrow">Snapshot</span>
            <h4>Key financial metrics</h4>
          </div>
          <div className="finance-takeaway-grid">
            {keyMetrics.slice(0, 4).map((metric) => (
              <div key={`${metric.label}-${metric.value}`} className="finance-takeaway-card">
                <span className="finance-metric-label">{metric.label}</span>
                <strong>{metric.value}</strong>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {takeaways.length > 0 ? (
        <section className="executive-block">
          <div className="executive-block-header">
            <span className="eyebrow">Priorities</span>
            <h4>Top financial takeaways</h4>
          </div>
          <div className="finance-takeaway-grid">
            {takeaways.slice(0, 3).map((item) => (
              <div key={item} className="finance-takeaway-card">
                <p>{item}</p>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {implications.length > 0 ? (
        <section className="executive-block">
          <div className="executive-block-header">
            <span className="eyebrow">Why It Matters</span>
            <h4>Business implications</h4>
          </div>
          <ul className="executive-bullet-list">
            {implications.slice(0, 3).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {thresholdEvents.length > 0 ? (
        <section className="executive-note-panel">
          <span className="eyebrow">Threshold Events</span>
          <strong>Watch items that change the decision</strong>
          <ul className="executive-bullet-list">
            {thresholdEvents.slice(0, 3).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {recommendation ? (
        <section className="recommendation-callout">
          <span className="eyebrow">Recommended Action</span>
          <strong>{recommendation}</strong>
          {nextSteps.length > 0 ? (
            <div className="finance-next-steps">
              {nextSteps.map((item) => (
                <span key={item} className="finance-next-step-pill">
                  {item}
                </span>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {primaryVisual?.title ? (
        <section className="executive-note-panel">
          <span className="eyebrow">{primaryVisual.label || 'Model Lens'}</span>
          <strong>{primaryVisual.title}</strong>
          {primaryVisual.description ? <p>{primaryVisual.description}</p> : null}
        </section>
      ) : null}
    </div>
  )
}
