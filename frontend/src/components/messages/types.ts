import type { ReactNode } from 'react'

export type SectionType = 'priority' | 'upcoming' | 'risk' | 'action' | 'detail'

export type AnswerSection = {
  label: string
  content?: string
  items?: string[]
  section_type?: SectionType | null
}

export type ChartDataPoint = {
  label: string
  value: number
}

export type FollowUpChip = {
  label: string
  prompt: string
}

export type ChartSpec = {
  type: string
  title: string
  subtitle?: string | null
  data: ChartDataPoint[]
  value_format: 'currency' | 'percent' | 'number'
  color_scheme: 'pipeline' | 'finance' | 'neutral'
}

export type AssistantMessage = {
  conversation_id: string
  message_id: string
  workflow_type: 'conversational' | 'report_generation' | 'document_explanation' | 'email_ingestion' | 'email_watcher' | 'email_action' | 'calendar_briefing' | 'calendar_action' | 'morning_brief' | 'schedule_planning' | 'meeting_prep' | 'weekly_recap'
  response_type: 'conversational' | 'report' | 'explanation' | 'brief' | 'schedule' | 'clarification'
  status: 'pending' | 'completed' | 'failed'
  answer: {
    title: string
    summary: string
    sections: AnswerSection[]
    chart?: ChartSpec | null
    follow_ups?: FollowUpChip[]
  }
  trust: {
    confidence: 'low' | 'medium' | 'high'
    confidence_score: number
    assumptions: string[]
    open_questions: string[]
    data_quality: 'low' | 'medium' | 'high'
    calculation_used: boolean
    missing_context: string[]
    evidence_state?: 'strong' | 'mixed' | 'sparse' | null
    evidence_reasons?: string[]
    safe_to_act?: boolean | null
    question_options?: Array<{
      question: string
      priority_score?: number
      options: Array<{
        label: string
        value: string
        apply_text: string
        description?: string | null
      }>
      offer_type?: 'action_offer' | 'clarification' | null
    }>
  }
  sources: Array<{
    source_id: string
    title: string
    type: 'document' | 'state' | 'artifact'
    snippet?: string
    role?: string | null
    thread_id?: string | null
    relevance_reason?: string | null
    used_for?: string[]
    confidence_impact?: string | null
  }>
  artifacts: Array<{
    artifact_type: string
    artifact_id: string
    label: string
    format?: string
    status?: string
    purpose?: string | null
    ready_when?: string | null
    blocking_reason?: string | null
    metadata?: {
      theme_id?: string
      template_id?: string
      presentation_version?: string
    }
  }>
  presentation?: {
    mode?: 'brief' | 'report' | 'schedule' | 'decision' | 'draft' | 'finance' | 'artifact' | 'media' | 'calendar' | 'canvas' | 'spreadsheet' | 'clarification' | null
    variant?: string | null
    preamble?: string | null
    summary?: string | null
    priorities?: Array<{
      title: string
      content?: string | null
      items?: string[]
    }>
    recommended_actions?: Array<{
      title: string
      content?: string | null
      items?: string[]
    }>
    risks?: Array<{
      title: string
      content?: string | null
      items?: string[]
    }>
    details?: Array<{
      title: string
      content?: string | null
      items?: string[]
    }>
    weekly_plan?: {
      planning_window?: {
        horizon?: string | null
        start_date?: string | null
        end_date?: string | null
        timezone?: string | null
        workday_start?: string | null
        workday_end?: string | null
        span_days?: number | null
      } | null
      blocks?: Array<{
        title: string
        kind?: string | null
        starts_at?: string | null
        ends_at?: string | null
        day_label?: string | null
        time_window?: string | null
        reason?: string | null
        source_refs?: string[]
        confidence?: 'low' | 'medium' | 'high' | null
      }>
      deadlines?: string[]
      meetings?: Array<{
        title: string
        starts_at?: string | null
        ends_at?: string | null
        attendees?: string[]
      }>
      follow_ups?: string[]
    } | null
    decision?: {
      decision_summary?: string | null
      recommended_option?: string | null
      impact_if_approved?: string | null
      impact_if_rejected?: string | null
      required_by?: string | null
      options?: Array<{
        label: string
        decision?: 'approve' | 'reject' | null
        mode?: 'draft' | 'send' | null
        description?: string | null
      }>
    } | null
    draft?: {
      channel?: string | null
      status?: string | null
      to?: string | null
      cc?: string[]
      subject?: string | null
      body?: string | null
      call_to_action?: string | null
    } | null
    finance?: {
      template?: string | null
      headline?: string | null
      takeaways?: string[]
      implications?: string[]
      recommendation?: string | null
      next_steps?: string[]
      threshold_events?: string[]
      key_metrics?: Array<{
        label: string
        value: string
      }>
      primary_visual?: {
        title?: string | null
        label?: string | null
        description?: string | null
      } | null
      charts?: Array<{
        title: string
        type: string
        group?: string
        data: Array<{
          metric: string
          actual?: number | null
          budget?: number | null
          forecast?: number | null
        }>
      }>
    } | null
    calendar?: {
      events?: Array<{
        title: string
        starts_at?: string | null
        ends_at?: string | null
        day_label?: string | null
        attendees?: string[]
        location?: string | null
        kind?: string | null
      }>
      follow_ups?: string[]
    } | null
    canvas?: {
      title?: string | null
      subtitle?: string | null
      hero_metric?: {
        label: string
        value: string
        delta?: string | null
      } | null
      sections?: Array<{
        label: string
        bullets?: string[]
        content?: string | null
        highlight?: boolean
      }>
      source_credit?: string | null
      theme_id?: string | null
    } | null
    spreadsheet?: {
      title?: string | null
      subtitle?: string | null
      frozen_columns?: number
      total_rows?: number | null
      source_artifact_id?: string | null
      columns?: Array<{
        key: string
        label: string
        width?: number | null
        align?: 'left' | 'center' | 'right' | null
      }>
      rows?: Array<{
        label?: string | null
        cells?: Array<{
          value?: string | null
          kind?: string | null
          align?: 'left' | 'center' | 'right' | null
        }>
      }>
    } | null
  } | null
  metadata: {
    interaction_id?: number
    current_stage?: string | null
    query?: string
    timestamp?: string
    envelope_version?: number
    semantic_source?: string
    finance_template?: string
    finance_digest?: {
      template?: string
      headline?: string
      takeaways?: string[]
      implications?: string[]
      recommendation?: string | null
      next_steps?: string[]
    } | null
    primary_visual?: {
      title?: string
      label?: string
      description?: string
    } | null
    gate?: {
      gate_type?: string
      reason?: string
      options?: Array<{
        label?: string
        decision?: 'approve' | 'reject'
        mode?: 'draft' | 'send'
      }>
    } | null
    approval?: {
      decision?: 'approve' | 'reject'
      note?: string | null
      actor?: string
    }
    execution_unavailable?: {
      channel?: string
      reason?: string
      channels?: string[]
      reasons?: string[]
    } | null
  }
}

export type AssistantArtifact = AssistantMessage['artifacts'][number]
export type AssistantSource = AssistantMessage['sources'][number]
export type RichRenderer = (value?: string) => ReactNode

export type ExecutiveSectionGroup = {
  title: string
  sections: AnswerSection[]
}

export type ExecutiveGrouping = {
  priorities: ExecutiveSectionGroup | null
  recommendedActions: ExecutiveSectionGroup | null
  risks: ExecutiveSectionGroup | null
  details: ExecutiveSectionGroup[]
}
