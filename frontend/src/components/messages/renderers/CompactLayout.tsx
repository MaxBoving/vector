import React from 'react'
import { Loader2 } from '../icons'
import { ArrowRight } from '../icons'

import { getModeCopy, getPresentationVariant } from '../messagePresentation'
import { InlineResult } from './InlineResult'
import { SectionList } from './SectionList'
import { useInlineAction } from './useInlineAction'
import type { MessageRendererProps } from './types'

// Email workflows that render the ranked-inbox UX.
// This fork is on workflow_type (a backend-stamped type), not derived from content.
const EMAIL_WORKFLOWS = new Set(['email_watcher', 'email_ingestion', 'weekly_recap'])

const getBriefSectionLabel = (section: { section_type?: string | null; label: string }) => {
  if (section.section_type === 'priority') return 'Priority'
  if (section.section_type === 'action') return 'Recommended action'
  if (section.section_type === 'risk') return 'Risk'
  if (section.section_type === 'upcoming') return 'Upcoming'
  if (section.section_type === 'detail') return 'Context'
  return section.label
}

function gmailThreadUrl(threadId: string) {
  return `https://mail.google.com/mail/u/0/#all/${threadId}`
}

// ── Email ranked item ────────────────────────────────────────

type EmailItemProps = {
  rank: number
  item: string
  subject?: string
  threadId?: string
  onInlineAction?: (prompt: string, intent: string) => Promise<string>
}

const EmailRankedItem: React.FC<EmailItemProps> = ({ rank, item, subject, threadId, onInlineAction }) => {
  const { status, activeIntent, result, trigger, dismiss } = useInlineAction(onInlineAction)

  const draftPrompt = threadId
    ? `Draft a concise CEO reply to the email thread "${subject}" (thread ID: ${threadId}). Context: ${item}`
    : `Draft a concise CEO reply to the email "${subject}". Context: ${item}`

  const summarizePrompt = threadId
    ? `Summarize the email thread "${subject}" (thread ID: ${threadId}). Context: ${item}`
    : `Summarize the email "${subject}". Context: ${item}`

  return (
    <li className="brief-email-ranked-item">
      <span className="brief-email-rank">{rank}</span>
      <span className="brief-email-ranked-body">
        <span className="brief-email-ranked-text">{item}</span>

        <span className="brief-email-ranked-actions">
          {threadId ? (
            <a href={gmailThreadUrl(threadId)} target="_blank" rel="noreferrer" className="brief-email-action-link">
              Open ↗
            </a>
          ) : null}

          {subject && onInlineAction ? (
            <>
              <button
                type="button"
                className={`brief-email-action-draft ${status === 'loading' && activeIntent === 'draft-reply' ? 'brief-email-action-active' : ''}`}
                disabled={status === 'loading'}
                onClick={() => trigger('draft-reply', draftPrompt)}
              >
                {status === 'loading' && activeIntent === 'draft-reply' ? <Loader2 size={11} className="spin" /> : 'Draft reply'}
              </button>
              <button
                type="button"
                className={`brief-email-action-draft ${status === 'loading' && activeIntent === 'summarize-email' ? 'brief-email-action-active' : ''}`}
                disabled={status === 'loading'}
                onClick={() => trigger('summarize-email', summarizePrompt)}
              >
                {status === 'loading' && activeIntent === 'summarize-email' ? <Loader2 size={11} className="spin" /> : 'Summarize'}
              </button>
            </>
          ) : null}
        </span>

        {result ? (
          <InlineResult
            label={activeIntent === 'draft-reply' ? 'Draft reply' : 'Summary'}
            result={result}
            onDismiss={dismiss}
          />
        ) : null}
      </span>
    </li>
  )
}

// ── CompactLayout ────────────────────────────────────────────

export const CompactLayout: React.FC<MessageRendererProps> = ({
  message,
  summary,
  renderRichContent,
  onInlineAction,
  onFollowUp,
}) => {
  const copy = getModeCopy(message)
  const variant = getPresentationVariant(message)
  const isNarrativeRecap = variant === 'narrative_recap'
  const isEmailBrief = EMAIL_WORKFLOWS.has(message.workflow_type)
  const sections = message.answer.sections
  const inboxSources = message.sources.filter((s) => s.role === 'inbox_signal')

  // Email path: thread items come from priority sections, follow-ups from action sections.
  // Read directly from section_type — no grouping intermediary.
  const threadItems = isEmailBrief
    ? sections.filter((s) => s.section_type === 'priority').flatMap((s) => s.items || []).slice(0, 8)
    : []

  // Suppress inline follow-ups when the parent renders clickable chips via BriefFollowUpPanel
  const followUpItems = isEmailBrief && !onFollowUp
    ? sections.filter((s) => s.section_type === 'action').flatMap((s) => s.items || []).slice(0, 5)
    : []

  const renderNarrativeSection = (section: { label: string; content?: string | null; items?: string[] | undefined }) => {
    const prose = section.content || (section.items || []).join('. ')
    if (!prose) {
      return null
    }
    return (
      <section key={section.label} className="brief-narrative-section">
        <span className="brief-narrative-section-label">{getBriefSectionLabel(section)}</span>
        <div className="brief-narrative-section-body">
          {renderRichContent(prose) || <p className="brief-narrative-section-copy">{prose}</p>}
        </div>
      </section>
    )
  }

  return (
    <div className="mode-renderer-shell mode-renderer-brief">
      <div className="brief-renderer">
        <div className="brief-renderer-summary">
          <span className="brief-renderer-summary-label">{copy.compactLeadLabel}</span>
          {renderRichContent(summary) || <p>{summary}</p>}
        </div>

        {isNarrativeRecap ? (
          sections.length > 0 ? (
            <div className="brief-renderer-narrative">
              {sections.map((section) => renderNarrativeSection(section))}
            </div>
          ) : null
        ) : isEmailBrief ? (
          <>
            {threadItems.length > 0 ? (
              <div className="brief-email-ranked">
                <span className="brief-email-ranked-label">Priority Inbox — Top {threadItems.length}</span>
                <ol className="brief-email-ranked-list">
                  {threadItems.map((item, i) => {
                    const source = inboxSources[i]
                    return (
                      <EmailRankedItem
                        key={item}
                        rank={i + 1}
                        item={item}
                        subject={source?.title}
                        threadId={source?.thread_id ?? undefined}
                        onInlineAction={onInlineAction}
                      />
                    )
                  })}
                </ol>
              </div>
            ) : null}

            {followUpItems.length > 0 ? (
              <div className="brief-followup-strip">
                <span className="brief-followup-label">Suggested Follow-Ups</span>
                <div className="followup-chips brief-followup-chips">
                  {followUpItems.map((item) =>
                    onFollowUp ? (
                      <button key={item} type="button" className="followup-chip" onClick={() => onFollowUp(item)}>
                        {item}
                        <ArrowRight size={12} className="followup-chip-icon" />
                      </button>
                    ) : (
                      <span key={item} className="brief-followup-item">{item}</span>
                    ),
                  )}
                </div>
              </div>
            ) : null}
          </>
        ) : (
          sections.length > 0 ? (
            <div className="brief-renderer-sections">
              <SectionList
                sections={sections}
                renderRichContent={renderRichContent}
                resolveLabel={getBriefSectionLabel}
                onInlineAction={onInlineAction}
                onFollowUp={onFollowUp}
                allowExpand={false}
              />
            </div>
          ) : null
        )}
      </div>
    </div>
  )
}
