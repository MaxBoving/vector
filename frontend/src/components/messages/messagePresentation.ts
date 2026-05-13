import type { AnswerSection, AssistantMessage, AssistantSource, ExecutiveGrouping, ExecutiveSectionGroup } from './types'

type RichBlock =
  | { type: 'heading'; level: 1 | 2 | 3; content: string }
  | { type: 'paragraph'; content: string }
  | { type: 'list'; items: string[] }
  | { type: 'table'; headers: string[]; rows: string[][] }

// Regex fallback for responses predating section_type — new responses use section_type directly
const PRIORITY_LABELS = /(priority|key finding|headline|important|focus|top|watch|threads|inputs|proposal)/i
const ACTION_LABELS = /(action|recommend|follow[- ]?up|next step|decision|plan|send|approve)/i
const RISK_LABELS = /(risk|deadline|constraint|tradeoff|missing|concern|blocker|question|meeting)/i

const resolveSectionBucket = (section: AnswerSection): 'priority' | 'action' | 'risk' | 'detail' => {
  if (section.section_type === 'priority' || section.section_type === 'upcoming') return 'priority'
  if (section.section_type === 'action') return 'action'
  if (section.section_type === 'risk') return 'risk'
  if (section.section_type === 'detail') return 'detail'
  // Legacy fallback: classify by label text
  if (PRIORITY_LABELS.test(section.label)) return 'priority'
  if (ACTION_LABELS.test(section.label)) return 'action'
  if (RISK_LABELS.test(section.label)) return 'risk'
  return 'detail'
}

const createGroup = (title: string, sections: AnswerSection[]): ExecutiveSectionGroup | null =>
  sections.length > 0 ? { title, sections } : null

const toAnswerSections = (
  sections?: Array<{ title: string; content?: string | null; items?: string[] }>,
): AnswerSection[] =>
  (sections || []).map((section) => ({
    label: section.title,
    content: section.content || undefined,
    items: section.items || [],
  }))

export const getPresentationMode = (message: AssistantMessage) => message.presentation?.mode || null

export const getPresentationVariant = (message: AssistantMessage) => message.presentation?.variant || null

export const formatPresentationVariantLabel = (variant?: string | null) => {
  const text = (variant || '').trim().replace(/_/g, ' ')
  if (!text) return null
  return text
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export const getPresentationVariantLabel = (message: AssistantMessage) =>
  formatPresentationVariantLabel(getPresentationVariant(message))

export const getResolvedPresentationMode = (message: AssistantMessage) => {
  const mode = getPresentationMode(message)
  if (mode) {
    return mode
  }

  // Legacy fallback for older cached responses that predate canonical presentation.mode.
  if (message.workflow_type === 'calendar_briefing') {
    return 'calendar'
  }
  if (message.presentation?.spreadsheet) {
    return 'spreadsheet'
  }
  if (message.workflow_type === 'schedule_planning') {
    return 'schedule'
  }
  if (message.workflow_type === 'email_watcher' || message.workflow_type === 'email_ingestion' || message.workflow_type === 'morning_brief' || message.workflow_type === 'meeting_prep' || message.workflow_type === 'weekly_recap') {
    return 'brief'
  }
  if (Boolean(message.metadata.finance_template && message.response_type === 'report' && message.workflow_type === 'report_generation')) {
    return 'finance'
  }
  return 'report'
}

export const isUserVisibleAssumption = (value: string) => {
  const text = (value || '').trim()
  if (!text) return false
  const lowered = text.toLowerCase()
  const internalMarkers = [
    'workflow',
    'planner',
    'planning workflow',
    'compound evidence',
    'evidence path',
    'runtime',
    'router',
    'agent',
    'artifact pipeline',
    'context stage',
    'stage output',
    'orchestration',
    'tool call',
    'classification',
    'semantic parse',
  ]
  return !internalMarkers.some((marker) => lowered.includes(marker))
}

export const getVisibleAssumptions = (message: AssistantMessage) =>
  message.trust.assumptions.map((item) => item.trim()).filter(isUserVisibleAssumption)

export const getExecutiveResponseLabel = (message: AssistantMessage) => {
  const mode = getResolvedPresentationMode(message)
  const variant = getPresentationVariant(message)

  if (mode === 'finance') {
    return 'Finance Digest'
  }
  if (mode === 'calendar') {
    return 'Calendar Brief'
  }
  if (mode === 'spreadsheet') {
    return 'Spreadsheet'
  }
  if (mode === 'decision') {
    return 'Decision Brief'
  }
  if (mode === 'draft') {
    return variant === 'email' ? 'Email Draft' : 'Draft'
  }
  if (mode === 'artifact') {
    return variant === 'calendar' ? 'Calendar Action' : 'Executive Output'
  }
  if (mode === 'brief') {
    if (variant === 'narrative_recap') {
      return 'Narrative Recap'
    }
    if (variant === 'list_form') {
      return 'Compact Brief'
    }
    if (variant === 'inbox_watch') {
      return 'Inbox Watch'
    }
    if (variant === 'meeting_brief') {
      return 'Calendar Brief'
    }
    if (variant === 'weekly_watch') {
      return 'Weekly Watch'
    }
    if (variant === 'weekly_recap') {
      return 'Week in Review'
    }
    if (variant === 'meeting_prep') {
      return 'Meeting Prep Brief'
    }
    return 'Executive Brief'
  }
  if (mode === 'schedule') {
    if (variant === 'week_timeline') {
      return 'Week Plan'
    }
    return variant === 'timeline' ? 'Executive Schedule' : 'Weekly Plan'
  }
  if (mode === 'report') {
    return variant === 'document' ? 'Document Brief' : 'Executive Brief'
  }
  return message.response_type === 'report' ? 'Executive Brief' : 'Executive Readout'
}

export type ExecutiveModeCopy = {
  bottomLineLabel: string
  implicationLabel: string
  actionLabel: string
  watchLabel: string
  emptyState: string
  compactLeadLabel: string
}

const MODE_COPY: Record<string, ExecutiveModeCopy> = {
  report: {
    bottomLineLabel: 'Bottom line',
    implicationLabel: 'Implications',
    actionLabel: 'Action',
    watchLabel: 'Watch items',
    emptyState: 'No additional detail available.',
    compactLeadLabel: 'Executive report',
  },
  finance: {
    bottomLineLabel: 'Bottom line',
    implicationLabel: 'Implications',
    actionLabel: 'Action',
    watchLabel: 'Watch items',
    emptyState: 'No financial detail available.',
    compactLeadLabel: 'Finance digest',
  },
  schedule: {
    bottomLineLabel: 'Today at a glance',
    implicationLabel: 'Conflicts',
    actionLabel: 'Prep',
    watchLabel: 'Deadlines',
    emptyState: 'No schedule items found.',
    compactLeadLabel: 'Schedule plan',
  },
  calendar: {
    bottomLineLabel: 'Today at a glance',
    implicationLabel: 'Conflicts',
    actionLabel: 'Prep',
    watchLabel: 'Follow-ups',
    emptyState: 'No calendar events found.',
    compactLeadLabel: 'Calendar brief',
  },
  brief: {
    bottomLineLabel: 'Executive brief',
    implicationLabel: 'Priorities',
    actionLabel: 'Next steps',
    watchLabel: 'Watch list',
    emptyState: 'No brief available.',
    compactLeadLabel: 'Executive brief',
  },
  decision: {
    bottomLineLabel: 'Decision path',
    implicationLabel: 'Tradeoffs',
    actionLabel: 'Recommended action',
    watchLabel: 'Risks',
    emptyState: 'No decision brief available.',
    compactLeadLabel: 'Decision brief',
  },
  draft: {
    bottomLineLabel: 'Draft posture',
    implicationLabel: 'Edits',
    actionLabel: 'Send path',
    watchLabel: 'Open issues',
    emptyState: 'No draft brief available.',
    compactLeadLabel: 'Draft brief',
  },
  artifact: {
    bottomLineLabel: 'Supporting asset',
    implicationLabel: 'Implications',
    actionLabel: 'Action',
    watchLabel: 'Watch items',
    emptyState: 'No supporting assets available.',
    compactLeadLabel: 'Supporting asset brief',
  },
  media: {
    bottomLineLabel: 'Media brief',
    implicationLabel: 'Implications',
    actionLabel: 'Action',
    watchLabel: 'Watch items',
    emptyState: 'No media brief available.',
    compactLeadLabel: 'Media brief',
  },
  spreadsheet: {
    bottomLineLabel: 'Workbook view',
    implicationLabel: 'Key deltas',
    actionLabel: 'Actions',
    watchLabel: 'Watch list',
    emptyState: 'No workbook view available.',
    compactLeadLabel: 'Workbook view',
  },
}

export const getModeCopy = (message: AssistantMessage): ExecutiveModeCopy => {
  const mode = getResolvedPresentationMode(message)
  return MODE_COPY[mode] || MODE_COPY.report
}

const normalizeLeadText = (value?: string) => {
  const text = cleanRichText(value)
  if (!text) return ''
  const clipped = text.replace(/\s+/g, ' ').trim()
  const firstSentenceEnd = clipped.search(/[.!?]\s/)
  if (firstSentenceEnd > 0) {
    return clipped.slice(0, firstSentenceEnd + 1).trim()
  }
  return clipped.length > 220 ? `${clipped.slice(0, 217).trim()}...` : clipped
}

export const getExecutiveLeadText = (message: AssistantMessage) => {
  const mode = getResolvedPresentationMode(message)
  const raw = message.presentation?.summary || message.answer.summary
  const normalized = normalizeLeadText(raw)
  if (!normalized) {
    return MODE_COPY[mode]?.emptyState || MODE_COPY.report.emptyState
  }
  return normalized
}

type TimedItem = {
  title: string
  starts_at?: string | null
  ends_at?: string | null
  day_label?: string | null
}

const toTimeValue = (value?: string | null) => {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.getTime()
}

const timeKeyForItem = (item: TimedItem) => {
  const day = item.day_label?.trim().toLowerCase() || ''
  const start = toTimeValue(item.starts_at)
  const end = toTimeValue(item.ends_at)
  return `${day}:${start ?? 'na'}:${end ?? 'na'}`
}

export const getTimedOverlapHighlights = (items: TimedItem[], limit = 3): string[] => {
  const timed = items
    .map((item) => ({
      ...item,
      start: toTimeValue(item.starts_at),
      end: toTimeValue(item.ends_at),
    }))
    .filter((item) => item.start !== null)

  const overlaps: string[] = []
  const seenKeys = new Set<string>()
  for (let i = 0; i < timed.length; i += 1) {
    const current = timed[i]
    const currentStart = current.start
    const currentEnd = current.end ?? (currentStart !== null ? currentStart + 30 * 60000 : null)
    if (currentStart === null || currentEnd === null) {
      continue
    }
    const group = timed.filter((candidate) => {
      const candidateStart = candidate.start
      if (candidate === current || candidateStart === null) return false
      const candidateEnd = candidate.end ?? candidateStart + 30 * 60000
      if (candidateEnd === null) return false
      if (current.day_label && candidate.day_label && current.day_label !== candidate.day_label) return false
      return candidateStart < currentEnd && candidateEnd > currentStart
    })

    if (group.length === 0) continue
    const groupNames = [current.title, ...group.map((item) => item.title)].slice(0, 3)
    const key = `${timeKeyForItem(current)}:${groupNames.join('|')}`
    if (!seenKeys.has(key)) {
      overlaps.push(`${groupNames.join(' • ')} overlaps`)
      seenKeys.add(key)
    }
    if (overlaps.length >= limit) {
      break
    }
  }
  return overlaps
}

export const getDeadlineHighlights = (deadlines: string[], limit = 3) =>
  deadlines
    .map((item) => cleanRichText(item))
    .filter(Boolean)
    .slice(0, limit)

export const isPlanningMessage = (message: AssistantMessage) =>
  getResolvedPresentationMode(message) === 'schedule'

export const isFinanceDigestMessage = (message: AssistantMessage) =>
  getResolvedPresentationMode(message) === 'finance'

export const isMixedEvidenceMessage = (message: AssistantMessage) => {
  // Explicitly tagged mixed by the backend
  if (message.trust.evidence_state === 'mixed') return true
  // Untagged but has enough unresolved signals to warrant interactive surfacing
  return (
    message.trust.open_questions.length >= 3 ||
    (message.trust.assumptions.length >= 2 && message.trust.open_questions.length >= 2) ||
    (message.trust.missing_context.length >= 2 && message.trust.open_questions.length >= 2)
  )
}

export const isSparseEvidenceMessage = (message: AssistantMessage) => {
  // Explicitly tagged sparse by backend — show SparseEvidenceCard
  if (message.trust.evidence_state === 'sparse') return true
  // Explicitly tagged mixed — let MixedEvidencePanel handle it instead
  if (message.trust.evidence_state === 'mixed') return false
  // Untagged: only treat as sparse for genuinely low confidence (e.g. failed responses)
  return message.trust.confidence === 'low'
}

export const summarizeTrustLanguage = (message: AssistantMessage) => {
  const parts: string[] = []
  const visibleAssumptions = getVisibleAssumptions(message)
  if (message.trust.evidence_state) {
    parts.push(`${message.trust.evidence_state} evidence`)
  }
  if (message.sources.length > 0) {
    parts.push(`${message.sources.length} source${message.sources.length === 1 ? '' : 's'}`)
  }
  if (visibleAssumptions.length > 0) {
    parts.push(`${visibleAssumptions.length} assumption${visibleAssumptions.length === 1 ? '' : 's'} to confirm`)
  }
  if (message.trust.open_questions.length > 0) {
    parts.push(`${message.trust.open_questions.length} open question${message.trust.open_questions.length === 1 ? '' : 's'}`)
  }
  if (message.trust.missing_context.length > 0) {
    parts.push(`${message.trust.missing_context.length} context gap${message.trust.missing_context.length === 1 ? '' : 's'}`)
  }
  return parts.join(' • ')
}

export const formatConfidenceScore = (score: number) => {
  if (!Number.isFinite(score)) {
    return '50%'
  }

  const normalized = score > 1 ? score / 100 : score
  const clamped = Math.max(0, Math.min(normalized, 1))
  return `${Math.round(clamped * 100)}%`
}

export const getConfidenceSummaryText = (message: AssistantMessage) => {
  if (message.trust.evidence_state === 'sparse') {
    return 'Some signals are estimated — answer the follow-up questions to sharpen this.'
  }
  if (message.trust.confidence === 'low') {
    const gaps = message.trust.missing_context.length
    return gaps > 0
      ? `Limited data — ${gaps} context gap${gaps === 1 ? '' : 's'} may affect accuracy.`
      : 'Limited data available. Treat as directional.'
  }
  if (message.trust.confidence === 'medium') {
    const assumptions = message.trust.assumptions.length
    return assumptions > 0
      ? `${assumptions} assumption${assumptions === 1 ? '' : 's'} made — review before acting.`
      : 'Some signals estimated.'
  }
  return ''
}

export const describeSourceRelevance = (source: AssistantSource, message: AssistantMessage) => {
  if (source.relevance_reason) {
    return source.relevance_reason
  }
  if (source.type === 'state') {
    return 'Used as current company context.'
  }

  if (source.type === 'artifact') {
    return message.response_type === 'report'
      ? 'Used to benchmark against prior internal reporting.'
      : 'Used as prior internal analysis for this explanation.'
  }

  if (source.snippet) {
    return 'Direct supporting evidence for the conclusion.'
  }

  return 'Supporting company material used in the answer.'
}

export const cleanRichText = (value?: string) =>
  (value || '')
    .replace(/^---\n[\s\S]*?\n---\n*/m, '')
    .replace(/\[\[[A-Z0-9_]+\]\]/g, '')
    .replace(/\[(VERIFIED|CEO_DIRECTIVE)\]/g, '')
    .replace(/^\[\[\d+\]\]\s*/gm, '')
    // Strip planning context leak: [Context: ...] blocks the LLM occasionally emits
    .replace(/\[Context:[^\]]{0,400}\]/g, '')
    // Strip "Follow-up action:" prefix artifact
    .replace(/^Follow-up action:\s*/i, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .trim()

export const cleanUserVisibleQuery = (value?: string) =>
  (value || '')
    .replace(/^\[Context:[\s\S]{0,2400}?\]\s*/i, '')
    .replace(/^\[Original question:[\s\S]{0,2400}?\]\s*CEO context:\s*/i, '')
    .replace(/^CEO follow-up:\s*/i, '')
    .replace(/^Follow-up action:\s*/i, '')
    .trim()

const isTableLine = (line: string) => /^\|.*\|$/.test(line.trim())

const parseTableRow = (line: string) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())

export const parseRichBlocks = (value?: string): RichBlock[] => {
  const text = cleanRichText(value)
  if (!text) {
    return []
  }

  const lines = text.split('\n')
  const blocks: RichBlock[] = []
  let paragraphBuffer: string[] = []
  let listBuffer: string[] = []

  const flushParagraph = () => {
    if (paragraphBuffer.length > 0) {
      blocks.push({ type: 'paragraph', content: paragraphBuffer.join(' ').trim() })
      paragraphBuffer = []
    }
  }

  const flushList = () => {
    if (listBuffer.length > 0) {
      blocks.push({ type: 'list', items: [...listBuffer] })
      listBuffer = []
    }
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim()
    if (!line) {
      flushParagraph()
      flushList()
      continue
    }

    if (isTableLine(line)) {
      flushParagraph()
      flushList()
      const tableLines = [line]
      while (index + 1 < lines.length && isTableLine(lines[index + 1].trim())) {
        index += 1
        tableLines.push(lines[index].trim())
      }
      const headers = parseTableRow(tableLines[0])
      const rows = tableLines
        .slice(1)
        .filter((tableLine) => !/^(\|\s*:?-+:?\s*)+\|$/.test(tableLine))
        .map(parseTableRow)
      blocks.push({ type: 'table', headers, rows })
      continue
    }

    if (line.startsWith('### ')) {
      flushParagraph()
      flushList()
      blocks.push({ type: 'heading', level: 3, content: line.replace(/^###\s+/, '') })
      continue
    }
    if (line.startsWith('## ')) {
      flushParagraph()
      flushList()
      blocks.push({ type: 'heading', level: 2, content: line.replace(/^##\s+/, '') })
      continue
    }
    if (line.startsWith('# ')) {
      flushParagraph()
      flushList()
      blocks.push({ type: 'heading', level: 1, content: line.replace(/^#\s+/, '') })
      continue
    }

    if (/^[-*•]\s+/.test(line)) {
      flushParagraph()
      listBuffer.push(line.replace(/^[-*•]\s+/, '').trim())
      continue
    }

    paragraphBuffer.push(line)
  }

  flushParagraph()
  flushList()
  return blocks
}

export const groupExecutiveSections = (message: AssistantMessage): ExecutiveGrouping => {
  const presentedPriorities = toAnswerSections(message.presentation?.priorities)
  const presentedActions = toAnswerSections(message.presentation?.recommended_actions)
  const presentedRisks = toAnswerSections(message.presentation?.risks)
  const presentedDetails = toAnswerSections(message.presentation?.details)

  if (presentedPriorities.length > 0 || presentedActions.length > 0 || presentedRisks.length > 0 || presentedDetails.length > 0) {
    return {
      priorities: createGroup('Priorities', presentedPriorities),
      recommendedActions: createGroup('Recommended Actions', presentedActions),
      risks: createGroup('Risks And Gaps', presentedRisks),
      details: presentedDetails.length > 0 ? [{ title: 'Additional Detail', sections: presentedDetails }] : [],
    }
  }

  const priorities: AnswerSection[] = []
  const recommendedActions: AnswerSection[] = []
  const risks: AnswerSection[] = []
  const details: AnswerSection[] = []

  for (const section of message.answer.sections) {
    const bucket = resolveSectionBucket(section)
    if (bucket === 'priority') { priorities.push(section); continue }
    if (bucket === 'action') { recommendedActions.push(section); continue }
    if (bucket === 'risk') { risks.push(section); continue }
    details.push(section)
  }

  if (priorities.length === 0 && message.answer.sections[0]) {
    priorities.push(message.answer.sections[0])
  }
  if (recommendedActions.length === 0 && message.answer.sections[1]) {
    recommendedActions.push(message.answer.sections[1])
  }
  if (risks.length === 0 && message.answer.sections[2]) {
    risks.push(message.answer.sections[2])
  }

  const consumed = new Set([...priorities, ...recommendedActions, ...risks])
  const remainingDetails = [...details, ...message.answer.sections.filter((section) => !consumed.has(section))]
    .filter((section, index, array) => array.findIndex((candidate) => candidate.label === section.label) === index)

  return {
    priorities: createGroup('Priorities', priorities),
    recommendedActions: createGroup('Recommended Actions', recommendedActions),
    risks: createGroup('Risks And Gaps', risks),
    details: remainingDetails.length > 0 ? [{ title: 'Additional Detail', sections: remainingDetails }] : [],
  }
}

export const collectRiskSignals = (message: AssistantMessage) => {
  // Always prefer actual questions — they're the only thing worth making interactive
  if (message.trust.open_questions.length > 0) {
    return message.trust.open_questions.slice(0, 4)
  }

  // Fall back to section risks from the answer body
  const grouped = groupExecutiveSections(message).risks?.sections ?? []
  return grouped.flatMap((section) => section.items || []).slice(0, 4)
}

export const getExecutiveSummary = (message: AssistantMessage) => message.presentation?.summary || message.answer.summary

export const getWeeklyPlanPresentation = (message: AssistantMessage) => message.presentation?.weekly_plan || null

export const getPlanningWindowSpanDays = (message: AssistantMessage) => {
  const window = getWeeklyPlanPresentation(message)?.planning_window
  if (!window) return null
  if (typeof window.span_days === 'number' && Number.isFinite(window.span_days) && window.span_days > 0) {
    return window.span_days
  }
  if (!window.start_date || !window.end_date) return null
  const start = new Date(window.start_date)
  const end = new Date(window.end_date)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null
  const span = Math.round((end.getTime() - start.getTime()) / 86400000) + 1
  return span > 0 ? span : null
}

export type ScheduleLayoutMode = 'timeline' | 'compact_report' | 'overview'

export const getScheduleLayoutMode = (message: AssistantMessage): ScheduleLayoutMode => {
  const spanDays = getPlanningWindowSpanDays(message)
  if (spanDays !== null && spanDays <= 2) return 'timeline'
  if (spanDays !== null && spanDays > 14) return 'overview'
  return 'compact_report'
}
