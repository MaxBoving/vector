import type { AssistantMessage } from '../messages/types'

export type KnowledgeDocument = {
  document_id: string
  title: string
  status: string
  intel_summary?: string
  domains: string[]
  purpose?: string
  identity_role?: string | null
}

export type LoginState = {
  username: string
  password: string
}

export type IntegrationStatus = {
  provider: 'google' | 'microsoft'
  service: 'gmail' | 'google_calendar' | 'outlook_mail' | 'outlook_calendar'
  connected: boolean
  account_email?: string | null
  expires_at?: string | null
}

export type ArtifactPreview = {
  artifact_id: string
  artifact_type: string
  label: string
  format?: string | null
  status?: string | null
  content: string
  metadata?: {
    theme_id?: string
    template_id?: string
    presentation_version?: string
  }
}

export type WorkbookViewerState = {
  messageId: string
  artifact: AssistantMessage['artifacts'][number]
  title: string
}

export type WorkbookViewTab = 'sheet' | 'charts' | 'pivot'

export type WorkbookMetricViewModel = {
  label: string
  value: string
}

export type WorkbookTableViewModel = {
  title: string
  columns: string[]
  rows: string[][]
  row_provenance?: Array<{
    source_type: string
    source_ref: string
    source_excerpt?: string | null
  }>
}

export type WorkbookChartViewModel = {
  title: string
  chart_type: string
  x_axis: string
  y_axis: string
  series_label: string
}

export type WorkbookSheetViewModel = {
  name: string
  kind?: 'summary' | 'model' | 'variance' | 'forecast' | 'charts'
  metrics?: WorkbookMetricViewModel[]
  tables?: WorkbookTableViewModel[]
  chart_specs?: WorkbookChartViewModel[]
  charts?: WorkbookChartViewModel[]
  pivot_snapshots?: Array<{
    title: string
    dimension: string
    measure: string
    rows: Array<{ label: string; value: number }>
  }>
}

export type WorkbookPreviewModel = {
  workbook_title?: string
  sheets?: WorkbookSheetViewModel[]
}

export type WorkbookPivotRowViewModel = {
  label: string
  value: number
}

export type WorkbookPivotSnapshotViewModel = {
  title: string
  dimension: string
  measure: string
  rows: WorkbookPivotRowViewModel[]
}

export type WorkbookTabViewModel = {
  name: string
  kind: 'summary' | 'model' | 'variance' | 'forecast' | 'charts'
  metrics: WorkbookMetricViewModel[]
  tables: Array<WorkbookTableViewModel & { row_provenance?: Array<Record<string, unknown>> }>
  charts: WorkbookChartViewModel[]
  pivot_snapshots: WorkbookPivotSnapshotViewModel[]
}

export type WorkbookViewResponse = {
  artifact_id: string
  title: string
  tabs: WorkbookTabViewModel[]
  metadata?: {
    period_coverage?: {
      periods?: string[]
      comparison_pairs?: Array<{ prior?: string; current?: string }>
      has_comparison?: boolean
    }
    comparison_basis?: Array<{
      source_ref?: string
      source_excerpt?: string
    }>
  }
}

export type QueryPlaybook = {
  label: string
  prompt: string
}

export type SuggestedAction = {
  label: string
  prompt: string
  description: string
}

export type UploadMode = 'reference' | 'report_example' | 'workbook_example' | 'brand_reference'

export type CompanyIdentityProfile = {
  company_name: string
  has_examples: boolean
  tone?: string | null
  preferred_formats: string[]
  section_patterns: string[]
  reference_titles: string[]
}

export type Project = {
  project_id: string
  name: string
  description?: string | null
  created_at?: string | null
  updated_at?: string | null
  document_ids: string[]
  conversation_ids: string[]
}

export type SavedConversation = {
  conversation_id: string
  title: string
  pinned: boolean
  archived: boolean
  created_at?: string | null
  updated_at?: string | null
  message_count: number
  latest_query?: string | null
  latest_timestamp?: string | null
}
