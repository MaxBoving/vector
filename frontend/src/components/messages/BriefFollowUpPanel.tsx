import React, { useState } from 'react'
import { ArrowRight, ChevronDown } from './icons'
import type { AssistantMessage } from './types'

type Props = {
  message: AssistantMessage
  onFollowUp: (text: string) => void
}

export const BriefFollowUpPanel: React.FC<Props> = ({ message, onFollowUp }) => {
  const [open, setOpen] = useState(true)
  const chips = message.answer.follow_ups ?? []

  if (!chips.length) return null

  return (
    <section className="brief-followup-panel">
      <button
        type="button"
        className="brief-followup-header"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <strong>Dig deeper or take action</strong>
        <ChevronDown size={13} className={`brief-followup-chevron${open ? ' brief-followup-chevron-open' : ''}`} />
      </button>

      {open ? (
        <div className="followup-chips brief-followup-chips">
          {chips.map((chip) => (
            <button
              key={chip.label}
              type="button"
              className="followup-chip"
              onClick={() => onFollowUp(chip.prompt)}
            >
              {chip.label}
              <ArrowRight size={12} className="followup-chip-icon" />
            </button>
          ))}
        </div>
      ) : null}
    </section>
  )
}
