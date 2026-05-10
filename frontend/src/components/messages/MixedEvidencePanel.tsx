import React, { useState } from 'react'
import { ArrowRight, ChevronDown } from './icons'
import { summarizeTrustLanguage } from './messagePresentation'
import type { AssistantMessage } from './types'

type QuestionOption = {
  label: string
  value: string
  apply_text: string
  description?: string | null
}

type QuestionEntry = {
  question: string
  priority_score?: number
  options: QuestionOption[]
}

// Build QuestionEntry list: use question_options from trust if present,
// filtering out action_offer entries (those are rendered by ProactiveOfferCard).
// Falls back to bare open_questions with no options.
function buildEntries(message: AssistantMessage): QuestionEntry[] {
  const qo = message.trust.question_options
  if (qo && qo.length > 0) {
    const clarifications = qo.filter((q) => q.offer_type !== 'action_offer')
    if (clarifications.length > 0) return clarifications.slice(0, 1)
  }
  // Fallback: bare questions, no options — still better than nothing
  return message.trust.open_questions.slice(0, 4).map((q) => ({ question: q, options: [] }))
}

type MixedEvidencePanelProps = {
  message: AssistantMessage
  onFollowUp?: (text: string) => void
}

export const MixedEvidencePanel: React.FC<MixedEvidencePanelProps> = ({ message, onFollowUp }) => {
  const [panelOpen, setPanelOpen] = useState(true)
  const [expandedIndex, setExpandedIndex] = useState<number | null>(0)
  const [answered, setAnswered] = useState<Record<number, string>>({})

  const entries = buildEntries(message)
  const assumptions = message.trust.assumptions.slice(0, 3)
  const gaps = message.trust.missing_context.slice(0, 3)
  const badge = summarizeTrustLanguage(message)

  if (entries.length === 0 && assumptions.length === 0 && gaps.length === 0) return null

  const handleAnswer = (index: number, option: QuestionOption) => {
    setAnswered((prev) => ({ ...prev, [index]: option.label }))
    setExpandedIndex(null)
    if (onFollowUp) onFollowUp(option.apply_text)
  }

  const handleBareQuestion = (index: number, question: string) => {
    setAnswered((prev) => ({ ...prev, [index]: 'Sent' }))
    setExpandedIndex(null)
    if (onFollowUp) onFollowUp(question)
  }

  return (
    <section className="mixed-evidence-panel">
      {/* ── Panel header ──────────────────────────────────── */}
      <button
        type="button"
        className="mixed-evidence-header"
        onClick={() => setPanelOpen((v) => !v)}
        aria-expanded={panelOpen}
      >
        <div className="mixed-evidence-header-left">
          <span className="mixed-evidence-label">Incomplete picture</span>
          {badge ? <span className="mixed-evidence-badge">{badge}</span> : null}
        </div>
        <ChevronDown
          size={14}
          className={`mixed-evidence-chevron${panelOpen ? ' mixed-evidence-chevron-open' : ''}`}
        />
      </button>

      {panelOpen ? (
        <div className="mixed-evidence-body">

          {/* ── Open questions — inline expand-and-select ─── */}
          {entries.length > 0 ? (
            <div className="mixed-evidence-group">
              <span className="mixed-evidence-group-label">
                Answer to sharpen the response
              </span>
              <div className="mixed-evidence-questions">
                {entries.map((entry, i) => {
                  const isExpanded = expandedIndex === i
                  const answeredLabel = answered[i]
                  const hasOptions = entry.options.length > 0

                  return (
                    <div
                      key={entry.question}
                      className={`meq-item${isExpanded ? ' meq-item-open' : ''}${answeredLabel ? ' meq-item-answered' : ''}`}
                    >
                      {/* Question row */}
                      <button
                        type="button"
                        className="meq-question-row"
                        onClick={() => {
                          if (answeredLabel) return
                          if (!hasOptions) {
                            handleBareQuestion(i, entry.question)
                            return
                          }
                          setExpandedIndex(isExpanded ? null : i)
                        }}
                        disabled={!!answeredLabel}
                      >
                        <span className="meq-question-index">{String(i + 1).padStart(2, '0')}</span>
                        <span className="meq-question-text">{entry.question}</span>
                        <span className="meq-question-trail">
                          {answeredLabel
                            ? <span className="meq-answered-label">{answeredLabel}</span>
                            : hasOptions
                              ? <ChevronDown size={12} className={`meq-chevron${isExpanded ? ' meq-chevron-open' : ''}`} />
                              : <ArrowRight size={12} className="meq-arrow" />
                          }
                        </span>
                      </button>

                      {/* Inline option chips */}
                      {isExpanded && hasOptions ? (
                        <div className="meq-options">
                          {entry.options.map((opt) => (
                            <button
                              key={opt.value}
                              type="button"
                              className="meq-option"
                              onClick={() => handleAnswer(i, opt)}
                            >
                              <span className="meq-option-label">{opt.label}</span>
                              {opt.description
                                ? <span className="meq-option-desc">{opt.description}</span>
                                : null}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  )
                })}
              </div>
            </div>
          ) : null}

          {/* ── Assumptions ──────────────────────────────── */}
          {assumptions.length > 0 ? (
            <div className="mixed-evidence-group">
              <span className="mixed-evidence-group-label">Assumptions — click to confirm or correct</span>
              {onFollowUp ? (
                <div className="followup-chips">
                  {assumptions.map((a) => (
                    <button
                      key={a}
                      type="button"
                      className="followup-chip followup-chip-assumption"
                      onClick={() => onFollowUp(`Is this assumption correct? "${a}"`)}
                    >
                      {a}
                      <ArrowRight size={12} className="followup-chip-icon" />
                    </button>
                  ))}
                </div>
              ) : (
                <ul className="mixed-evidence-list">
                  {assumptions.map((a) => <li key={a}>{a}</li>)}
                </ul>
              )}
            </div>
          ) : null}

          {/* ── Context gaps ─────────────────────────────── */}
          {gaps.length > 0 ? (
            <div className="mixed-evidence-group">
              <span className="mixed-evidence-group-label">Context gaps</span>
              <ul className="mixed-evidence-list mixed-evidence-gaps">
                {gaps.map((g) => (
                  <li key={g}>
                    {g}
                    {onFollowUp ? (
                      <button
                        type="button"
                        className="mixed-evidence-gap-add"
                        onClick={() => onFollowUp(`I can add context: ${g}`)}
                      >
                        Add context
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
