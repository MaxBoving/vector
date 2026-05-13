import React from 'react'
import { ArrowRight, Check, Copy, Loader2 } from './icons'

import { AccuracyGapsPanel } from './AccuracyGapsPanel'
import { AnswerChart } from './AnswerChart'
import { ApprovalDecisionCard } from './ApprovalDecisionCard'
import { BriefFollowUpPanel } from './BriefFollowUpPanel'
import { ArtifactExperience } from './ArtifactExperience'
import { ConfidenceBand } from './ConfidenceBand'
import { DetailsDrawer } from './DetailsDrawer'
import { ProactiveOfferCard } from './ProactiveOfferCard'
import {
  collectRiskSignals,
  getConfidenceSummaryText,
  getExecutiveSummary,
  getScheduleLayoutMode,
  groupExecutiveSections,
  summarizeTrustLanguage,
} from './messagePresentation'
import { ModeRenderer } from './renderers/ModeRenderer'
import type { AssistantArtifact, AssistantMessage, RichRenderer } from './types'
import type { ArtifactPreview } from '../dashboard/types'

type ExecutiveMessageCardProps = {
  message: AssistantMessage
  generatedArtifacts: AssistantArtifact[]
  plannedExports: AssistantArtifact[]
  artifactPreviews: Record<string, ArtifactPreview>
  previewLoadingId: string | null
  copied: boolean
  workbookLoading: boolean
  approvalNote: string
  isResolvingApproval: boolean
  renderRichContent: RichRenderer
  renderAnalysisPreview: (artifactId: string, content: string) => React.ReactNode
  onCopy: () => void
  onEnsureArtifactPreview: (artifact: AssistantArtifact) => Promise<ArtifactPreview | null>
  onDownloadArtifact: (artifact: AssistantArtifact) => void
  onOpenWorkbook: (artifact: AssistantArtifact) => void
  onApprovalNoteChange: (value: string) => void
  onResolveApproval: (decision: 'approve' | 'reject', mode?: 'draft' | 'send') => void
  onInlineAction?: (prompt: string, intent: string) => Promise<string>
  onFollowUp?: (text: string) => void
  onIntegrationConnect?: (value: string) => void
}

export const ExecutiveMessageCard: React.FC<ExecutiveMessageCardProps> = ({
  message,
  generatedArtifacts,
  plannedExports,
  artifactPreviews,
  previewLoadingId,
  copied,
  workbookLoading,
  approvalNote,
  isResolvingApproval,
  renderRichContent,
  renderAnalysisPreview,
  onCopy,
  onEnsureArtifactPreview,
  onDownloadArtifact,
  onOpenWorkbook,
  onApprovalNoteChange,
  onResolveApproval,
  onInlineAction,
  onFollowUp,
  onIntegrationConnect,
}) => {
  const grouping = groupExecutiveSections(message)
  const riskSignals = collectRiskSignals(message)
  const trustSummary = summarizeTrustLanguage(message)
  const confidenceSummary = getConfidenceSummaryText(message)
  const executiveSummary = getExecutiveSummary(message)

  const preamble = message.presentation?.preamble
  const mode = message.presentation?.mode
  const scheduleLayoutMode = getScheduleLayoutMode(message)
  const isTimelineSchedule = mode === 'schedule' ? scheduleLayoutMode === 'timeline' : false
  const hideTitle = mode === 'schedule' || mode === 'calendar' || mode === 'canvas'
  const artifactOnlyMessage = generatedArtifacts.length > 0
  const hasSuggestedFollowUps = Boolean(message.answer.follow_ups?.length)
  const hasRecommendedActions = Boolean(grouping.recommendedActions?.sections?.length)
  const hasProactiveOffer = Boolean(
    onFollowUp &&
    !hasSuggestedFollowUps &&
    !hasRecommendedActions &&
    message.trust.question_options?.some((option) => option.offer_type === 'action_offer' && option.options.length > 0)
  )
  const hasBriefFollowUps = hasSuggestedFollowUps
  const inlineEmailDraftActions =
    message.workflow_type === 'email_ingestion' &&
    mode === 'draft' &&
    message.status === 'pending' &&
    Boolean(message.metadata.gate?.options?.length)
  if (message.response_type === 'clarification') {
    const clarificationLines = preamble?.split('\n') ?? []
    const questionLines = clarificationLines.filter((line) => line.startsWith('—')).map((line) => line.replace(/^—\s*/, ''))
    const textLines = clarificationLines.filter((line) => !line.startsWith('—') && line.trim())
    return (
      <div className="clarification-message">
        {textLines.map((line, i) => (
          <p key={i} className="clarification-text">{line}</p>
        ))}
        {questionLines.length > 0 ? (
          <div className="clarification-questions-block">
            {onFollowUp ? (
              <div className="followup-chips">
                {questionLines.map((question) => (
                  <button key={question} type="button" className="followup-chip" onClick={() => onFollowUp(question)}>
                    {question}
                    <ArrowRight size={12} className="followup-chip-icon" />
                  </button>
                ))}
              </div>
            ) : (
              questionLines.map((question, i) => (
                <p key={i} className="clarification-question">— {question}</p>
              ))
            )}
          </div>
        ) : null}
      </div>
    )
  }

  return (
    <div className="assistant-card assistant-card-chat executive-assistant-card">
      {preamble ? (
        <section className="executive-preamble-block">
          <p className="executive-preamble">{preamble}</p>
        </section>
      ) : null}
      {!artifactOnlyMessage ? (
        <section className="executive-response-block">
          <div className="assistant-card-top executive-card-top">
            <div className="executive-card-heading">
              {!hideTitle ? <h3>{message.answer.title}</h3> : null}
            </div>
            <div className="response-actions">
              <button type="button" className="icon-action" onClick={onCopy} aria-label="Copy response" data-tooltip="Copy response">
                {copied ? <Check size={16} /> : <Copy size={16} />}
              </button>
            </div>
          </div>

          {message.status === 'pending' && !message.metadata.gate ? (
            <div className="pending-row executive-pending-row">
              <Loader2 size={16} className="spin" />
              <span>Preparing the response and supporting materials…</span>
            </div>
          ) : null}

          {confidenceSummary && message.trust.confidence !== 'high' && mode !== 'brief' && mode !== 'schedule' && mode !== 'calendar' ? (
            <ConfidenceBand confidence={message.trust.confidence} label={confidenceSummary} />
          ) : null}

          {message.trust.missing_context?.length && !isTimelineSchedule ? (
            <AccuracyGapsPanel
              missingContext={message.trust.missing_context}
              onIntegrationConnect={onIntegrationConnect}
            />
          ) : null}

          {message.answer.chart ? <AnswerChart chart={message.answer.chart} /> : null}

          <ModeRenderer
            message={message}
            summary={executiveSummary}
            renderRichContent={renderRichContent}
            riskSignals={riskSignals}
            onInlineAction={onInlineAction}
            onFollowUp={onFollowUp}
            onResolveApproval={onResolveApproval}
            isResolvingApproval={isResolvingApproval}
            onIntegrationConnect={onIntegrationConnect}
          />
        </section>
      ) : null}

      {hasBriefFollowUps && onFollowUp && !hasProactiveOffer ? (
        <BriefFollowUpPanel message={message} onFollowUp={onFollowUp} />
      ) : null}

      <ArtifactExperience
        artifacts={generatedArtifacts}
        plannedArtifacts={plannedExports}
        artifactPreviews={artifactPreviews}
        previewLoadingId={previewLoadingId}
        workbookLoading={workbookLoading}
        renderAnalysisPreview={renderAnalysisPreview}
        onEnsureArtifactPreview={onEnsureArtifactPreview}
        onDownloadArtifact={onDownloadArtifact}
        onOpenWorkbook={onOpenWorkbook}
      />

      {!artifactOnlyMessage && hasProactiveOffer && !isTimelineSchedule ? (
        <ProactiveOfferCard message={message} onFollowUp={onFollowUp} onIntegrationConnect={onIntegrationConnect} />
      ) : null}

      {!artifactOnlyMessage ? <DetailsDrawer message={message} plannedExports={plannedExports} /> : null}

      {message.status === 'pending' && message.metadata.gate && !inlineEmailDraftActions ? (
        <ApprovalDecisionCard
          message={message}
          note={approvalNote}
          isResolving={isResolvingApproval}
          onNoteChange={onApprovalNoteChange}
          onResolve={onResolveApproval}
        />
      ) : null}
    </div>
  )
}
