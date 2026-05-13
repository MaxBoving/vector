import React from 'react'
import { ArrowRight } from './icons'

type SparseEvidenceCardProps = {
  summary: string
  signals: string[]
  onFollowUp?: (text: string) => void
}

export const SparseEvidenceCard: React.FC<SparseEvidenceCardProps> = ({ summary, signals, onFollowUp }) => (
  <section className="sparse-evidence-card">
    <div className="executive-block-header">
      <span className="eyebrow">Need Clarification</span>
      <h4>Answer these to tighten the recommendation</h4>
    </div>
    <p>{summary}</p>
    {signals.length > 0 ? (
      onFollowUp ? (
        <div className="followup-chips">
          {signals.map((signal) => (
            <button key={signal} type="button" className="followup-chip" onClick={() => onFollowUp(signal)}>
              {signal}
              <ArrowRight size={12} className="followup-chip-icon" />
            </button>
          ))}
        </div>
      ) : (
        <ul className="executive-bullet-list">
          {signals.map((signal) => (
            <li key={signal}>{signal}</li>
          ))}
        </ul>
      )
    ) : null}
  </section>
)
