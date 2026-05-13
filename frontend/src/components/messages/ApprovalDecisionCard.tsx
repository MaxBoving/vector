import React from 'react'
import { CheckCircle2, Loader2 } from './icons'

import type { AssistantMessage } from './types'

type ApprovalDecisionCardProps = {
  message: AssistantMessage
  note: string
  isResolving: boolean
  onNoteChange: (value: string) => void
  onResolve: (decision: 'approve' | 'reject', mode?: 'draft' | 'send') => void
}

export const ApprovalDecisionCard: React.FC<ApprovalDecisionCardProps> = ({
  message,
  note,
  isResolving,
  onNoteChange,
  onResolve,
}) => (
  <div className="approval-decision-card">
    <div className="approval-decision-copy">
      <span className="eyebrow">Decision Needed</span>
      <h4>Review before this moves forward</h4>
      <p>
        {message.metadata.gate?.reason ||
          'This request affects an external or sensitive action and needs a business decision.'}
      </p>
      <label className="approval-note-field">
        <span className="eyebrow">Optional Context</span>
        <textarea
          rows={2}
          value={note}
          onChange={(event) => onNoteChange(event.target.value)}
          placeholder="Add context for this decision."
        />
      </label>
    </div>
    <div className="approval-actions">
      {message.metadata.gate?.options && message.metadata.gate.options.length > 0 ? (
        message.metadata.gate.options.map((option) => {
          const isApprove = option.decision === 'approve'
          const buttonClass =
            isApprove && option.mode === 'send'
              ? 'send-button danger-action'
              : isApprove
                ? 'send-button'
                : 'soft-button'

          return (
            <button
              key={`${message.message_id}-${option.label}-${option.mode || 'default'}`}
              className={buttonClass}
              type="button"
              disabled={isResolving}
              onClick={() => onResolve(option.decision || 'reject', option.mode)}
              data-tooltip={option.label || 'Resolve decision'}
            >
              {isResolving ? <Loader2 size={16} className="spin" /> : isApprove ? <CheckCircle2 size={16} /> : null}
              {option.label || 'Approve'}
            </button>
          )
        })
      ) : (
        <>
          <button className="soft-button" type="button" disabled={isResolving} onClick={() => onResolve('reject')} data-tooltip="Decline and stop">
            {isResolving ? <Loader2 size={16} className="spin" /> : null}
            Decline
          </button>
          <button className="send-button" type="button" disabled={isResolving} onClick={() => onResolve('approve')} data-tooltip="Approve and continue">
            {isResolving ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />}
            Approve and continue
          </button>
        </>
      )}
    </div>
  </div>
)
