import type { AssistantMessage, RichRenderer } from '../types'

export type MessageRendererProps = {
  message: AssistantMessage
  summary: string
  renderRichContent: RichRenderer
  riskSignals: string[]
  onInlineAction?: (prompt: string, intent: string) => Promise<string>
  onFollowUp?: (text: string) => void
  onResolveApproval?: (decision: 'approve' | 'reject', mode?: 'draft' | 'send') => void
  isResolvingApproval?: boolean
  onIntegrationConnect?: (value: string) => void
}
