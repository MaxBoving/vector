import React from 'react'
import { Loader2 } from '../icons'

import { InlineResult } from './InlineResult'
import { getModeCopy, getPresentationVariantLabel } from '../messagePresentation'
import { useInlineAction } from './useInlineAction'
import type { MessageRendererProps } from './types'

const TONES = ['concise', 'formal', 'assertive', 'warmer'] as const

function channelLabel(channel?: string | null): string {
  if (!channel) return 'Email'
  return channel.charAt(0).toUpperCase() + channel.slice(1).replace(/_/g, ' ')
}

function statusLabel(status?: string | null): string | null {
  if (!status) return null
  if (status === 'ready_to_send') return 'Ready to send'
  if (status === 'needs_review') return 'Needs review'
  if (status === 'draft') return 'Draft'
  return status.replace(/_/g, ' ')
}

export const DraftRenderer: React.FC<MessageRendererProps> = ({
  message,
  onInlineAction,
  onResolveApproval,
  isResolvingApproval,
  onIntegrationConnect,
}) => {
  const draft = message.presentation?.draft
  const copy = getModeCopy(message)
  const variantLabel = getPresentationVariantLabel(message)
  const channel = channelLabel(draft?.channel)
  const status = statusLabel(draft?.status)
  const { status: actionStatus, activeIntent, result, trigger, dismiss } = useInlineAction(onInlineAction)
  const gateOptions = message.metadata.gate?.options ?? []
  const connectOptions = (message.trust.question_options ?? [])
    .filter((entry) => entry.offer_type === 'action_offer')
    .flatMap((entry) => entry.options)
    .filter((option) => option.value.startsWith('connect_'))

  return (
    <div className="mode-renderer-shell mode-renderer-draft">
      <div className="draft-renderer">
        <div className="draft-renderer-headline">
          <span className="draft-renderer-headline-label">{copy.bottomLineLabel}</span>
          {variantLabel ? <span className="mode-variant-pill">{variantLabel}</span> : null}
          <p className="draft-renderer-headline-copy">{draft?.call_to_action || copy.emptyState}</p>
        </div>

        <div className="draft-renderer-meta">
          <span className="draft-renderer-channel">{channel}</span>
          {status ? <span className="draft-renderer-status">{status}</span> : null}
        </div>

        <div className="draft-sheet">
          <div className="draft-sheet-row">
            <span className="draft-field-label">To</span>
            <strong>{draft?.to || 'Recipient needed'}</strong>
          </div>
          {draft?.cc && draft.cc.length > 0 ? (
            <div className="draft-sheet-row">
              <span className="draft-field-label">Cc</span>
              <span>{draft.cc.join(', ')}</span>
            </div>
          ) : null}
          <div className="draft-sheet-row">
            <span className="draft-field-label">Subject</span>
            <strong>{draft?.subject || 'Subject needed'}</strong>
          </div>
          <div className="draft-sheet-body">
            <span className="draft-field-label">Body</span>
            <div className="draft-body-copy">
              {draft?.body ? <p>{draft.body}</p> : <p>Body content has not been prepared yet.</p>}
            </div>
          </div>
        </div>

        {draft?.call_to_action ? (
          <p className="draft-renderer-cta">{draft.call_to_action}</p>
        ) : null}

        {message.status === 'pending' && gateOptions.length > 0 && onResolveApproval ? (
          <div className="draft-revise-tones">
            {gateOptions.map((option) => {
              const isSend = option.decision === 'approve'
              const buttonClass = isSend ? 'send-button' : 'soft-button'
              return (
                <button
                  key={`${option.label || option.mode || option.decision}`}
                  type="button"
                  className={buttonClass}
                  disabled={Boolean(isResolvingApproval)}
                  onClick={() => onResolveApproval(option.decision || 'reject', option.mode)}
                >
                  {Boolean(isResolvingApproval) && isSend ? <Loader2 size={12} className="spin" /> : null}
                  {option.label || (isSend ? 'Send' : 'Discard')}
                </button>
              )
            })}
          </div>
        ) : null}

        {message.metadata.execution_unavailable?.channel === 'email' && connectOptions.length > 0 && onIntegrationConnect ? (
          <div className="draft-revise-tones">
            {connectOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                className="send-button"
                onClick={() => onIntegrationConnect(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : null}

        {onInlineAction ? (
          <div className="draft-renderer-revise">
            <span className="draft-revise-label">Revise tone</span>
            {result ? (
              <InlineResult
                label={`Revised — ${activeIntent ?? 'tone'}`}
                result={result}
                onDismiss={dismiss}
                className="draft-inline-result"
              />
            ) : (
              <div className="draft-revise-tones">
                {TONES.map((tone) => (
                  <button
                    key={tone}
                    type="button"
                    className={`draft-revise-pill${actionStatus === 'loading' && activeIntent === tone ? ' draft-revise-pill--active' : ''}`}
                    disabled={actionStatus === 'loading'}
                    onClick={() => trigger(tone, `Rewrite the following draft email to sound more ${tone}. Keep the same meaning and key details, just adjust the tone.\n\nSubject: ${draft?.subject || ''}\n\nBody:\n${draft?.body || ''}`)}
                  >
                    {actionStatus === 'loading' && activeIntent === tone
                      ? <Loader2 size={11} className="spin" />
                      : tone}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}
