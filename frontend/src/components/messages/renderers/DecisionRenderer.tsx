import React from 'react'

import { getModeCopy, getPresentationVariantLabel } from '../messagePresentation'
import type { MessageRendererProps } from './types'

export const DecisionRenderer: React.FC<MessageRendererProps> = ({ message, summary }) => {
  const decision = message.presentation?.decision
  const copy = getModeCopy(message)
  const variantLabel = getPresentationVariantLabel(message)
  const gateOptions = message.metadata.gate?.options || []
  const options = (decision?.options?.length ? decision.options : gateOptions).map((option) => ({
    label: option.label || 'Resolve',
    decision: option.decision,
    mode: option.mode,
    description: 'description' in option ? option.description || undefined : undefined,
  }))

  return (
    <div className="mode-renderer-shell mode-renderer-decision">
      <div className="decision-renderer">
        <div className="decision-renderer-summary-block">
          <span className="decision-renderer-summary-label">{copy.bottomLineLabel}</span>
          {variantLabel ? <span className="mode-variant-pill">{variantLabel}</span> : null}
          <p className="decision-renderer-summary">
            {decision?.decision_summary || summary || copy.emptyState}
          </p>
        </div>

        {decision?.recommended_option ? (
          <div className="decision-renderer-recommended">
            <span className="decision-renderer-recommended-label">{copy.actionLabel}</span>
            <span>{decision.recommended_option}</span>
          </div>
        ) : null}

        {(decision?.impact_if_approved || decision?.impact_if_rejected) ? (
          <div className="decision-consequence-grid">
            {decision.impact_if_approved ? (
              <div className="decision-consequence decision-consequence-approved">
                <span className="decision-consequence-header">If approved</span>
                <p>{decision.impact_if_approved}</p>
              </div>
            ) : null}
            {decision.impact_if_rejected ? (
              <div className="decision-consequence decision-consequence-rejected">
                <span className="decision-consequence-header">If rejected</span>
                <p>{decision.impact_if_rejected}</p>
              </div>
            ) : null}
          </div>
        ) : null}

        {options.length > 0 ? (
          <div className="decision-options">
            {options.map((option) => (
              <div key={`${option.label}-${option.mode || 'default'}`} className="decision-option">
                <strong>{option.label}</strong>
                {option.description ? <p>{option.description}</p> : null}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
