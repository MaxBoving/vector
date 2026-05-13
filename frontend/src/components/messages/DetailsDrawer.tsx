import React, { useState } from 'react'
import { ChevronRight } from './icons'

import { describeSourceRelevance, getVisibleAssumptions } from './messagePresentation'
import type { AssistantArtifact, AssistantMessage } from './types'

type DetailsDrawerProps = {
  message: AssistantMessage
  plannedExports: AssistantArtifact[]
}

export const DetailsDrawer: React.FC<DetailsDrawerProps> = ({ message, plannedExports }) => {
  const [open, setOpen] = useState(false)
  const generatedArtifacts = message.artifacts.filter((artifact) => artifact.status !== 'planned')
  const visibleAssumptions = getVisibleAssumptions(message)

  const hasDetails =
    message.sources.length > 0 ||
    visibleAssumptions.length > 0 ||
    message.trust.open_questions.length > 0 ||
    message.trust.missing_context.length > 0 ||
    generatedArtifacts.length > 0 ||
    plannedExports.length > 0

  if (!hasDetails) {
    return null
  }

  return (
    <section className="details-drawer">
      <button type="button" className="details-drawer-toggle" onClick={() => setOpen((current) => !current)}>
        <span className="eyebrow">More context</span>
        <span>{open ? 'Hide' : 'Sources, assumptions, and exports'}</span>
        <ChevronRight size={16} className={open ? 'toggle-open' : ''} />
      </button>

      {open ? (
        <div className="details-drawer-body">
          {message.sources.length > 0 ? (
            <div className="details-drawer-section">
              <span className="eyebrow">Sources</span>
              <div className="source-list">
                {message.sources.map((source) => (
                  <div key={source.source_id} className="source-card">
                    <div className="source-card-top">
                      <div className="source-chip">
                        <strong>{source.title}</strong>
                        <span>{source.role || source.type}</span>
                      </div>
                      <span className="source-relevance-label">{describeSourceRelevance(source, message)}</span>
                    </div>
                    {source.used_for && source.used_for.length > 0 ? (
                      <div className="source-actions">
                        {source.used_for.map((usage) => (
                          <span key={usage} className="finance-next-step-pill">
                            {usage.replace(/_/g, ' ')}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {source.snippet ? (
                      <div className="source-snippet-block">
                        <span className="eyebrow">Relevant excerpt</span>
                        <p className="source-snippet">{source.snippet}</p>
                      </div>
                    ) : null}
                    {source.confidence_impact ? <p className="executive-section-copy">Confidence impact: {source.confidence_impact}</p> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {visibleAssumptions.length > 0 ? (
            <div className="details-drawer-section">
              <span className="eyebrow">Assumptions To Confirm</span>
              <ul className="executive-bullet-list">
                {visibleAssumptions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {message.trust.open_questions.length > 0 ? (
            <div className="details-drawer-section">
              <span className="eyebrow">Open Questions</span>
              <ul className="executive-bullet-list">
                {message.trust.open_questions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {message.trust.missing_context.length > 0 ? (
            <div className="details-drawer-section">
              <span className="eyebrow">Missing Context</span>
              <ul className="executive-bullet-list">
                {message.trust.missing_context.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {generatedArtifacts.length > 0 ? (
            <div className="details-drawer-section">
              <span className="eyebrow">Artifact Presentation</span>
              <div className="export-chip-list">
                {generatedArtifacts.map((artifact) => {
                  const metadata = artifact.metadata || {}
                  const chips = [metadata.theme_id, metadata.template_id, metadata.presentation_version].filter(Boolean)
                  if (chips.length === 0) {
                    return null
                  }
                  return (
                    <div key={artifact.artifact_id} className="export-chip">
                      <strong>{artifact.label}</strong>
                      {chips.map((value) => (
                        <span key={String(value)}>{String(value).replace(/_/g, ' ')}</span>
                      ))}
                    </div>
                  )
                })}
              </div>
            </div>
          ) : null}

          {plannedExports.length > 0 ? (
            <div className="details-drawer-section">
              <span className="eyebrow">Pending Exports</span>
              <div className="export-chip-list">
                {plannedExports.map((artifact) => (
                  <div key={artifact.artifact_id} className="export-chip">
                    <strong>{artifact.label}</strong>
                    <span>{artifact.format ? `.${artifact.format}` : 'planned export'}</span>
                    {artifact.purpose ? <span>{artifact.purpose.replace(/_/g, ' ')}</span> : null}
                    {artifact.ready_when ? <span>{artifact.ready_when}</span> : null}
                    {artifact.blocking_reason ? <span>{artifact.blocking_reason}</span> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
