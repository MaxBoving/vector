import React, { useState } from 'react'
import { Loader2 } from '../icons'

import { InlineResult } from './InlineResult'
import { useInlineAction } from './useInlineAction'
import type { AnswerSection, RichRenderer } from '../types'

// ── Types ────────────────────────────────────────────────────

type SectionItemProps = {
  section: AnswerSection
  renderRichContent: RichRenderer
  resolveLabel?: (section: AnswerSection) => string
  onInlineAction?: (prompt: string, intent: string) => Promise<string>
  onFollowUp?: (text: string) => void
  /** If true, render "Go deeper" action button (report layout) */
  allowExpand?: boolean
}

type SectionListProps = {
  sections: AnswerSection[]
  renderRichContent: RichRenderer
  resolveLabel?: (section: AnswerSection) => string
  onInlineAction?: (prompt: string, intent: string) => Promise<string>
  onFollowUp?: (text: string) => void
  allowExpand?: boolean
}

// ── Item list (priority / upcoming / null) ───────────────────

const ItemList: React.FC<{ section: AnswerSection; renderRichContent: RichRenderer }> = ({
  section,
  renderRichContent,
}) => (
  <>
    {section.content ? (
      <div className="section-item-prose">
        {renderRichContent(section.content) || <p className="section-item-copy">{section.content}</p>}
      </div>
    ) : null}
    {section.items && section.items.length > 0 ? (
      <ul className="section-item-list">
        {section.items.map((item) => (
          <li key={item} className="section-item-bullet">
            <span className="section-item-dot" aria-hidden="true" />
            {item}
          </li>
        ))}
      </ul>
    ) : null}
  </>
)

// ── Risk strip (risk) ────────────────────────────────────────

const RiskStrip: React.FC<{ section: AnswerSection; renderRichContent: RichRenderer }> = ({
  section,
  renderRichContent,
}) => (
  <>
    {section.content ? (
      <div className="section-item-prose">
        {renderRichContent(section.content) || <p className="section-item-copy">{section.content}</p>}
      </div>
    ) : null}
    {section.items && section.items.length > 0 ? (
      <ul className="section-item-list section-item-list-risk">
        {section.items.map((item) => (
          <li key={item} className="section-item-bullet section-item-bullet-risk">{item}</li>
        ))}
      </ul>
    ) : null}
  </>
)

// ── Action list (action) ─────────────────────────────────────

const ActionList: React.FC<{
  section: AnswerSection
  renderRichContent: RichRenderer
  onFollowUp?: (text: string) => void
}> = ({ section, renderRichContent, onFollowUp }) => (
  <>
    {section.content ? (
      <div className="section-item-prose">
        {renderRichContent(section.content) || <p className="section-item-copy">{section.content}</p>}
      </div>
    ) : null}
    {section.items && section.items.length > 0 ? (
      <div className="followup-chips section-item-list section-item-list-action">
        {section.items.map((item) =>
          onFollowUp ? (
            <button key={item} type="button" className="followup-chip section-item-bullet section-item-bullet-action section-item-bullet-clickable" onClick={() => onFollowUp(item)}>
              {item}
            </button>
          ) : (
            <span key={item} className="section-item-bullet section-item-bullet-action">{item}</span>
          ),
        )}
      </div>
    ) : null}
  </>
)

// ── Section type → label class ───────────────────────────────

const SECTION_LABEL_CLASSES: Record<string, string> = {
  risk:    'section-label-risk',
  action:  'section-label-action',
  upcoming: 'section-label-upcoming',
}

// ── SectionItem ──────────────────────────────────────────────

export const SectionItem: React.FC<SectionItemProps> = ({
  section,
  renderRichContent,
  resolveLabel,
  onInlineAction,
  onFollowUp,
  allowExpand = false,
}) => {
  const { status, result, trigger, dismiss } = useInlineAction(onInlineAction)
  const type = section.section_type ?? 'detail'
  const labelClass = SECTION_LABEL_CLASSES[type] ?? ''
  const displayLabel = resolveLabel ? resolveLabel(section) : section.label

  const body = (() => {
    if (type === 'risk') return <RiskStrip section={section} renderRichContent={renderRichContent} />
    if (type === 'action') return <ActionList section={section} renderRichContent={renderRichContent} onFollowUp={onFollowUp} />
    return <ItemList section={section} renderRichContent={renderRichContent} />
  })()

  return (
    <div className={`section-item section-item-${type}`}>
      <div className="section-item-header">
        <span className={`section-item-label ${labelClass}`}>{displayLabel}</span>
        {allowExpand && onInlineAction ? (
          <button
            type="button"
            className="section-item-expand"
            disabled={status === 'loading'}
            onClick={() =>
              trigger(
                'report-deeper',
                `Expand the "${displayLabel}" section with deeper analysis. Current content: ${section.content || ''} ${(section.items || []).join('. ')}`,
              )
            }
          >
            {status === 'loading' ? <Loader2 size={11} className="spin" /> : 'Go deeper'}
          </button>
        ) : null}
      </div>

      {body}

      {result ? <InlineResult label="Expanded Analysis" result={result} onDismiss={dismiss} /> : null}
    </div>
  )
}

// ── SectionList ──────────────────────────────────────────────

export const SectionList: React.FC<SectionListProps> = ({
  sections,
  renderRichContent,
  resolveLabel,
  onInlineAction,
  onFollowUp,
  allowExpand = false,
}) => {
  const [expandedDetails, setExpandedDetails] = useState(false)

  const primary = sections.filter((s) => s.section_type !== 'detail')
  const details = sections.filter((s) => s.section_type === 'detail')

  return (
    <div className="section-list">
      {primary.map((section) => (
        <SectionItem
          key={section.label}
          section={section}
          renderRichContent={renderRichContent}
          resolveLabel={resolveLabel}
          onInlineAction={onInlineAction}
          onFollowUp={onFollowUp}
          allowExpand={allowExpand}
        />
      ))}

      {details.length > 0 ? (
        <div className="section-list-details">
          <button
            type="button"
            className="section-list-details-toggle"
            onClick={() => setExpandedDetails((v) => !v)}
            aria-expanded={expandedDetails}
          >
            {expandedDetails ? 'Hide detail' : 'Show additional detail'}
          </button>
          {expandedDetails
            ? details.map((section) => (
                <SectionItem
                  key={section.label}
                  section={section}
                  renderRichContent={renderRichContent}
                  resolveLabel={resolveLabel}
                  onInlineAction={onInlineAction}
                  onFollowUp={onFollowUp}
                  allowExpand={allowExpand}
                />
              ))
            : null}
        </div>
      ) : null}
    </div>
  )
}
