import React from 'react'

import {
  cleanRichText,
  getExecutiveLeadText,
  getModeCopy,
  getPresentationVariant,
  getPresentationVariantLabel,
  groupExecutiveSections,
} from '../messagePresentation'
import type { MessageRendererProps } from './types'

export const ReportLayout: React.FC<MessageRendererProps> = ({
  message,
}) => {
  const copy = getModeCopy(message)
  const variant = getPresentationVariant(message)
  const variantLabel = getPresentationVariantLabel(message)
  const grouping = groupExecutiveSections(message)
  const lead = getExecutiveLeadText(message)
  const headline = lead || copy.emptyState
  const implicationSections = grouping.priorities?.sections || []
  const actionSections = grouping.recommendedActions?.sections || []
  const watchSections = grouping.risks?.sections || []
  const detailSections = grouping.details.flatMap((group) => group.sections || [])
  const isDocumentVariant = variant === 'document'

  const renderBulletPanel = (eyebrow: string, heading: string, sections: typeof implicationSections) => {
    if (sections.length === 0) return null
    const items = sections.flatMap((section) => [
      cleanRichText(section.content || ''),
      ...(section.items || []).map((item) => cleanRichText(item)),
    ]).filter(Boolean).slice(0, 5)

    if (items.length === 0) return null

    return (
      <section className="executive-block">
        <div className="executive-block-header">
          <span className="eyebrow">{eyebrow}</span>
          <h4>{heading}</h4>
        </div>
        <ul className="executive-bullet-list">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>
    )
  }

  const renderDocumentPanel = (eyebrow: string, heading: string, sections: typeof implicationSections) => {
    if (sections.length === 0) return null
    const paragraphs = sections.flatMap((section) => [
      cleanRichText(section.content || ''),
      ...(section.items || []).map((item) => cleanRichText(item)),
    ]).filter(Boolean).slice(0, 5)

    if (paragraphs.length === 0) return null

    return (
      <section className="executive-block executive-block-document">
        <div className="executive-block-header">
          <span className="eyebrow">{eyebrow}</span>
          <h4>{heading}</h4>
          {variantLabel ? <span className="mode-variant-pill">{variantLabel}</span> : null}
        </div>
        <div className="report-document-paragraphs">
          {paragraphs.map((item) => (
            <p key={item}>{item}</p>
          ))}
        </div>
      </section>
    )
  }

  return (
    <div className="mode-renderer-shell mode-renderer-report">
      <div className="report-renderer">
        <section className="executive-block executive-block-highlight">
          <div className="executive-block-header">
            <span className="eyebrow">{copy.bottomLineLabel}</span>
            <h4>{headline}</h4>
            {variantLabel ? <span className="mode-variant-pill">{variantLabel}</span> : null}
          </div>
        </section>

        {isDocumentVariant
          ? (
            <>
              {renderDocumentPanel(copy.implicationLabel, 'What it means', implicationSections)}
              {renderDocumentPanel(copy.actionLabel, 'What to do', actionSections)}
              {renderDocumentPanel(copy.watchLabel, 'What to watch', watchSections)}
            </>
          )
          : (
            <>
              {renderBulletPanel(copy.implicationLabel, 'What it means', implicationSections)}
              {renderBulletPanel(copy.actionLabel, 'What to do', actionSections)}
              {renderBulletPanel(copy.watchLabel, 'What to watch', watchSections)}
            </>
          )
        }

        {detailSections.length > 0 ? (
          <section className="executive-note-panel">
            <span className="eyebrow">Supporting detail</span>
            <ul className="executive-bullet-list">
              {detailSections.slice(0, 3).map((section) => (
                <li key={section.label}>
                  <strong>{section.label}</strong>
                  {section.content ? <p>{cleanRichText(section.content)}</p> : null}
                  {section.items?.length ? <p>{section.items.map((item) => cleanRichText(item)).filter(Boolean).join(' · ')}</p> : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

      </div>
    </div>
  )
}
