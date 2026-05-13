import React, { useState } from 'react'
import { ArrowRight } from './icons'
import type { AssistantMessage } from './types'

type Offer = {
  question: string
  options: Array<{
    label: string
    value: string
    apply_text: string
    description?: string | null
  }>
}

type ProactiveOfferCardProps = {
  message: AssistantMessage
  onFollowUp?: (text: string) => void
  onIntegrationConnect?: (value: string) => void
}

/**
 * Renders proactive action offers — when the system detects a decision situation,
 * it surfaces concrete options to do the full work (build brief, draft response, etc.)
 * rather than asking clarifying questions.
 *
 * Shown ABOVE the MixedEvidencePanel. Only renders for question_options with
 * offer_type === "action_offer".
 */
export const ProactiveOfferCard: React.FC<ProactiveOfferCardProps> = ({ message, onFollowUp, onIntegrationConnect }) => {
  const [accepted, setAccepted] = useState<string | null>(null)

  const offers: Offer[] = (message.trust.question_options ?? [])
    .filter((qo) => qo.offer_type === 'action_offer' && qo.options.length > 0)
    .slice(0, 2) // keep a small amount of variety without overwhelming the CEO

  if (offers.length === 0 || !onFollowUp) return null

  const handleAccept = (opt: Offer['options'][number]) => {
    setAccepted(opt.label)
    if (opt.value.startsWith('connect_') && onIntegrationConnect) {
      onIntegrationConnect(opt.value)
      return
    }
    // Record that the CEO engaged with a proactive offer — signals response style preference
    void fetch('/identity/preference-signal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ signal_type: 'response_style', value: opt.value }),
    })
    onFollowUp(opt.apply_text)
  }

  return (
    <section className="proactive-offer-card">
      {offers.map((offer, index) => (
        <div key={`${offer.question}-${index}`} className="proactive-offer-card-block">
          <div className="proactive-offer-header">
            <span className="proactive-offer-eyebrow">
              {index === 0 ? 'I can help with this' : 'Another priority'}
            </span>
            <p className="proactive-offer-question">{offer.question}</p>
          </div>
          <div className="proactive-offer-options">
            {offer.options.map((opt) => (
              <button
                key={`${offer.question}-${opt.value}`}
                type="button"
                className={`proactive-offer-option${accepted === opt.label ? ' proactive-offer-option-accepted' : ''}`}
                onClick={() => handleAccept(opt)}
                disabled={accepted !== null}
              >
                <div className="proactive-offer-option-content">
                  <span className="proactive-offer-option-label">{opt.label}</span>
                  {opt.description ? (
                    <span className="proactive-offer-option-desc">{opt.description}</span>
                  ) : null}
                </div>
                <ArrowRight size={14} className="proactive-offer-option-arrow" />
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}
