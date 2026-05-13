import React from 'react'

import { ExecutiveMessageCard } from '../messages/ExecutiveMessageCard'
import { cleanUserVisibleQuery } from '../messages/messagePresentation'
import type { AssistantMessage } from '../messages/types'
import { CircleHelp, Loader2, MessageSquareText, Paperclip } from './icons'
import type { ArtifactPreview, KnowledgeDocument, SuggestedAction } from './types'

type ThreadPaneProps = {
  messages: AssistantMessage[]
  pendingQuery: string | null
  pendingAttachments: KnowledgeDocument[]
  uploadFeed: Array<{ id: string; title: string; summary: string | null }>
  starterActions: SuggestedAction[]
  copiedMessageId: string | null
  workbookViewLoadingId: string | null
  artifactPreviews: Record<string, ArtifactPreview>
  previewLoadingId: string | null
  approvalNotes: Record<string, string>
  resolvingMessageId: string | null
  renderRichContent: (value?: string) => React.ReactNode
  renderAnalysisPreview: (artifactId: string, content: string) => React.ReactNode
  messageRefs: React.MutableRefObject<Record<string, HTMLElement | null>>
  threadEndRef: React.RefObject<HTMLDivElement>
  onApplySuggestedPrompt: (prompt: string) => Promise<void> | void
  onCopyResponse: (message: AssistantMessage) => void
  onEnsureArtifactPreview: (message: AssistantMessage, artifact: AssistantMessage['artifacts'][number]) => Promise<ArtifactPreview | null>
  onDownloadArtifact: (message: AssistantMessage, artifact: AssistantMessage['artifacts'][number]) => void
  onOpenWorkbook: (message: AssistantMessage, artifact: AssistantMessage['artifacts'][number]) => void
  onApprovalNoteChange: (messageId: string, value: string) => void
  onResolveApproval: (message: AssistantMessage, decision: 'approve' | 'reject', mode?: 'draft' | 'send') => void
  onSubmitFollowUp: (message: AssistantMessage, text: string) => void
  onInlineAction: (prompt: string, intent: string) => Promise<string>
  onIntegrationConnect: (value: string) => void
}

export const ThreadPane: React.FC<ThreadPaneProps> = ({
  messages,
  pendingQuery,
  pendingAttachments,
  uploadFeed,
  starterActions,
  copiedMessageId,
  workbookViewLoadingId,
  artifactPreviews,
  previewLoadingId,
  approvalNotes,
  resolvingMessageId,
  renderRichContent,
  renderAnalysisPreview,
  messageRefs,
  threadEndRef,
  onApplySuggestedPrompt,
  onCopyResponse,
  onEnsureArtifactPreview,
  onDownloadArtifact,
  onOpenWorkbook,
  onApprovalNoteChange,
  onResolveApproval,
  onSubmitFollowUp,
  onInlineAction,
  onIntegrationConnect,
}) => (
  <section className="thread-panel">
    {messages.length === 0 && !pendingQuery ? (
      <div className="empty-state">
        <MessageSquareText size={26} />
        <h3>Start with one of these</h3>
        <div className="starter-action-grid">
          {starterActions.map((action) => (
            <button
              key={action.label}
              className="starter-action-card"
              type="button"
              onClick={() => void onApplySuggestedPrompt(action.prompt)}
            >
              <strong>{action.label}</strong>
              <span>{action.prompt}</span>
              <span
                className="starter-action-info"
                data-tooltip={action.description}
                aria-label={`${action.label} info`}
              >
                <CircleHelp size={14} />
              </span>
            </button>
          ))}
        </div>
      </div>
    ) : (
      <div className="message-thread">
        {messages.map((message) => {
          const generatedArtifacts = message.artifacts.filter((artifact) => artifact.status !== 'planned')
          const analysisArtifact = generatedArtifacts.find((artifact) => artifact.artifact_type === 'analysis_xlsx')
          const plannedExports = message.artifacts.filter(
            (artifact) =>
              artifact.status === 'planned' &&
              !generatedArtifacts.some((generated) => generated.artifact_type === artifact.artifact_type),
          )
          return (
            <article
              key={message.message_id}
              className="message-stack"
              ref={(element) => {
                messageRefs.current[message.message_id] = element
              }}
            >
              <div className="chat-row chat-row-user">
                <div className="chat-bubble chat-bubble-user">
                  <p>{cleanUserVisibleQuery(message.metadata.query) || 'Untitled request'}</p>
                </div>
              </div>

              <div className="chat-row chat-row-assistant">
                <ExecutiveMessageCard
                  message={message}
                  generatedArtifacts={generatedArtifacts}
                  plannedExports={plannedExports}
                  artifactPreviews={artifactPreviews}
                  previewLoadingId={previewLoadingId}
                  copied={copiedMessageId === message.message_id}
                  workbookLoading={workbookViewLoadingId === analysisArtifact?.artifact_id}
                  approvalNote={approvalNotes[message.message_id] || ''}
                  isResolvingApproval={resolvingMessageId === message.message_id}
                  renderRichContent={renderRichContent}
                  renderAnalysisPreview={renderAnalysisPreview}
                  onCopy={() => void onCopyResponse(message)}
                  onEnsureArtifactPreview={(artifact) => onEnsureArtifactPreview(message, artifact)}
                  onDownloadArtifact={(artifact) => void onDownloadArtifact(message, artifact)}
                  onOpenWorkbook={(artifact) => void onOpenWorkbook(message, artifact)}
                  onApprovalNoteChange={(value) => onApprovalNoteChange(message.message_id, value)}
                  onResolveApproval={(decision, mode) => void onResolveApproval(message, decision, mode)}
                  onFollowUp={(text) => onSubmitFollowUp(message, text)}
                  onInlineAction={onInlineAction}
                  onIntegrationConnect={onIntegrationConnect}
                />
              </div>
            </article>
          )
        })}
        {uploadFeed.map((upload) => (
          <div key={upload.id} className="chat-row chat-row-user">
            <div className="upload-feed-bubble">
              <Paperclip size={13} />
              <span className="upload-feed-title">{upload.title}</span>
              {upload.summary ? <span className="upload-feed-summary">{upload.summary}</span> : null}
            </div>
          </div>
        ))}
        {pendingQuery ? (
          <article key="__thinking__" className="message-stack">
            <div className="chat-row chat-row-user">
              <div className="chat-bubble chat-bubble-user">
                {pendingAttachments.length > 0 ? (
                  <div className="pending-attachments">
                    {pendingAttachments.map((doc) => (
                      <span key={doc.document_id} className="pending-attachment-chip">
                        <Paperclip size={11} />
                        {doc.title}
                      </span>
                    ))}
                  </div>
                ) : null}
                <p>{cleanUserVisibleQuery(pendingQuery) || pendingQuery}</p>
              </div>
            </div>
            <div className="chat-row chat-row-assistant">
              <div className="assistant-card assistant-card-chat executive-assistant-card thinking-bubble-card">
                <div className="thinking-dots">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            </div>
          </article>
        ) : null}
        <div ref={threadEndRef} />
      </div>
    )}
  </section>
)
