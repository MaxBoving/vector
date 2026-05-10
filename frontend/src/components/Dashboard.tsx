import React, { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { cleanRichText, parseRichBlocks } from './messages/messagePresentation'
import type { AssistantMessage } from './messages/types'
import { DashboardShell } from './dashboard/DashboardShell'
import { ThreadPane } from './dashboard/ThreadPane'
import {
  Archive,
  ArrowDown,
  ArrowRight,
  CalendarClock,
  Check,
  ChevronRight,
  Download,
  Edit3,
  FileUp,
  History,
  Loader2,
  LogOut,
  Mail,
  MessageSquareText,
  Moon,
  Paperclip,
  Pin,
  Plus,
  Sun,
  Trash2,
  X,
} from './dashboard/icons'
import type {
  ArtifactPreview,
  CompanyIdentityProfile,
  IntegrationStatus,
  KnowledgeDocument,
  LoginState,
  Project,
  QueryPlaybook,
  SavedConversation,
  SuggestedAction,
  UploadMode,
  WorkbookPreviewModel,
  WorkbookSheetViewModel,
  WorkbookViewResponse,
  WorkbookViewTab,
  WorkbookViewerState,
} from './dashboard/types'
import '../styles/App.css'

const WorkbookWorkspace = lazy(() =>
  import('./dashboard/WorkbookWorkspace').then((module) => ({ default: module.WorkbookWorkspace })),
)

const isProtectedConversationId = (conversationId: string) =>
  conversationId.startsWith('default:') || conversationId.endsWith(':primary')

const getProjectForConversation = (projects: Project[], conversationId?: string | null) =>
  projects.find((project) => conversationId && project.conversation_ids.includes(conversationId)) || null

const summarizeComparisonBasis = (view?: WorkbookViewResponse) => {
  const metadata = view?.metadata
  const periodCoverage = metadata?.period_coverage
  const comparisonPair = periodCoverage?.comparison_pairs?.[0]
  const basis = metadata?.comparison_basis?.[0]
  const periods = periodCoverage?.periods || []

  return {
    periods,
    hasComparison: Boolean(periodCoverage?.has_comparison || comparisonPair),
    comparisonLabel:
      comparisonPair?.prior && comparisonPair?.current
        ? `${comparisonPair.current} vs ${comparisonPair.prior}`
        : null,
    basisRef: basis?.source_ref || null,
    basisExcerpt: basis?.source_excerpt || null,
  }
}

const summarizeInlineComparisonBasis = (
  artifactId: string,
  preview: WorkbookPreviewModel,
  workbookViews: Record<string, WorkbookViewResponse>,
) => {
  const cachedSummary = summarizeComparisonBasis(workbookViews[artifactId])
  if (cachedSummary.hasComparison || cachedSummary.periods.length > 0) {
    return cachedSummary
  }

  const varianceSheet = (preview.sheets || []).find((sheet) => sheet.name === 'Variance' || sheet.kind === 'variance')
  const comparisonTable = varianceSheet?.tables?.find((table) => table.title === 'Period Comparison')
  const firstComparisonRow = comparisonTable?.rows?.[0]
  const inferredPeriods = new Set<string>()
  if (firstComparisonRow?.[1]) {
    inferredPeriods.add(String(firstComparisonRow[1]))
  }
  if (firstComparisonRow?.[2]) {
    inferredPeriods.add(String(firstComparisonRow[2]))
  }

  return {
    periods: Array.from(inferredPeriods),
    hasComparison: Boolean(firstComparisonRow),
    comparisonLabel:
      firstComparisonRow?.[2] && firstComparisonRow?.[1]
        ? `${String(firstComparisonRow[2])} vs ${String(firstComparisonRow[1])}`
        : null,
    basisRef: null,
    basisExcerpt: null,
  }
}

const TOKEN_STORAGE_KEY = 'agenticmind.authToken'
const THEME_STORAGE_KEY = 'agenticmind.theme'
const SUGGESTED_ACTIONS: SuggestedAction[] = [
  {
    label: 'Board-ready report',
    prompt: 'Generate a board-ready report on ',
    description: 'Use this for executive summaries, financial check-ins, and board-style updates.',
  },
  {
    label: 'Analyze a document',
    prompt: 'Analyze this document and explain ',
    description: 'Use this for contracts, policies, audits, memos, and uploaded company materials.',
  },
  {
    label: 'Explain implications',
    prompt: 'Explain the implications of ',
    description: 'Use this when you want risks, tradeoffs, and recommended decisions from existing context.',
  },
  {
    label: 'Morning brief',
    prompt: 'Prepare a morning brief for ',
    description: 'Use this for a concise read on priorities, risks, and decisions that need attention.',
  },
]

const shuffleSuggestedActions = () => {
  const items = [...SUGGESTED_ACTIONS]
  for (let index = items.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1))
    ;[items[index], items[swapIndex]] = [items[swapIndex], items[index]]
  }
  return items
}

const QUERY_PLAYBOOKS: QueryPlaybook[] = [
  {
    label: 'Generate Report',
    prompt: 'Generate an executive report on ',
  },
  {
    label: 'Analyze Document',
    prompt: 'Analyze this document and explain ',
  },
  {
    label: 'Explain Implications',
    prompt: 'Explain the implications of ',
  },
  {
    label: 'Morning Brief',
    prompt: 'Prepare a morning brief for ',
  },
]

const parseJsonSafely = async <T,>(response: Response): Promise<T | null> => {
  const text = await response.text()
  if (!text) {
    return null
  }

  try {
    return JSON.parse(text) as T
  } catch {
    return null
  }
}

const renderRichContent = (value?: string) => {
  const blocks = parseRichBlocks(value)
  if (blocks.length === 0) {
    return null
  }

  return (
    <div className="rich-content">
      {blocks.map((block, index) => {
        if (block.type === 'heading') {
          if (block.level === 1) {
            return <h4 key={`${block.type}-${index}`} className="rich-heading rich-heading-lg">{block.content}</h4>
          }
          if (block.level === 2) {
            return <h5 key={`${block.type}-${index}`} className="rich-heading">{block.content}</h5>
          }
          return <h6 key={`${block.type}-${index}`} className="rich-heading rich-heading-sm">{block.content}</h6>
        }

        if (block.type === 'list') {
          return (
            <ul key={`${block.type}-${index}`} className="rich-list">
              {block.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )
        }

        if (block.type === 'table') {
          return (
            <div key={`${block.type}-${index}`} className="rich-table-wrap">
              <table className="rich-table">
                <thead>
                  <tr>
                    {block.headers.map((header) => (
                      <th key={header}>{header}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr key={`${rowIndex}-${row.join('-')}`}>
                      {row.map((cell, cellIndex) => (
                        <td key={`${rowIndex}-${cellIndex}`}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }

        return (
          <p key={`${block.type}-${index}`} className="rich-paragraph">
            {block.content}
          </p>
        )
      })}
    </div>
  )
}

export const Dashboard: React.FC = () => {
  const [token, setToken] = useState<string | null>(null)
  const [conversations, setConversations] = useState<SavedConversation[]>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null)
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const [integrations, setIntegrations] = useState<IntegrationStatus[]>([])
  const [selectedDocuments, setSelectedDocuments] = useState<KnowledgeDocument[]>([])
  const [identityProfile, setIdentityProfile] = useState<CompanyIdentityProfile | null>(null)
  const [uploadMode, setUploadMode] = useState<UploadMode>('reference')
  const [lastAttachedTitle, setLastAttachedTitle] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [newProjectName, setNewProjectName] = useState('')
  const [showProjectComposer, setShowProjectComposer] = useState(false)
  const [login, setLogin] = useState<LoginState>({ username: 'max', password: 'password123' })
  const [starterActions] = useState<SuggestedAction[]>(() => shuffleSuggestedActions())
  const [isSending, setIsSending] = useState(false)
  const [pendingQuery, setPendingQuery] = useState<string | null>(null)
  const [pendingAttachments, setPendingAttachments] = useState<KnowledgeDocument[]>([])
  const [uploadFeed, setUploadFeed] = useState<Array<{ id: string; title: string; summary: string | null }>>([])
  const [isUploading, setIsUploading] = useState(false)
  const [deletingConversationId, setDeletingConversationId] = useState<string | null>(null)
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(null)
  const [confirmingProjectDeleteId, setConfirmingProjectDeleteId] = useState<string | null>(null)
  const [editingConversationId, setEditingConversationId] = useState<string | null>(null)
  const [editingConversationTitle, setEditingConversationTitle] = useState('')
  const [updatingConversationId, setUpdatingConversationId] = useState<string | null>(null)
  const [resolvingMessageId, setResolvingMessageId] = useState<string | null>(null)
  const [eventLoading, setEventLoading] = useState<'email' | 'calendar' | 'morning' | null>(null)
  const [connectingService, setConnectingService] = useState<IntegrationStatus['service'] | null>(null)
  const [approvalNotes, setApprovalNotes] = useState<Record<string, string>>({})
  const [isAuthenticating, setIsAuthenticating] = useState(false)
  const [loadState, setLoadState] = useState<'signed_out' | 'loading' | 'ready' | 'error'>('signed_out')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const [artifactPreviews, setArtifactPreviews] = useState<Record<string, ArtifactPreview>>({})
  const [activeArtifactSheets, setActiveArtifactSheets] = useState<Record<string, string>>({})
  const [activeArtifactViewTabs, setActiveArtifactViewTabs] = useState<Record<string, WorkbookViewTab>>({})
  const [artifactPaneWidths, setArtifactPaneWidths] = useState<Record<string, number>>({})
  const [activeProvenanceRows, setActiveProvenanceRows] = useState<Record<string, { tableTitle: string; rowIndex: number }>>({})
  const [workbookViews, setWorkbookViews] = useState<Record<string, WorkbookViewResponse>>({})
  const [workbookViewLoadingId, setWorkbookViewLoadingId] = useState<string | null>(null)
  const [previewLoadingId, setPreviewLoadingId] = useState<string | null>(null)
  const [workbookViewer, setWorkbookViewer] = useState<WorkbookViewerState | null>(null)
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null)
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof window === 'undefined') {
      return 'light'
    }
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY)
    return storedTheme === 'dark' ? 'dark' : 'light'
  })
  const fileInputRef = useRef<HTMLInputElement>(null)
  const threadEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const messageRefs = useRef<Record<string, HTMLElement | null>>({})

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  }, [theme])

  useEffect(() => {
    const storedToken = window.localStorage.getItem(TOKEN_STORAGE_KEY)
    if (!storedToken) {
      return
    }

    setToken(storedToken)
    setLoadState('loading')
  }, [])

  useEffect(() => {
    if (!token) {
      return
    }

    setLoadState('loading')
    void initializeWorkspace(token)
      .then(() => {
        setLoadState('ready')
        setErrorMessage(null)
      })
      .catch((error: unknown) => {
        console.error(error)
        window.localStorage.removeItem(TOKEN_STORAGE_KEY)
        setToken(null)
        setConversations([])
        setActiveConversationId(null)
        setProjects([])
        setActiveProjectId(null)
        setMessages([])
        setDocuments([])
        setIntegrations([])
        setIdentityProfile(null)
        setSelectedDocuments([])
        setLoadState('error')
        setErrorMessage(error instanceof Error ? error.message : 'Unable to load the assistant workspace.')
      })
  }, [token])

  useEffect(() => {
    if (!activeProjectId) {
      setSelectedDocuments([])
      return
    }

    const activeProject = projects.find((project) => project.project_id === activeProjectId)
    if (!activeProject) {
      setSelectedDocuments([])
      return
    }

    const projectDocs = activeProject.document_ids
      .map((documentId) => documents.find((document) => document.document_id === documentId))
      .filter((document): document is KnowledgeDocument => Boolean(document))

    setSelectedDocuments(projectDocs.slice(0, 3))
  }, [activeProjectId, documents, projects])

  useEffect(() => {
    const updateScrollState = () => {
      const documentHeight = document.documentElement.scrollHeight
      const viewportBottom = window.scrollY + window.innerHeight
      const distanceFromBottom = documentHeight - viewportBottom
      setShowScrollToBottom(distanceFromBottom > 720)
    }

    updateScrollState()
    window.addEventListener('scroll', updateScrollState, { passive: true })
    window.addEventListener('resize', updateScrollState)

    return () => {
      window.removeEventListener('scroll', updateScrollState)
      window.removeEventListener('resize', updateScrollState)
    }
  }, [messages])

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) {
      return
    }

    textarea.style.height = '0px'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`
  }, [query])

  useEffect(() => {
    if (!workbookViewer) {
      return
    }

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setWorkbookViewer(null)
      }
    }

    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [workbookViewer])

  useEffect(() => {
    if (!confirmingProjectDeleteId) {
      return
    }

    const timeout = window.setTimeout(() => {
      setConfirmingProjectDeleteId((current) => (current === confirmingProjectDeleteId ? null : current))
    }, 4000)

    return () => window.clearTimeout(timeout)
  }, [confirmingProjectDeleteId])

  useEffect(() => {
    if (pendingQuery) {
      threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [pendingQuery])

  useEffect(() => {
    if (!token || !activeConversationId || !messages.some((message) => message.status === 'pending')) {
      return
    }

    const interval = window.setInterval(() => {
      void fetchConversation(activeConversationId, token).catch((error: unknown) => console.error(error))
    }, 2000)

    return () => window.clearInterval(interval)
  }, [activeConversationId, messages, token])

  useEffect(() => {
    const fill = (prompt: string) => {
      setQuery(prompt)
      setTimeout(() => {
        textareaRef.current?.focus()
        const len = textareaRef.current?.value.length ?? 0
        textareaRef.current?.setSelectionRange(len, len)
      }, 0)
    }

    const handler = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, string>>).detail
      const { intent } = detail

      if (intent === 'draft-reply') {
        const ref = detail.threadId ? ` (thread ID: ${detail.threadId})` : ''
        fill(`Draft a reply to the email thread "${detail.subject}"${ref}`)
      } else if (intent === 'summarize-email') {
        const ref = detail.threadId ? ` (thread ID: ${detail.threadId})` : ''
        fill(`Summarize the email "${detail.subject}"${ref} — break it into concrete asks, deliverables, and todos`)
      } else if (intent === 'schedule-prep') {
        fill(`Prepare a meeting prep brief for "${detail.title}"`)
      } else if (intent === 'finance-drill') {
        fill(`Drill down on: "${detail.signal}" — what is driving this and what should I do about it?`)
      } else if (intent === 'finance-scenario') {
        fill(`Run a scenario where ${detail.label} changes — what is the downstream impact on the financials?`)
      } else if (intent === 'report-deeper') {
        fill(`Go deeper on "${detail.section}" — expand with more detail and supporting evidence`)
      } else if (intent === 'draft-revise') {
        fill(`Revise this draft to be more ${detail.tone} — keep the core message but adjust the tone throughout`)
      }
    }

    document.addEventListener('compose-action', handler)
    return () => document.removeEventListener('compose-action', handler)
  }, [])

  const authenticatedFetch = async (path: string, init?: RequestInit, authToken?: string) => {
    const activeToken = authToken ?? token
    if (!activeToken) {
      throw new Error('Not authenticated.')
    }

    const response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.headers ?? {}),
        Authorization: `Bearer ${activeToken}`,
      },
    })

    if (response.status === 401) {
      throw new Error('Your session expired. Sign in again.')
    }

    return response
  }

  const fetchConversation = async (conversationId: string, authToken?: string) => {
    const response = await authenticatedFetch(`/assistant/conversations/${conversationId}`, undefined, authToken)
    const data = await response.json()

    if (!response.ok) {
      throw new Error(data.detail || 'Unable to load the conversation.')
    }

    setMessages(Array.isArray(data.messages) ? data.messages : [])
  }

  const fetchConversations = async (authToken?: string) => {
    const response = await authenticatedFetch('/assistant/conversations', undefined, authToken)
    const data = await response.json()

    if (!response.ok) {
      throw new Error(data.detail || 'Unable to load conversations.')
    }

    const nextConversations = Array.isArray(data) ? data : []
    setConversations(nextConversations)
    return nextConversations as SavedConversation[]
  }

  const createConversation = async (authToken?: string) => {
    const response = await authenticatedFetch(
      '/assistant/conversations',
      {
        method: 'POST',
      },
      authToken,
    )
    const data = await response.json()

    if (!response.ok) {
      throw new Error(data.detail || 'Unable to start a new conversation.')
    }

    return data as SavedConversation
  }

  const deleteConversation = async (conversationId: string, authToken?: string) => {
    if (isProtectedConversationId(conversationId)) {
      return
    }

    const response = await authenticatedFetch(
      `/assistant/conversations/${conversationId}`,
      {
        method: 'DELETE',
      },
      authToken,
    )
    const data = await parseJsonSafely<{ detail?: string }>(response)

    if (!response.ok) {
      if (response.status === 404 && isProtectedConversationId(conversationId)) {
        return
      }
      throw new Error(data?.detail || 'Unable to delete conversation.')
    }
  }

  const updateConversation = async (
    conversationId: string,
    updates: { title?: string; pinned?: boolean; archived?: boolean },
    authToken?: string,
  ) => {
    const response = await authenticatedFetch(
      `/assistant/conversations/${conversationId}`,
      {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(updates),
      },
      authToken,
    )
    const data = await parseJsonSafely<SavedConversation & { detail?: string }>(response)
    if (!response.ok || !data) {
      throw new Error(data?.detail || 'Unable to update conversation.')
    }
    return data
  }

  const fetchProjects = async (authToken?: string) => {
    const response = await authenticatedFetch('/assistant/projects', undefined, authToken)
    const data = await response.json()

    if (!response.ok) {
      throw new Error(data.detail || 'Unable to load projects.')
    }

    const nextProjects = Array.isArray(data) ? data : []
    setProjects(nextProjects)
    return nextProjects as Project[]
  }

  const createProject = async (name: string, authToken?: string) => {
    const response = await authenticatedFetch(
      '/assistant/projects',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name }),
      },
      authToken,
    )
    const data = await response.json()

    if (!response.ok) {
      throw new Error(data.detail || 'Unable to create project.')
    }

    return data as Project
  }

  const deleteProject = async (projectId: string, authToken?: string) => {
    const response = await authenticatedFetch(
      `/assistant/projects/${projectId}`,
      {
        method: 'DELETE',
      },
      authToken,
    )
    const data = await parseJsonSafely<{ detail?: string }>(response)
    if (!response.ok) {
      throw new Error(data?.detail || 'Unable to delete project.')
    }
  }

  const saveProject = async (projectId: string, updates: Partial<Pick<Project, 'name' | 'description' | 'document_ids' | 'conversation_ids'>>) => {
    const response = await authenticatedFetch(`/assistant/projects/${projectId}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(updates),
    })
    const data = await response.json()

    if (!response.ok) {
      throw new Error(data.detail || 'Unable to update project.')
    }

    setProjects((current) =>
      current.map((project) => (project.project_id === projectId ? data : project)),
    )
    return data as Project
  }

  const initializeWorkspace = async (authToken?: string) => {
    const existingConversations = await fetchConversations(authToken)
    const existingProjects = await fetchProjects(authToken)
    await Promise.all([fetchDocuments(authToken), fetchIntegrations(authToken), fetchIdentityProfile(authToken)])
    const freshConversation = await createConversation(authToken)
    const nextConversations = [freshConversation, ...existingConversations.filter((item) => item.conversation_id !== freshConversation.conversation_id)]
    setConversations(nextConversations)
    setActiveConversationId(freshConversation.conversation_id)
    setActiveProjectId(existingProjects[0]?.project_id ?? null)
    setMessages([])
  }

  const fetchDocuments = async (authToken?: string) => {
    const response = await authenticatedFetch('/documents', undefined, authToken)
    const data = await response.json()

    if (!response.ok) {
      throw new Error(data.detail || 'Unable to load documents.')
    }

    const nextDocuments = Array.isArray(data) ? data : []
    setDocuments(nextDocuments)
    setSelectedDocuments((current) =>
      current.filter((selected) => nextDocuments.some((doc) => doc.document_id === selected.document_id)),
    )
  }

  const fetchIntegrations = async (authToken?: string) => {
    const response = await authenticatedFetch('/integrations', undefined, authToken)
    const data = await response.json()

    if (!response.ok) {
      throw new Error(data.detail || 'Unable to load integrations.')
    }

    setIntegrations(Array.isArray(data) ? data : [])
  }

  const fetchIdentityProfile = async (authToken?: string) => {
    const response = await authenticatedFetch('/identity/profile', undefined, authToken)
    const data = await parseJsonSafely<CompanyIdentityProfile & { detail?: string }>(response)

    if (!response.ok) {
      throw new Error(data?.detail || 'Unable to load company identity.')
    }

    if (!data) {
      throw new Error('Company identity endpoint returned a non-JSON response.')
    }

    setIdentityProfile(data)
  }

  const submitLogin = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isAuthenticating) {
      return
    }

    setIsAuthenticating(true)
    setErrorMessage(null)

    try {
      const formData = new FormData()
      formData.append('username', login.username)
      formData.append('password', login.password)

      const response = await fetch('/auth/login', { method: 'POST', body: formData })
      const data = await parseJsonSafely<{ access_token?: string; detail?: string }>(response)

      if (!response.ok || !data?.access_token) {
        throw new Error(data?.detail || 'Unable to authenticate. Check that the backend is running and returning JSON.')
      }

      window.localStorage.setItem(TOKEN_STORAGE_KEY, data.access_token)
      setToken(data.access_token)
    } catch (error) {
      console.error(error)
      setLoadState('error')
      setErrorMessage(error instanceof Error ? error.message : 'Unable to authenticate.')
    } finally {
      setIsAuthenticating(false)
    }
  }

  const signOut = () => {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY)
    setToken(null)
    setConversations([])
    setActiveConversationId(null)
    setProjects([])
    setActiveProjectId(null)
    setMessages([])
    setDocuments([])
    setIntegrations([])
    setIdentityProfile(null)
    setSelectedDocuments([])
    setLastAttachedTitle(null)
    setLoadState('signed_out')
    setErrorMessage(null)
  }

  const submitQuery = async (
    override?: string | {
      requestText: string
      displayText?: string
      follow_up_context?: ClarificationFollowUpContext | null
    },
  ) => {
    const requestText = typeof override === 'string' ? override : override?.requestText
    const displayText = typeof override === 'object' ? override.displayText : undefined
    const followUpContext = typeof override === 'object' ? override.follow_up_context : null
    const trimmed = (requestText ?? query).trim()
    const visibleQuery = (displayText ?? requestText ?? query).trim()
    if (!trimmed || !token || isSending || !activeConversationId) {
      return
    }

    setIsSending(true)
    setErrorMessage(null)
    setQuery('')
    setPendingQuery(visibleQuery || trimmed)
    setPendingAttachments([...selectedDocuments])

    try {
      const response = await authenticatedFetch('/assistant/query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: trimmed,
          conversation_id: activeConversationId,
          project_id: activeProjectId,
          attachments: selectedDocuments.map((document) => ({
            document_id: document.document_id,
            filename: document.title,
          })),
          options: {
            response_mode: 'auto',
            include_sources: true,
          },
          follow_up_context: followUpContext,
        }),
      })

      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Unable to submit request.')
      }

      setMessages((current) => [...current, data])
      setPendingQuery(null)
      setPendingAttachments([])
      if (activeProjectId && activeConversationId) {
        const activeProject = projects.find((project) => project.project_id === activeProjectId)
        const nextConversationIds = Array.from(new Set([...(activeProject?.conversation_ids || []), activeConversationId]))
        await saveProject(activeProjectId, { conversation_ids: nextConversationIds })
      }
      await fetchConversations()
    } catch (error) {
      console.error(error)
      setPendingQuery(null)
      setPendingAttachments([])
      setErrorMessage(error instanceof Error ? error.message : 'Unable to submit request.')
    } finally {
      setIsSending(false)
      if (activeConversationId) {
        void fetchConversation(activeConversationId).catch((error: unknown) => console.error(error))
      }
    }
  }

  const uploadDocument = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file || !token) {
      return
    }

    setIsUploading(true)
    setErrorMessage(null)

    try {
      const formData = new FormData()
      formData.append('file', file)
      const uploadConfig =
        uploadMode === 'report_example'
          ? { purpose: 'example_material', identity_role: 'report_example' }
          : uploadMode === 'workbook_example'
            ? { purpose: 'example_material', identity_role: 'workbook_example' }
            : uploadMode === 'brand_reference'
              ? { purpose: 'example_material', identity_role: 'brand_reference' }
              : { purpose: 'reference', identity_role: '' }

      const response = await authenticatedFetch(
        `/documents/upload?title=${encodeURIComponent(file.name)}&purpose=${encodeURIComponent(uploadConfig.purpose)}&identity_role=${encodeURIComponent(uploadConfig.identity_role)}`,
        {
        method: 'POST',
        body: formData,
      })

      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Upload failed.')
      }

      await Promise.all([fetchDocuments(), fetchIdentityProfile()])
      const uploadedDocument = {
        document_id: data.document_id,
        title: data.title,
        status: data.status,
        intel_summary: data.intel_summary,
        domains: [],
        purpose: data.purpose,
        identity_role: data.identity_role,
      }

      if (activeProjectId) {
        const activeProject = projects.find((project) => project.project_id === activeProjectId)
        const nextDocumentIds = Array.from(new Set([...(activeProject?.document_ids || []), uploadedDocument.document_id]))
        await saveProject(activeProjectId, { document_ids: nextDocumentIds })
      }

      setSelectedDocuments((current) => {
        if (current.some((document) => document.document_id === uploadedDocument.document_id)) {
          return current
        }
        return [uploadedDocument, ...current]
      })
      setLastAttachedTitle(data.title)
      setUploadFeed((current) => [
        ...current,
        { id: data.document_id, title: data.title, summary: data.intel_summary || null },
      ])
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Upload failed.')
    } finally {
      setIsUploading(false)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  const toggleDocument = (document: KnowledgeDocument) => {
    setSelectedDocuments((current) => {
      const exists = current.some((item) => item.document_id === document.document_id)
      const nextSelection = exists ? current.filter((item) => item.document_id !== document.document_id) : [document, ...current].slice(0, 3)
      if (activeProjectId) {
        void saveProject(activeProjectId, { document_ids: nextSelection.map((item) => item.document_id) }).catch((error: unknown) =>
          console.error(error),
        )
      }
      return nextSelection
    })
  }

  const applySuggestedPrompt = async (prompt: string) => {
    if (token) {
      await startFreshConversation()
    }
    setQuery(prompt)
    textareaRef.current?.focus()
  }

  const runEventWorkflow = async (
    kind: 'email' | 'calendar' | 'morning',
    path: string,
    body: Record<string, unknown>,
  ) => {
    if (!token || eventLoading) {
      return
    }

    setEventLoading(kind)
    setErrorMessage(null)

    try {
      const response = await authenticatedFetch(path, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body ?? {}),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Unable to run workflow.')
      }
      setMessages((current) => [...current, data])
      if (activeConversationId) {
        await fetchConversation(activeConversationId)
      }
      await fetchConversations()
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to run workflow.')
    } finally {
      setEventLoading(null)
    }
  }

  const triggerMorningBrief = async () => {
    const now = new Date()
    await runEventWorkflow('morning', '/briefings/morning', {
      scheduled_for: now.toISOString(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    })
  }

  const triggerCalendarBrief = async () => {
    await runEventWorkflow('calendar', '/events/calendar', {})
  }

  const triggerEmailBrief = async () => {
    await runEventWorkflow('email', '/events/email', {})
  }

  const connectIntegration = async (service: IntegrationStatus['service']) => {
    if (!token || connectingService) {
      return
    }

    setConnectingService(service)
    setErrorMessage(null)

    try {
      const response = await authenticatedFetch(`/integrations/${service}/connect`, {
        method: 'POST',
      })
      const data = await response.json()
      if (!response.ok || !data.auth_url) {
        throw new Error(data.detail || 'Unable to start provider connection.')
      }
      window.location.href = data.auth_url
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to start provider connection.')
      setConnectingService(null)
    }
  }

  const handleMessageIntegrationConnect = (value: string) => {
    if (value === 'connect_google_workspace') {
      void connectIntegration('gmail')
      return
    }
    if (value === 'connect_outlook_workspace') {
      void connectIntegration('outlook_mail')
    }
  }

  type ClarificationFollowUpContext = {
    source_interaction_id?: number
    source_response_type?: 'clarification' | 'conversational' | 'report' | 'explanation' | 'brief' | 'schedule'
    selected_option_label?: string
    selected_option_value?: string
    selected_option_apply_text?: string
  }

  const normalizeClarificationText = (value: string) =>
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .trim()
      .replace(/\s+/g, ' ')

  const buildClarificationFollowUpContext = (
    message: AssistantMessage,
    text: string,
  ): ClarificationFollowUpContext | null => {
    if (message.response_type !== 'clarification') {
      return null
    }

    const normalizedText = normalizeClarificationText(text)
    const options = (message.trust.question_options ?? [])
      .flatMap((entry) => entry.options.map((option) => ({
        ...option,
        offer_type: entry.offer_type,
      })))
      .filter((option) => option.offer_type !== 'action_offer')

    const matchedOption = options.find((option) => {
      const candidates = [option.label, option.value, option.apply_text, option.description].filter(Boolean)
      return candidates.some((candidate) => normalizeClarificationText(String(candidate)) === normalizedText)
    })

    const context: ClarificationFollowUpContext = {
      source_interaction_id: message.metadata?.interaction_id,
      source_response_type: message.response_type,
    }

    if (matchedOption) {
      context.selected_option_label = matchedOption.label
      context.selected_option_value = matchedOption.value
      context.selected_option_apply_text = matchedOption.apply_text
    } else if (normalizedText) {
      context.selected_option_apply_text = text
    }

    return context
  }

  const queueFollowUp = (prompt: string) => {
    setQuery(prompt)
    textareaRef.current?.focus()
  }

  const extractNumericValue = (value?: string) => {
    if (!value) {
      return null
    }

    const normalized = value.replace(/[$,%]/g, '').replace(/,/g, '').trim()
    const parsed = Number.parseFloat(normalized)
    return Number.isFinite(parsed) ? parsed : null
  }

  const renderSimpleChart = (
    chart: NonNullable<NonNullable<WorkbookPreviewModel['sheets']>[number]['chart_specs']>[number],
    rows: string[][],
  ) => {
    const chartRows = rows
      .map((row) => ({
        label: row[0] || 'Item',
        value: extractNumericValue(row[1]),
      }))
      .filter((row): row is { label: string; value: number } => row.value !== null)
      .slice(0, 6)

    if (chartRows.length === 0) {
      return (
        <div className="analysis-chart-empty">
          <span>Chart preview unavailable</span>
          <p>The workbook includes this chart spec, but the preview rows are not numeric enough to render inline.</p>
        </div>
      )
    }

    const maxValue = Math.max(...chartRows.map((row) => row.value), 1)
    const sortedRows = [...chartRows].sort((left, right) => right.value - left.value)
    const callouts = sortedRows.length > 1
      ? [
          `Lead: ${sortedRows[0].label} at ${sortedRows[0].value.toLocaleString()}.`,
          `Range: ${(sortedRows[0].value - sortedRows[sortedRows.length - 1].value).toLocaleString()} across visible rows.`,
        ]
      : []
    return (
      <div className="analysis-chart-shell">
        <div className="analysis-chart-meta">
          <strong>{chart.title}</strong>
          <span>{chart.chart_type}</span>
        </div>
        {callouts.length > 0 ? (
          <div className="analysis-chart-callouts">
            {callouts.map((callout) => (
              <span key={`${chart.title}-${callout}`} className="analysis-chart-callout">
                {callout}
              </span>
            ))}
          </div>
        ) : null}
        <div className="analysis-chart-bars">
          {chartRows.map((row) => (
            <div key={`${chart.title}-${row.label}`} className="analysis-chart-row">
              <span className="analysis-chart-label">{row.label}</span>
              <div className="analysis-chart-track">
                <div className="analysis-chart-bar" style={{ width: `${Math.max((row.value / maxValue) * 100, 8)}%` }} />
              </div>
              <strong className="analysis-chart-value">{row.value}</strong>
            </div>
          ))}
        </div>
      </div>
    )
  }

  const buildPivotSnapshot = (table?: { columns: string[]; rows: string[][] }) => {
    if (!table || !table.rows.length || table.columns.length < 2) {
      return []
    }

    const grouped = new Map<string, number>()

    table.rows.forEach((row) => {
      const groupLabel = row[0] || 'Other'
      const numericCandidates = row
        .slice(1)
        .map((cell) => extractNumericValue(cell))
        .filter((value): value is number => value !== null)
      const numericValue = numericCandidates[numericCandidates.length - 1]
      if (numericValue === undefined) {
        return
      }
      grouped.set(groupLabel, (grouped.get(groupLabel) || 0) + numericValue)
    })

    return Array.from(grouped.entries())
      .map(([label, value]) => ({ label, value }))
      .sort((left, right) => right.value - left.value)
      .slice(0, 6)
  }

  const parseWorkbookPreviewModel = (content: string): WorkbookPreviewModel | null => {
    try {
      return JSON.parse(cleanRichText(content)) as WorkbookPreviewModel
    } catch {
      return null
    }
  }

  const workbookPreviewToViewResponse = (artifactId: string, preview: WorkbookPreviewModel): WorkbookViewResponse => ({
    artifact_id: artifactId,
    title: preview.workbook_title || 'Workbook Viewer',
    tabs: (preview.sheets || []).map((sheet) => ({
      name: sheet.name,
      kind: 'summary',
      metrics: sheet.metrics || [],
      tables: (sheet.tables || []).map((table) => ({
        ...table,
        row_provenance: [],
      })),
      charts: sheet.chart_specs || [],
      pivot_snapshots: [],
    })),
  })

  const getPrimaryWorkbookTable = (sheet: WorkbookSheetViewModel) =>
    sheet.tables?.find((table) => table.rows.length > 0) || sheet.tables?.[0]

  const isNumericWorkbookCell = (value?: string) => extractNumericValue(value) !== null

  const formatWorkbookCell = (value?: string) => {
    if (!value) {
      return '—'
    }
    const numericValue = extractNumericValue(value)
    if (numericValue === null) {
      return value
    }
    if (value.includes('%')) {
      return `${numericValue}%`
    }
    if (value.includes('$')) {
      return value
    }
    return Number.isInteger(numericValue) ? numericValue.toLocaleString() : numericValue.toLocaleString(undefined, { maximumFractionDigits: 2 })
  }

  const chartEntriesForSheet = (sheet: WorkbookSheetViewModel) => sheet.charts || sheet.chart_specs || []

  const pivotEntriesForSheet = (sheet: WorkbookSheetViewModel) =>
    (sheet.pivot_snapshots || []).map((snapshot) => ({
      label: snapshot.title,
      value: snapshot.rows.reduce((total, row) => total + row.value, 0),
    }))

  const provenanceToneClass = (sourceType?: string) => {
    if (sourceType === 'company_state') {
      return 'provenance-pill-state'
    }
    if (sourceType === 'retrieved_document') {
      return 'provenance-pill-document'
    }
    if (sourceType === 'derived_metric') {
      return 'provenance-pill-derived'
    }
    return 'provenance-pill-fallback'
  }

  const startPaneResize = (artifactId: string, startX: number) => {
    const startingWidth = artifactPaneWidths[artifactId] ?? 280

    const handleMove = (event: MouseEvent) => {
      const nextWidth = Math.min(420, Math.max(220, startingWidth + event.clientX - startX))
      setArtifactPaneWidths((current) => ({
        ...current,
        [artifactId]: nextWidth,
      }))
    }

    const handleUp = () => {
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', handleUp)
    }

    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', handleUp)
  }

  const summarizeWorkbookTab = (tab: WorkbookViewResponse['tabs'][number]) => {
    const primaryTable = tab.tables.find((table) => table.rows.length > 0) || tab.tables[0]
    const rowCount = primaryTable?.rows.length || 0
    const metricCount = tab.metrics.length
    const chartCount = tab.charts.length
    const pivotCount = tab.pivot_snapshots.length
    return { rowCount, metricCount, chartCount, pivotCount }
  }

  const lastNumericValue = (row: string[]) => {
    const numericCandidates = row
      .slice(1)
      .map((cell) => extractNumericValue(cell))
      .filter((value): value is number => value !== null)
    return numericCandidates[numericCandidates.length - 1] ?? null
  }

  const buildSheetInsights = (
    metrics: Array<{ label: string; value: string }>,
    primaryTable: { columns: string[]; rows: string[][] } | undefined,
    comparisonLabel: string | null,
    chartCount: number,
  ) => {
    const insights: string[] = []
    if (metrics[0]) insights.push(`${metrics[0].label} is currently ${metrics[0].value}.`)
    if (primaryTable?.rows.length) {
      const ranked = primaryTable.rows
        .map((row) => ({ label: row[0] || 'Item', value: lastNumericValue(row) }))
        .filter((row): row is { label: string; value: number } => row.value !== null)
        .sort((left, right) => right.value - left.value)
      if (ranked[0]) insights.push(`${ranked[0].label} is the strongest visible row in this view.`)
      if (ranked.length > 1) insights.push(`${ranked[ranked.length - 1].label} is the weakest visible row and the first candidate for drilldown.`)
    }
    if (comparisonLabel) insights.push(`Comparison basis is ${comparisonLabel}.`)
    if (chartCount > 0) insights.push(`${chartCount} chart${chartCount === 1 ? '' : 's'} available for this sheet.`)
    return insights.slice(0, 4)
  }

  const buildRowSnapshot = (columns: string[], row: string[]) =>
    columns.map((column, index) => ({ column, value: formatWorkbookCell(row[index]) }))

  const renderAnalysisSpecPreview = (artifactId: string, content: string, viewerMode = false) => {
    const spec = parseWorkbookPreviewModel(content)
    if (!spec) {
      return <pre className="artifact-preview-raw">{content}</pre>
    }

    if (!spec?.sheets?.length) {
      return null
    }

    const activeSheetName = activeArtifactSheets[artifactId] || spec.sheets[0].name
    const activeSheet = spec.sheets.find((sheet) => sheet.name === activeSheetName) || spec.sheets[0]
    const primaryTable = getPrimaryWorkbookTable(activeSheet)
    const pivotSnapshot = activeSheet.pivot_snapshots?.length ? pivotEntriesForSheet(activeSheet) : buildPivotSnapshot(primaryTable)
    const activeViewTab: WorkbookViewTab =
      activeArtifactViewTabs[artifactId] || (viewerMode && pivotSnapshot.length > 0 ? 'sheet' : 'sheet')
    const sidebarWidth = artifactPaneWidths[artifactId] ?? 280
    const workbookSummary = {
      metricCount: activeSheet.metrics?.length || 0,
      tableCount: activeSheet.tables?.length || 0,
      rowCount: primaryTable?.rows.length || 0,
      chartCount: chartEntriesForSheet(activeSheet).length,
      pivotCount: pivotSnapshot.length > 0 ? 1 : 0,
    }

    if (!viewerMode) {
      const visibleMetrics = (activeSheet.metrics || []).slice(0, 4)
      const visibleRows = primaryTable?.rows.slice(0, 6) || []
      const primaryChart = chartEntriesForSheet(activeSheet)[0]
      const inlineComparisonSummary = summarizeInlineComparisonBasis(artifactId, spec, workbookViews)
      const sheetInsights = buildSheetInsights(
        activeSheet.metrics || [],
        primaryTable,
        inlineComparisonSummary.comparisonLabel,
        chartEntriesForSheet(activeSheet).length,
      )

      return (
        <div className="analysis-spec-preview analysis-workbook-preview analysis-workbook-preview-inline">
          <div className="analysis-workbook-header">
            <div>
              {spec.workbook_title ? <h5 className="rich-heading">{spec.workbook_title}</h5> : null}
              <span className="artifact-preview-label">
                {activeSheet.name} • {activeSheet.tables?.length || 0} table{activeSheet.tables?.length === 1 ? '' : 's'}
              </span>
            </div>
            {spec.sheets.length > 1 ? (
              <div className="analysis-sheet-tabs" role="tablist" aria-label="Workbook sheets">
                {spec.sheets.map((sheet) => (
                  <button
                    key={`${artifactId}-${sheet.name}`}
                    type="button"
                    className={`analysis-sheet-tab ${sheet.name === activeSheet.name ? 'analysis-sheet-tab-active' : ''}`}
                    onClick={() =>
                      setActiveArtifactSheets((current) => ({
                        ...current,
                        [artifactId]: sheet.name,
                      }))
                    }
                  >
                    {sheet.name}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          <div className="analysis-workbook-overview">
            <div className="analysis-workbook-overview-card">
              <span>Metrics</span>
              <strong>{workbookSummary.metricCount}</strong>
            </div>
            <div className="analysis-workbook-overview-card">
              <span>Rows in focus</span>
              <strong>{workbookSummary.rowCount || '—'}</strong>
            </div>
            <div className="analysis-workbook-overview-card">
              <span>Charts</span>
              <strong>{workbookSummary.chartCount}</strong>
            </div>
            <div className="analysis-workbook-overview-card">
              <span>Pivot views</span>
              <strong>{workbookSummary.pivotCount}</strong>
            </div>
          </div>
          {sheetInsights.length > 0 ? (
            <div className="analysis-sheet-insights">
              {sheetInsights.map((insight) => (
                <div key={`${activeSheet.name}-${insight}`} className="analysis-sheet-insight-card">
                  {insight}
                </div>
              ))}
            </div>
          ) : null}
          {inlineComparisonSummary.hasComparison || inlineComparisonSummary.periods.length > 0 ? (
            <div className="inline-workbook-basis">
              <strong>
                {inlineComparisonSummary.comparisonLabel
                  ? `Comparison: ${inlineComparisonSummary.comparisonLabel}`
                  : 'Workbook period coverage'}
              </strong>
              {inlineComparisonSummary.basisExcerpt ? <span>{inlineComparisonSummary.basisExcerpt}</span> : null}
              {!inlineComparisonSummary.basisExcerpt && inlineComparisonSummary.basisRef ? (
                <span>{inlineComparisonSummary.basisRef}</span>
              ) : null}
            </div>
          ) : null}
          <div className="analysis-inline-grid analysis-workbook-stage">
            {visibleMetrics.length > 0 ? (
              <div className="analysis-metric-grid">
                {visibleMetrics.map((metric) => (
                  <div key={`${activeSheet.name}-${metric.label}`} className="analysis-metric-card">
                    <span>{metric.label}</span>
                    <strong>{metric.value}</strong>
                  </div>
                ))}
              </div>
            ) : null}

            {primaryTable ? (
              <div className="analysis-table-shell">
                <span className="artifact-preview-label">{primaryTable.title}</span>
                <div className="analysis-grid-shell">
                  <div className="analysis-grid-index-column">
                    <span className="analysis-grid-index-head">#</span>
                    {visibleRows.map((_, rowIndex) => (
                      <span key={`${primaryTable.title}-index-${rowIndex}`} className="analysis-grid-index-cell">
                        {rowIndex + 1}
                      </span>
                    ))}
                  </div>
                  <div className="analysis-grid-table-wrap">
                    <table className="rich-table analysis-grid-table">
                      <thead>
                        <tr>
                          {primaryTable.columns.map((header, index) => (
                            <th
                              key={`${header}-${index}`}
                              className={index > 0 ? 'analysis-grid-cell-numeric' : undefined}
                            >
                              {header}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {visibleRows.map((row, rowIndex) => (
                          <tr key={`${activeSheet.name}-${primaryTable.title}-${rowIndex}`}>
                            {row.map((cell, cellIndex) => (
                              <td
                                key={`${activeSheet.name}-${primaryTable.title}-${rowIndex}-${cellIndex}`}
                                className={isNumericWorkbookCell(cell) ? 'analysis-grid-cell-numeric' : undefined}
                              >
                                {formatWorkbookCell(cell)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            ) : (
              <div className="analysis-empty-state">
                <strong>No table preview</strong>
                <span>This workbook sheet does not yet expose a previewable table in the current backend payload.</span>
              </div>
            )}

            {primaryChart ? (
              <div className="analysis-chart-gallery-card">
                {renderSimpleChart(primaryChart, primaryTable?.rows || [])}
              </div>
            ) : (
              <div className="analysis-empty-state">
                <strong>No chart preview</strong>
                <span>Charts will appear here when the workbook schema includes normalized chart data.</span>
              </div>
            )}

            {pivotSnapshot.length > 0 ? (
              <div className="analysis-pivot-shell">
                <div className="analysis-pivot-header">
                  <strong>Pivot Snapshot</strong>
                  <span>Read-only grouped view of the active sheet</span>
                </div>
                <div className="analysis-pivot-grid">
                  {pivotSnapshot.slice(0, 4).map((item) => (
                    <div key={`${activeSheet.name}-pivot-${item.label}`} className="analysis-pivot-card">
                      <span>{item.label}</span>
                      <strong>{item.value.toLocaleString()}</strong>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )
    }

    return (
        <div className={`analysis-spec-preview analysis-workbook-preview ${viewerMode ? 'analysis-workbook-preview-viewer' : ''}`}>
          <div className="analysis-workbook-header">
            <div>
              {spec.workbook_title ? <h5 className="rich-heading">{spec.workbook_title}</h5> : null}
            <span className="artifact-preview-label">
              {activeSheet.name} • {spec.sheets.length} sheet{spec.sheets.length === 1 ? '' : 's'} • read-only viewer
            </span>
          </div>
          <div className="analysis-sheet-tabs" role="tablist" aria-label="Workbook sheets">
            {spec.sheets.map((sheet) => (
              <button
                key={`${artifactId}-${sheet.name}`}
                type="button"
                className={`analysis-sheet-tab ${sheet.name === activeSheet.name ? 'analysis-sheet-tab-active' : ''}`}
                onClick={() =>
                  setActiveArtifactSheets((current) => ({
                    ...current,
                    [artifactId]: sheet.name,
                  }))
                }
              >
                {sheet.name}
              </button>
            ))}
          </div>
          </div>
          <div className="analysis-workbook-overview">
            <div className="analysis-workbook-overview-card">
              <span>Sheet</span>
              <strong>{activeSheet.name}</strong>
            </div>
            <div className="analysis-workbook-overview-card">
              <span>Metrics</span>
              <strong>{workbookSummary.metricCount}</strong>
            </div>
            <div className="analysis-workbook-overview-card">
              <span>Tables</span>
              <strong>{workbookSummary.tableCount}</strong>
            </div>
            <div className="analysis-workbook-overview-card">
              <span>Rows</span>
              <strong>{workbookSummary.rowCount || '—'}</strong>
            </div>
            <div className="analysis-workbook-overview-card">
              <span>Charts</span>
              <strong>{workbookSummary.chartCount}</strong>
            </div>
          </div>
          <div className="analysis-sheet-insights">
            {buildSheetInsights(
              activeSheet.metrics || [],
              primaryTable,
              null,
              chartEntriesForSheet(activeSheet).length,
            ).map((insight) => (
              <div key={`${activeSheet.name}-${insight}`} className="analysis-sheet-insight-card">
                {insight}
              </div>
            ))}
          </div>

        {viewerMode ? (
          <div className="analysis-view-tabs analysis-view-tabs-sticky" role="tablist" aria-label="Workbook views">
            <button
              type="button"
              className={`analysis-view-tab ${activeViewTab === 'sheet' ? 'analysis-view-tab-active' : ''}`}
              onClick={() => setActiveArtifactViewTabs((current) => ({ ...current, [artifactId]: 'sheet' }))}
            >
              Sheet
            </button>
            {chartEntriesForSheet(activeSheet).length > 0 ? (
              <button
                type="button"
                className={`analysis-view-tab ${activeViewTab === 'charts' ? 'analysis-view-tab-active' : ''}`}
                onClick={() => setActiveArtifactViewTabs((current) => ({ ...current, [artifactId]: 'charts' }))}
              >
                Charts
              </button>
            ) : null}
            {pivotSnapshot.length > 0 ? (
              <button
                type="button"
                className={`analysis-view-tab ${activeViewTab === 'pivot' ? 'analysis-view-tab-active' : ''}`}
                onClick={() => setActiveArtifactViewTabs((current) => ({ ...current, [artifactId]: 'pivot' }))}
              >
                Pivot
              </button>
            ) : null}
          </div>
        ) : null}

        <div className="analysis-sheet analysis-sheet-grid analysis-workbook-stage analysis-workbook-stage-viewer">
          <div className="analysis-sheet-sidebar" style={viewerMode ? { width: `${sidebarWidth}px` } : undefined}>
            <span className="artifact-preview-label">{activeSheet.name}</span>
            {activeSheet.metrics && activeSheet.metrics.length > 0 ? (
              <div className="analysis-metric-grid">
                {activeSheet.metrics.map((metric) => (
                  <div key={`${activeSheet.name}-${metric.label}`} className="analysis-metric-card">
                    <span>{metric.label}</span>
                    <strong>{metric.value}</strong>
                  </div>
                ))}
              </div>
            ) : null}
            {activeViewTab !== 'pivot' && chartEntriesForSheet(activeSheet).length > 0 ? (
              <div className="analysis-chart-list">
                {chartEntriesForSheet(activeSheet).map((chart) => (
                  <div key={`${activeSheet.name}-${chart.title}`} className="analysis-chart-card">
                    {renderSimpleChart(chart, primaryTable?.rows || [])}
                  </div>
                ))}
              </div>
            ) : null}
            {!viewerMode && pivotSnapshot.length > 0 ? (
              <div className="analysis-pivot-shell">
                <div className="analysis-pivot-header">
                  <strong>Pivot Snapshot</strong>
                  <span>Static summary from the current workbook preview</span>
                </div>
                <div className="analysis-pivot-grid">
                  {pivotSnapshot.map((item) => (
                    <div key={`${activeSheet.name}-pivot-${item.label}`} className="analysis-pivot-card">
                      <span>{item.label}</span>
                      <strong>{item.value.toLocaleString()}</strong>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>

          {viewerMode ? (
            <button
              type="button"
              className="analysis-pane-resizer"
              aria-label="Resize workbook side panel"
              onMouseDown={(event) => startPaneResize(artifactId, event.clientX)}
            />
          ) : null}

          <div className="analysis-sheet-main">
            {viewerMode && activeViewTab === 'charts' ? (
              <div className="analysis-chart-gallery">
                {chartEntriesForSheet(activeSheet).length ? chartEntriesForSheet(activeSheet).map((chart) => (
                  <div key={`${activeSheet.name}-gallery-${chart.title}`} className="analysis-chart-gallery-card">
                    {renderSimpleChart(chart, primaryTable?.rows || [])}
                  </div>
                )) : (
                  <div className="analysis-empty-state">
                    <strong>No charts available</strong>
                    <span>This sheet does not currently expose chart specs from the backend workbook preview.</span>
                  </div>
                )}
              </div>
            ) : null}

            {viewerMode && activeViewTab === 'pivot' ? (
              <div className="analysis-pivot-shell analysis-pivot-shell-main">
                <div className="analysis-pivot-header">
                  <strong>Pivot Snapshot</strong>
                  <span>Static grouped view for the active sheet</span>
                </div>
                {pivotSnapshot.length > 0 ? (
                  <div className="analysis-pivot-grid">
                    {pivotSnapshot.map((item) => (
                      <div key={`${activeSheet.name}-pivot-main-${item.label}`} className="analysis-pivot-card">
                        <span>{item.label}</span>
                        <strong>{item.value.toLocaleString()}</strong>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="analysis-empty-state">
                    <strong>No pivot snapshot</strong>
                    <span>The current sheet does not yet provide enough grouped numeric structure for a pivot view.</span>
                  </div>
                )}
              </div>
            ) : null}

            {(activeViewTab === 'sheet' || !viewerMode) ? (
              activeSheet.tables?.length ? activeSheet.tables.map((table) => {
                const selectedRow = activeProvenanceRows[artifactId]
                const selectedProvenance =
                  selectedRow?.tableTitle === table.title && table.row_provenance
                    ? table.row_provenance[selectedRow.rowIndex]
                    : null
                const selectedRowValues =
                  selectedRow?.tableTitle === table.title && table.rows[selectedRow.rowIndex]
                    ? buildRowSnapshot(table.columns, table.rows[selectedRow.rowIndex])
                    : []

                return (
                <div key={`${activeSheet.name}-${table.title}`} className="analysis-table-shell">
                  <span className="artifact-preview-label">{table.title}</span>
                  <div className="analysis-grid-shell">
                    <div className="analysis-grid-index-column">
                      <span className="analysis-grid-index-head">#</span>
                      {table.rows.map((_, rowIndex) => (
                        <span key={`${table.title}-index-${rowIndex}`} className="analysis-grid-index-cell">
                          {rowIndex + 1}
                        </span>
                      ))}
                    </div>
                    <div className="analysis-grid-table-wrap">
                      <table className="rich-table analysis-grid-table">
                        <thead>
                          <tr>
                            {table.columns.map((header, index) => (
                              <th
                                key={`${header}-${index}`}
                                className={index > 0 ? 'analysis-grid-cell-numeric' : undefined}
                              >
                                {header}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {table.rows.map((row, rowIndex) => (
                            <tr
                              key={`${activeSheet.name}-${table.title}-${rowIndex}`}
                              className={
                                selectedRow?.tableTitle === table.title && selectedRow.rowIndex === rowIndex
                                  ? 'analysis-row-active'
                                  : undefined
                              }
                              onClick={
                                viewerMode && table.row_provenance?.[rowIndex]
                                  ? () =>
                                      setActiveProvenanceRows((current) => ({
                                        ...current,
                                        [artifactId]: { tableTitle: table.title, rowIndex },
                                      }))
                                  : undefined
                              }
                            >
                              {row.map((cell, cellIndex) => (
                                <td
                                  key={`${activeSheet.name}-${table.title}-${rowIndex}-${cellIndex}`}
                                  className={isNumericWorkbookCell(cell) ? 'analysis-grid-cell-numeric' : undefined}
                                >
                                  {cellIndex === row.length - 1 && table.row_provenance?.[rowIndex] ? (
                                    <span
                                      className={`provenance-pill ${provenanceToneClass(table.row_provenance[rowIndex].source_type)}`}
                                      title={table.row_provenance[rowIndex].source_excerpt || table.row_provenance[rowIndex].source_ref}
                                    >
                                      {cell}
                                    </span>
                                  ) : (
                                    formatWorkbookCell(cell)
                                  )}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  {viewerMode && selectedProvenance ? (
                    <div className="provenance-drawer">
                      <div className="provenance-drawer-header">
                        <strong>Row Provenance</strong>
                        <span className={`provenance-pill ${provenanceToneClass(selectedProvenance.source_type)}`}>
                          {selectedProvenance.source_type.replace(/_/g, ' ')}
                        </span>
                      </div>
                      {selectedRowValues.length > 0 ? (
                        <div className="provenance-row-grid">
                          {selectedRowValues.map((entry) => (
                            <div key={`${entry.column}-${entry.value}`} className="provenance-row-card">
                              <span>{entry.column}</span>
                              <strong>{entry.value}</strong>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      <p className="provenance-ref">{selectedProvenance.source_ref}</p>
                      <p className="provenance-excerpt">
                        {selectedProvenance.source_excerpt || 'No source excerpt was recorded for this row.'}
                      </p>
                    </div>
                  ) : null}
                </div>
                )
              }) : (
                <div className="analysis-empty-state">
                  <strong>No sheet preview</strong>
                  <span>This sheet has no previewable tables in the current workbook payload.</span>
                </div>
              )
            ) : null}
          </div>
        </div>
      </div>
    )
  }

  const renderWorkbookViewResponse = (artifactId: string, view: WorkbookViewResponse) => {
    if (!view.tabs.length) {
      return (
        <div className="analysis-empty-state">
          <strong>No workbook tabs</strong>
          <span>This workbook view did not include any tabs.</span>
        </div>
      )
    }

    const activeTabName = activeArtifactSheets[artifactId] || view.tabs[0].name
    const activeTab = view.tabs.find((tab) => tab.name === activeTabName) || view.tabs[0]
    const primaryTable = activeTab.tables.find((table) => table.rows.length > 0) || activeTab.tables[0]
    const derivedPivotSnapshot =
      activeTab.pivot_snapshots.length > 0
        ? activeTab.pivot_snapshots[0]
        : primaryTable
          ? {
              title: `${activeTab.name} Pivot`,
              dimension: primaryTable.columns[0] || 'Group',
              measure: primaryTable.columns[1] || 'Value',
              rows: buildPivotSnapshot(primaryTable),
            }
          : null
    const activeViewTab: WorkbookViewTab = activeArtifactViewTabs[artifactId] || 'sheet'
    const sidebarWidth = artifactPaneWidths[artifactId] ?? 280
    const comparisonSummary = summarizeComparisonBasis(view)
    const workbookSummary = summarizeWorkbookTab(activeTab)

    return (
      <div className="analysis-spec-preview analysis-workbook-preview analysis-workbook-preview-viewer">
        <div className="analysis-workbook-header">
          <div>
            <h5 className="rich-heading">{view.title}</h5>
            <span className="artifact-preview-label">
              {activeTab.kind} tab • {view.tabs.length} tab{view.tabs.length === 1 ? '' : 's'} • typed workbook view
            </span>
          </div>
          <div className="analysis-sheet-tabs" role="tablist" aria-label="Workbook tabs">
            {view.tabs.map((tab) => (
              <button
                key={`${artifactId}-${tab.name}`}
                type="button"
                className={`analysis-sheet-tab ${tab.name === activeTab.name ? 'analysis-sheet-tab-active' : ''}`}
                onClick={() =>
                  setActiveArtifactSheets((current) => ({
                    ...current,
                    [artifactId]: tab.name,
                  }))
                }
              >
                {tab.name}
              </button>
            ))}
          </div>
        </div>
        <div className="analysis-workbook-overview">
          <div className="analysis-workbook-overview-card">
            <span>Active tab</span>
            <strong>{activeTab.name}</strong>
          </div>
          <div className="analysis-workbook-overview-card">
            <span>Metrics</span>
            <strong>{workbookSummary.metricCount}</strong>
          </div>
          <div className="analysis-workbook-overview-card">
            <span>Tables</span>
            <strong>{activeTab.tables.length}</strong>
          </div>
          <div className="analysis-workbook-overview-card">
            <span>Rows</span>
            <strong>{workbookSummary.rowCount || '—'}</strong>
          </div>
          <div className="analysis-workbook-overview-card">
            <span>Charts</span>
            <strong>{workbookSummary.chartCount}</strong>
          </div>
        </div>

        {comparisonSummary.hasComparison || comparisonSummary.periods.length > 0 ? (
          <div className="workbook-coverage-banner">
            <div className="workbook-coverage-copy">
              <strong>
                {comparisonSummary.comparisonLabel
                  ? `Comparison based on ${comparisonSummary.comparisonLabel}`
                  : 'Workbook period coverage'}
              </strong>
              {comparisonSummary.basisExcerpt ? <span>{comparisonSummary.basisExcerpt}</span> : null}
              {!comparisonSummary.basisExcerpt && comparisonSummary.basisRef ? <span>{comparisonSummary.basisRef}</span> : null}
            </div>
            {comparisonSummary.periods.length > 0 ? (
              <div className="workbook-coverage-chips">
                {comparisonSummary.periods.map((period) => (
                  <span key={`${artifactId}-${period}`} className="workbook-coverage-chip">
                    {period}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="analysis-view-tabs" role="tablist" aria-label="Workbook views">
          <button
            type="button"
            className={`analysis-view-tab ${activeViewTab === 'sheet' ? 'analysis-view-tab-active' : ''}`}
            onClick={() => setActiveArtifactViewTabs((current) => ({ ...current, [artifactId]: 'sheet' }))}
          >
            Sheet
          </button>
          {activeTab.charts.length > 0 ? (
            <button
              type="button"
              className={`analysis-view-tab ${activeViewTab === 'charts' ? 'analysis-view-tab-active' : ''}`}
              onClick={() => setActiveArtifactViewTabs((current) => ({ ...current, [artifactId]: 'charts' }))}
            >
              Charts
            </button>
          ) : null}
          {derivedPivotSnapshot && derivedPivotSnapshot.rows.length > 0 ? (
            <button
              type="button"
              className={`analysis-view-tab ${activeViewTab === 'pivot' ? 'analysis-view-tab-active' : ''}`}
              onClick={() => setActiveArtifactViewTabs((current) => ({ ...current, [artifactId]: 'pivot' }))}
            >
              Pivot
            </button>
          ) : null}
        </div>

        <div className="analysis-sheet analysis-sheet-grid analysis-workbook-stage analysis-workbook-stage-viewer">
          <div className="analysis-sheet-sidebar" style={{ width: `${sidebarWidth}px` }}>
            <span className="artifact-preview-label">{activeTab.name}</span>
            {activeTab.metrics.length > 0 ? (
              <div className="analysis-metric-grid">
                {activeTab.metrics.map((metric) => (
                  <div key={`${activeTab.name}-${metric.label}`} className="analysis-metric-card">
                    <span>{metric.label}</span>
                    <strong>{metric.value}</strong>
                  </div>
                ))}
              </div>
            ) : null}
            {activeViewTab !== 'pivot' && activeTab.charts.length > 0 ? (
              <div className="analysis-chart-list">
                {activeTab.charts.map((chart) => (
                  <div key={`${activeTab.name}-${chart.title}`} className="analysis-chart-card">
                    {renderSimpleChart(chart, primaryTable?.rows || [])}
                  </div>
                ))}
              </div>
            ) : null}
            {activeViewTab !== 'pivot' && derivedPivotSnapshot && derivedPivotSnapshot.rows.length > 0 ? (
              <div className="analysis-pivot-shell">
                <div className="analysis-pivot-header">
                  <strong>{derivedPivotSnapshot.title}</strong>
                  <span>
                    {derivedPivotSnapshot.dimension} by {derivedPivotSnapshot.measure}
                  </span>
                </div>
                <div className="analysis-pivot-grid">
                  {derivedPivotSnapshot.rows.slice(0, 4).map((item) => (
                    <div key={`${activeTab.name}-pivot-${item.label}`} className="analysis-pivot-card">
                      <span>{item.label}</span>
                      <strong>{item.value.toLocaleString()}</strong>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>

          <button
            type="button"
            className="analysis-pane-resizer"
            aria-label="Resize workbook side panel"
            onMouseDown={(event) => startPaneResize(artifactId, event.clientX)}
          />

          <div className="analysis-sheet-main">
            {activeViewTab === 'charts' ? (
              activeTab.charts.length ? (
                <div className="analysis-chart-gallery">
                  {activeTab.charts.map((chart) => (
                    <div key={`${activeTab.name}-gallery-${chart.title}`} className="analysis-chart-gallery-card">
                      {renderSimpleChart(chart, primaryTable?.rows || [])}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="analysis-empty-state">
                  <strong>No charts available</strong>
                  <span>This workbook view did not include chart data for the active tab.</span>
                </div>
              )
            ) : null}

            {activeViewTab === 'pivot' ? (
              derivedPivotSnapshot && derivedPivotSnapshot.rows.length > 0 ? (
                <div className="analysis-pivot-shell analysis-pivot-shell-main">
                  <div className="analysis-pivot-header">
                    <strong>{derivedPivotSnapshot.title}</strong>
                    <span>
                      {derivedPivotSnapshot.dimension} by {derivedPivotSnapshot.measure}
                    </span>
                  </div>
                  <div className="analysis-pivot-grid">
                    {derivedPivotSnapshot.rows.map((item) => (
                      <div key={`${activeTab.name}-pivot-main-${item.label}`} className="analysis-pivot-card">
                        <span>{item.label}</span>
                        <strong>{item.value.toLocaleString()}</strong>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="analysis-empty-state">
                  <strong>No pivot snapshot</strong>
                  <span>This workbook view did not include grouped pivot data for the active tab.</span>
                </div>
              )
            ) : null}

            {activeViewTab === 'sheet' ? (
              activeTab.tables.length ? activeTab.tables.map((table) => (
                <div key={`${activeTab.name}-${table.title}`} className="analysis-table-shell">
                  <span className="artifact-preview-label">{table.title}</span>
                  <div className="analysis-grid-shell">
                    <div className="analysis-grid-index-column">
                      <span className="analysis-grid-index-head">#</span>
                      {table.rows.map((_, rowIndex) => (
                        <span key={`${table.title}-index-${rowIndex}`} className="analysis-grid-index-cell">
                          {rowIndex + 1}
                        </span>
                      ))}
                    </div>
                    <div className="analysis-grid-table-wrap">
                      <table className="rich-table analysis-grid-table">
                        <thead>
                          <tr>
                            {table.columns.map((header, index) => (
                              <th key={`${header}-${index}`} className={index > 0 ? 'analysis-grid-cell-numeric' : undefined}>
                                {header}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {table.rows.map((row, rowIndex) => (
                            <tr
                              key={`${activeTab.name}-${table.title}-${rowIndex}`}
                              className={
                                activeProvenanceRows[artifactId]?.tableTitle === table.title &&
                                activeProvenanceRows[artifactId]?.rowIndex === rowIndex
                                  ? 'analysis-grid-row-active'
                                  : undefined
                              }
                              onClick={() =>
                                setActiveProvenanceRows((current) => ({
                                  ...current,
                                  [artifactId]: { tableTitle: table.title, rowIndex },
                                }))
                              }
                            >
                              {row.map((cell, cellIndex) => (
                                <td
                                  key={`${activeTab.name}-${table.title}-${rowIndex}-${cellIndex}`}
                                  className={isNumericWorkbookCell(cell) ? 'analysis-grid-cell-numeric' : undefined}
                                >
                                  {formatWorkbookCell(cell)}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  {table.row_provenance && table.row_provenance.length > 0 && activeProvenanceRows[artifactId]?.tableTitle === table.title ? (
                    <div className="analysis-provenance-card">
                      <strong>Row provenance</strong>
                      <span>
                        Source: {String(table.row_provenance[activeProvenanceRows[artifactId].rowIndex]?.source_ref || table.row_provenance[activeProvenanceRows[artifactId].rowIndex]?.source_type || 'Unknown')}
                      </span>
                      {table.row_provenance[activeProvenanceRows[artifactId].rowIndex]?.source_excerpt ? (
                        <p>{String(table.row_provenance[activeProvenanceRows[artifactId].rowIndex]?.source_excerpt)}</p>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              )) : (
                <div className="analysis-empty-state">
                  <strong>No sheet preview</strong>
                  <span>This workbook view did not include any tables for the active tab.</span>
                </div>
              )
            ) : null}
          </div>
        </div>
      </div>
    )
  }

  const downloadArtifact = async (message: AssistantMessage, artifact: AssistantMessage['artifacts'][number]) => {
    if (!token || !message.metadata.interaction_id) {
      return
    }

    setErrorMessage(null)
    try {
      const response = await authenticatedFetch(`/artifacts/${message.metadata.interaction_id}/${artifact.artifact_type}/download`)
      if (!response.ok) {
        const data = await parseJsonSafely<{ detail?: string }>(response)
        throw new Error(data?.detail || 'Unable to download artifact.')
      }
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${artifact.label}.${artifact.format || 'bin'}`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(url)
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to download artifact.')
    }
  }

  const ensureArtifactPreview = async (message: AssistantMessage, artifact: AssistantMessage['artifacts'][number]) => {
    if (artifactPreviews[artifact.artifact_id]) {
      return artifactPreviews[artifact.artifact_id]
    }

    if (!token || !message.metadata.interaction_id) {
      return null
    }

    setPreviewLoadingId(artifact.artifact_id)
    setErrorMessage(null)
    try {
      const response = await authenticatedFetch(`/artifacts/${message.metadata.interaction_id}/${artifact.artifact_type}`)
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Unable to load artifact preview.')
      }
      setArtifactPreviews((current) => ({ ...current, [artifact.artifact_id]: data }))
      return data as ArtifactPreview
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to load artifact preview.')
      return null
    } finally {
      setPreviewLoadingId(null)
    }
  }

  const openWorkbookViewer = async (message: AssistantMessage, artifact: AssistantMessage['artifacts'][number]) => {
    if (!token || !message.metadata.interaction_id) {
      return
    }

    setWorkbookViewLoadingId(artifact.artifact_id)
    setErrorMessage(null)

    try {
      const response = await authenticatedFetch(`/artifacts/${message.metadata.interaction_id}/analysis_xlsx/view`)
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Unable to load workbook view.')
      }
      setWorkbookViews((current) => ({ ...current, [artifact.artifact_id]: data as WorkbookViewResponse }))
      setWorkbookViewer({
        messageId: message.message_id,
        artifact,
        title: (data as WorkbookViewResponse).title || 'Workbook Viewer',
      })
    } catch (error) {
      console.error(error)
      const preview = await ensureArtifactPreview(message, artifact)
      if (!preview) {
        setErrorMessage(error instanceof Error ? error.message : 'Unable to load workbook view.')
        return
      }
      const fallbackView = workbookPreviewToViewResponse(
        artifact.artifact_id,
        parseWorkbookPreviewModel(preview.content) || { workbook_title: preview.label, sheets: [] },
      )
      setWorkbookViews((current) => ({ ...current, [artifact.artifact_id]: fallbackView }))
      setWorkbookViewer({
        messageId: message.message_id,
        artifact,
        title: fallbackView.title || 'Workbook Viewer',
      })
    } finally {
      setWorkbookViewLoadingId(null)
    }
  }

  useEffect(() => {
    if (!token) {
      return
    }

    const previewTargets = messages
      .filter((message) => message.status === 'completed')
      .flatMap((message) =>
        message.artifacts
          .filter((artifact) => artifact.status !== 'planned')
          .map((artifact) => ({ message, artifact }))
      )
      .filter(
        (entry) =>
          !artifactPreviews[entry.artifact.artifact_id] &&
          Boolean(entry.message.metadata.interaction_id)
      )

    if (previewTargets.length === 0) {
      return
    }

    let cancelled = false

    const preloadPreviews = async () => {
      for (const entry of previewTargets) {
        if (cancelled) {
          return
        }
        try {
          const response = await authenticatedFetch(
            `/artifacts/${entry.message.metadata.interaction_id}/${entry.artifact.artifact_type}`
          )
          const data = await response.json()
          if (!response.ok) {
            continue
          }
          if (!cancelled) {
            setArtifactPreviews((current) => ({ ...current, [entry.artifact.artifact_id]: data }))
          }
        } catch {
          continue
        }
      }
    }

    void preloadPreviews()

    return () => {
      cancelled = true
    }
  }, [artifactPreviews, messages, token])

  const buildResponseCopyText = (message: AssistantMessage) => {
    const lines = [message.answer.title, '', message.answer.summary]
    for (const section of message.answer.sections) {
      lines.push('', section.label)
      if (section.content) {
        lines.push(section.content)
      }
      for (const item of section.items || []) {
        lines.push(`- ${item}`)
      }
    }
    return lines.join('\n').trim()
  }

  const copyResponse = async (message: AssistantMessage) => {
    try {
      await navigator.clipboard.writeText(buildResponseCopyText(message))
      setCopiedMessageId(message.message_id)
      window.setTimeout(() => {
        setCopiedMessageId((current) => (current === message.message_id ? null : current))
      }, 1600)
    } catch (error) {
      console.error(error)
      setErrorMessage('Unable to copy response.')
    }
  }

  const jumpToBottom = () => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }

  const resolveApproval = async (message: AssistantMessage, decision: 'approve' | 'reject', mode?: 'draft' | 'send') => {
    if (!message.metadata.interaction_id || !token || resolvingMessageId === message.message_id) {
      return
    }

    setResolvingMessageId(message.message_id)
    setErrorMessage(null)

    try {
      const response = await authenticatedFetch(`/assistant/messages/${message.metadata.interaction_id}/resolve`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          decision,
          mode,
          note: approvalNotes[message.message_id]?.trim() || undefined,
        }),
      })

      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Unable to resolve approval.')
      }

      setMessages((current) => current.map((item) => (item.message_id === message.message_id ? data : item)))
      if (activeConversationId) {
        await fetchConversation(activeConversationId)
      }
      await fetchConversations()
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to resolve approval.')
    } finally {
      setResolvingMessageId(null)
    }
  }

  const handleComposerKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) {
      return
    }

    event.preventDefault()
    void submitQuery()
  }

  const hasMessages = messages.length > 0
  const visibleConversations = conversations
    .filter((conversation) => conversation.message_count > 0 || conversation.conversation_id === activeConversationId)
    .sort((left, right) => {
      if (left.pinned !== right.pinned) {
        return left.pinned ? -1 : 1
      }
      return (right.updated_at || '').localeCompare(left.updated_at || '')
    })
  const activeConversation = conversations.find((conversation) => conversation.conversation_id === activeConversationId) ?? null
  const activeProject = projects.find((project) => project.project_id === activeProjectId) ?? null
  const projectDocuments = activeProject
    ? activeProject.document_ids
        .map((documentId) => documents.find((document) => document.document_id === documentId))
        .filter((document): document is KnowledgeDocument => Boolean(document))
        .slice(0, 4)
    : []
  const visibleConversationsForProject = activeProject
    ? visibleConversations.filter(
        (conversation) =>
          conversation.conversation_id === activeConversationId ||
          activeProject.conversation_ids.includes(conversation.conversation_id),
      )
    : visibleConversations
  const gmailConnected = integrations.some((item) => item.service === 'gmail' && item.connected)
  const outlookMailConnected = integrations.some((item) => item.service === 'outlook_mail' && item.connected)
  const googleCalendarConnected = integrations.some((item) => item.service === 'google_calendar' && item.connected)
  const outlookCalendarConnected = integrations.some((item) => item.service === 'outlook_calendar' && item.connected)
  const emailConnected = gmailConnected || outlookMailConnected
  const calendarConnected = googleCalendarConnected || outlookCalendarConnected
  const connectCalendar = () => connectIntegration(googleCalendarConnected ? 'outlook_calendar' : 'google_calendar')
  const connectEmail = () => connectIntegration(gmailConnected ? 'outlook_mail' : 'gmail')

  const openConversation = async (conversationId: string) => {
    if (!token || conversationId === activeConversationId) {
      return
    }

    setLoadState('loading')
    setErrorMessage(null)
    try {
      await fetchConversation(conversationId)
      setActiveConversationId(conversationId)
      setLoadState('ready')
    } catch (error) {
      console.error(error)
      setLoadState('error')
      setErrorMessage(error instanceof Error ? error.message : 'Unable to open conversation.')
    }
  }

  const startFreshConversation = async () => {
    if (!token) {
      return
    }

    setLoadState('loading')
    setErrorMessage(null)
    try {
      const conversation = await createConversation()
      if (activeProjectId) {
        const project = projects.find((item) => item.project_id === activeProjectId)
        if (project) {
          const nextConversationIds = Array.from(new Set([...project.conversation_ids, conversation.conversation_id]))
          await saveProject(activeProjectId, { conversation_ids: nextConversationIds })
        }
      }
      setConversations((current) => [conversation, ...current.filter((item) => item.conversation_id !== conversation.conversation_id)])
      setActiveConversationId(conversation.conversation_id)
      setMessages([])
      setLoadState('ready')
    } catch (error) {
      console.error(error)
      setLoadState('error')
      setErrorMessage(error instanceof Error ? error.message : 'Unable to start a new conversation.')
    }
  }

  const handleDeleteConversation = async (conversationId: string) => {
    if (!token || isProtectedConversationId(conversationId)) {
      return
    }

    setDeletingConversationId(conversationId)
    setErrorMessage(null)
    try {
      await deleteConversation(conversationId)
      const nextProjects = await fetchProjects()
      const nextConversations = await fetchConversations()

      if (activeConversationId === conversationId) {
        const activeProject = nextProjects.find((project) => project.project_id === activeProjectId)
        const nextVisibleConversations = activeProject
          ? nextConversations.filter((conversation) => activeProject.conversation_ids.includes(conversation.conversation_id))
          : nextConversations

        if (nextVisibleConversations[0]) {
          await fetchConversation(nextVisibleConversations[0].conversation_id)
          setActiveConversationId(nextVisibleConversations[0].conversation_id)
        } else {
          await startFreshConversation()
        }
      }
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to delete conversation.')
    } finally {
      setDeletingConversationId(null)
    }
  }

  const handleDeleteProject = async (projectId: string) => {
    if (!token) {
      return
    }

    setDeletingProjectId(projectId)
    setErrorMessage(null)
    try {
      await deleteProject(projectId)
      const nextProjects = await fetchProjects()
      if (activeProjectId === projectId) {
        setActiveProjectId(nextProjects[0]?.project_id ?? null)
      }
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to delete project.')
    } finally {
      setDeletingProjectId(null)
    }
  }

  const requestDeleteProject = async (projectId: string) => {
    if (confirmingProjectDeleteId !== projectId) {
      setConfirmingProjectDeleteId(projectId)
      return
    }

    setConfirmingProjectDeleteId(null)
    await handleDeleteProject(projectId)
  }

  const startRenameConversation = (conversation: SavedConversation) => {
    setEditingConversationId(conversation.conversation_id)
    setEditingConversationTitle(conversation.title || 'New conversation')
  }

  const cancelRenameConversation = () => {
    setEditingConversationId(null)
    setEditingConversationTitle('')
  }

  const submitRenameConversation = async (conversationId: string) => {
    const trimmed = editingConversationTitle.trim()
    if (!trimmed || !token) {
      cancelRenameConversation()
      return
    }

    setErrorMessage(null)
    setUpdatingConversationId(conversationId)
    try {
      const updated = await updateConversation(conversationId, { title: trimmed })
      setConversations((current) =>
        current.map((conversation) =>
          conversation.conversation_id === conversationId ? { ...conversation, ...updated } : conversation,
        ),
      )
      cancelRenameConversation()
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to rename conversation.')
    } finally {
      setUpdatingConversationId(null)
    }
  }

  const applyConversationUpdate = async (conversationId: string, updates: { pinned?: boolean; archived?: boolean }) => {
    if (!token || conversationId.startsWith('default:')) {
      return
    }

    setUpdatingConversationId(conversationId)
    setErrorMessage(null)
    try {
      const updated = await updateConversation(conversationId, updates)
      const nextConversations = updates.archived
        ? conversations.filter((conversation) => conversation.conversation_id !== conversationId)
        : conversations.map((conversation) =>
            conversation.conversation_id === conversationId ? { ...conversation, ...updated } : conversation,
          )

      setConversations(
        nextConversations.sort((left, right) => {
          if (left.pinned !== right.pinned) {
            return left.pinned ? -1 : 1
          }
          return (right.updated_at || '').localeCompare(left.updated_at || '')
        }),
      )

      if (updates.archived && activeConversationId === conversationId) {
        const nextVisible = nextConversations.filter((conversation) => conversation.message_count > 0 || conversation.conversation_id === activeConversationId)
        if (nextVisible[0]) {
          await fetchConversation(nextVisible[0].conversation_id)
          setActiveConversationId(nextVisible[0].conversation_id)
        } else {
          await startFreshConversation()
        }
      }
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to update conversation.')
    } finally {
      setUpdatingConversationId(null)
    }
  }

  const submitProjectCreate = async () => {
    const trimmed = newProjectName.trim()
    if (!trimmed || !token) {
      return
    }

    setErrorMessage(null)
    try {
      const project = await createProject(trimmed)
      setProjects((current) => [project, ...current.filter((item) => item.project_id !== project.project_id)])
      setActiveProjectId(project.project_id)
      setNewProjectName('')
      setShowProjectComposer(false)
    } catch (error) {
      console.error(error)
      setErrorMessage(error instanceof Error ? error.message : 'Unable to create project.')
    }
  }

  if (!token) {
    return (
      <div className="auth-shell">
        <section className="auth-card">
          <span className="eyebrow">Executive Advisor</span>
          <h1>Sign in to agenticMIND</h1>
          <p className="auth-copy">
            Use the assistant API workspace to draft reports, explain documents, and keep the trust layer visible.
          </p>

          <form className="auth-form" onSubmit={submitLogin}>
            <label>
              <span>Username</span>
              <input
                value={login.username}
                onChange={(event) => setLogin((current) => ({ ...current, username: event.target.value }))}
                autoComplete="username"
              />
            </label>
            <label>
              <span>Password</span>
              <input
                type="password"
                value={login.password}
                onChange={(event) => setLogin((current) => ({ ...current, password: event.target.value }))}
                autoComplete="current-password"
              />
            </label>
            <button className="send-button auth-button" type="submit" disabled={isAuthenticating}>
              {isAuthenticating ? <Loader2 size={16} className="spin" /> : <ArrowRight size={16} />}
              Continue
            </button>
          </form>

          {errorMessage ? <p className="error-copy">{errorMessage}</p> : null}
        </section>
      </div>
    )
  }

  return (
    <DashboardShell
      sidebar={
        <>
        <div className="brand-block">
          <span className="eyebrow">Executive Advisor</span>
          <h1>agenticMIND</h1>
          <p>
            Company-specific reporting and document explanation for fast executive decisions.
          </p>
        </div>

        <div className="sidebar-card">
          <div className="sidebar-card-header">
            <MessageSquareText size={16} />
            <span>Start</span>
          </div>
          <div className="playbook-list">
            {QUERY_PLAYBOOKS.map((playbook) => (
              <button
                key={playbook.label}
                type="button"
                className="playbook-card"
                onClick={() => void applySuggestedPrompt(playbook.prompt)}
              >
                <strong>{playbook.label}</strong>
              </button>
            ))}
          </div>
          <div className="compact-event-list">
            <button
              className="compact-event-card"
              type="button"
              disabled={eventLoading !== null}
              onClick={() => void triggerMorningBrief()}
              data-tooltip="Run a morning brief"
            >
              <div>
                <strong>Morning brief</strong>
                <span>Daily executive digest</span>
              </div>
              {eventLoading === 'morning' ? <Loader2 size={16} className="spin" /> : <CalendarClock size={16} />}
            </button>
            <button
              className="compact-event-card"
              type="button"
              disabled={connectingService !== null || eventLoading !== null}
              onClick={() => void (calendarConnected ? triggerCalendarBrief() : connectCalendar())}
              data-tooltip={calendarConnected ? 'Run a calendar brief' : 'Connect a calendar'}
            >
              <div>
                <strong>Calendar brief</strong>
                <span>{calendarConnected ? 'Run from connected calendar' : 'Connect calendar'}</span>
              </div>
              {eventLoading === 'calendar' || connectingService === 'google_calendar' || connectingService === 'outlook_calendar' ? <Loader2 size={16} className="spin" /> : <CalendarClock size={16} />}
            </button>
            <button
              className="compact-event-card"
              type="button"
              disabled={connectingService !== null || eventLoading !== null}
              onClick={() => void (emailConnected ? triggerEmailBrief() : connectEmail())}
              data-tooltip={emailConnected ? 'Run an email brief' : 'Connect an email inbox'}
            >
              <div>
                <strong>Email brief</strong>
                <span>{emailConnected ? 'Run from connected inbox' : 'Connect email'}</span>
              </div>
              {eventLoading === 'email' || connectingService === 'gmail' || connectingService === 'outlook_mail' ? <Loader2 size={16} className="spin" /> : <Mail size={16} />}
            </button>
          </div>
        </div>

        <div className="sidebar-card workspace-card">
          <div className="sidebar-card-header">
            <History size={16} />
            <span>Workspace</span>
            <button
              type="button"
              className="mini-icon-action"
              onClick={() => setShowProjectComposer((current) => !current)}
              aria-label="Create project"
              data-tooltip={showProjectComposer ? 'Hide project creation' : 'Create project'}
            >
              <Plus size={15} />
            </button>
          </div>
          <div className="project-shell">
            {showProjectComposer ? (
              <div className="project-create-row workspace-create-row">
                <input
                  className="project-name-input"
                  value={newProjectName}
                  onChange={(event) => setNewProjectName(event.target.value)}
                  placeholder="New project"
                />
                <button type="button" className="mini-icon-action workspace-create-action" onClick={() => void submitProjectCreate()} data-tooltip="Save project" aria-label="Save project">
                  <Check size={15} />
                </button>
              </div>
            ) : null}
            <div className="workspace-list">
              <button
                type="button"
                className={`history-row history-row-query workspace-row ${activeProject ? '' : 'history-row-active'}`}
                onClick={() => setActiveProjectId(null)}
                data-tooltip="Open general conversations"
              >
                <div className="history-row-query-shell">
                  <strong>General</strong>
                  <span>{visibleConversations.length} convos</span>
                </div>
              </button>
              {projects.length > 0 ? (
              <div className="project-list">
                {projects.map((project) => (
                  <div
                    key={project.project_id}
                    className={`history-row history-row-query workspace-row ${project.project_id === activeProjectId ? 'history-row-active' : ''}`}
                  >
                    <button
                      type="button"
                      className="history-row-main"
                      onClick={() => setActiveProjectId(project.project_id)}
                      data-tooltip={`Open project ${project.name}`}
                    >
                      <div className="history-row-query-shell">
                        <strong>{project.name}</strong>
                        <span>{project.document_ids.length} docs • {project.conversation_ids.length} convos</span>
                      </div>
                    </button>
                    <button
                      type="button"
                      className={`history-row-delete ${confirmingProjectDeleteId === project.project_id ? 'danger-action' : ''}`}
                      onClick={() => void requestDeleteProject(project.project_id)}
                      aria-label={`Delete project ${project.name}`}
                      data-tooltip={confirmingProjectDeleteId === project.project_id ? 'Click again to confirm delete' : 'Delete project'}
                      disabled={deletingProjectId === project.project_id}
                    >
                      {deletingProjectId === project.project_id ? <Loader2 size={14} className="spin" /> : <Trash2 size={14} />}
                    </button>
                  </div>
                ))}
              </div>
              ) : null}
            </div>
            <div className="project-workspace workspace-panel">
              <div className="project-workspace-header workspace-panel-header">
                <div className="workspace-panel-title">
                  <strong>{activeProject ? activeProject.name : 'General'}</strong>
                </div>
                <div className="workspace-panel-actions">
                  {activeProject ? (
                    <button
                      type="button"
                      className={`mini-icon-action ${confirmingProjectDeleteId === activeProject.project_id ? 'danger-action' : ''}`}
                      onClick={() => void requestDeleteProject(activeProject.project_id)}
                      aria-label={`Delete project ${activeProject.name}`}
                      data-tooltip={confirmingProjectDeleteId === activeProject.project_id ? 'Click again to confirm delete' : 'Delete project'}
                      disabled={deletingProjectId === activeProject.project_id}
                    >
                      {deletingProjectId === activeProject.project_id ? <Loader2 size={15} className="spin" /> : <Trash2 size={15} />}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="mini-icon-action"
                    onClick={() => void startFreshConversation()}
                    aria-label={activeProject ? `New conversation in ${activeProject.name}` : 'New conversation'}
                    data-tooltip={activeProject ? 'New conversation in project' : 'New conversation'}
                  >
                    <Plus size={15} />
                  </button>
                </div>
              </div>
              {activeProject ? (
                <>
                <div className="project-workspace-section">
                  <div className="project-workspace-header">
                    <span className="project-workspace-meta">
                      {projectDocuments.length > 0 ? `${projectDocuments.length} document${projectDocuments.length === 1 ? '' : 's'}` : 'No documents yet'}
                    </span>
                  </div>
                  {projectDocuments.length > 0 ? (
                    <div className="project-chip-list">
                      {projectDocuments.map((document) => (
                        <button
                          key={document.document_id}
                          type="button"
                          className={`project-chip ${selectedDocuments.some((item) => item.document_id === document.document_id) ? 'project-chip-active' : ''}`}
                          onClick={() => toggleDocument(document)}
                          data-tooltip={`${selectedDocuments.some((item) => item.document_id === document.document_id) ? 'Remove' : 'Attach'} ${document.title}`}
                        >
                          {document.title}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="muted">Attach and upload documents into this project so future queries start with the right context.</p>
                  )}
                </div>
                </>
              ) : null}
              <div className="project-workspace-section project-workspace-section-history">
                <div className="project-workspace-header">
                  <span className="project-workspace-meta">
                    {visibleConversationsForProject.length} conversation{visibleConversationsForProject.length === 1 ? '' : 's'}
                  </span>
                </div>
                <div className="history-list history-list-project">
                  {visibleConversationsForProject.length > 0 ? (
                    visibleConversationsForProject.map((conversation) => (
                      <div
                        key={conversation.conversation_id}
                        className={`history-row history-row-compact ${conversation.conversation_id === activeConversationId ? 'history-row-active' : ''}`}
                      >
                        {editingConversationId === conversation.conversation_id ? (
                          <input
                            className="history-row-input"
                            value={editingConversationTitle}
                            onChange={(event) => setEditingConversationTitle(event.target.value)}
                            onBlur={() => void submitRenameConversation(conversation.conversation_id)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') {
                                event.preventDefault()
                                void submitRenameConversation(conversation.conversation_id)
                              }
                              if (event.key === 'Escape') {
                                event.preventDefault()
                                cancelRenameConversation()
                              }
                            }}
                            autoFocus
                          />
                        ) : (
                          <button
                            type="button"
                            className="history-row-main"
                            onClick={() => void openConversation(conversation.conversation_id)}
                            data-tooltip={`Open ${conversation.title || 'conversation'}`}
                          >
                            <strong>
                              {conversation.pinned ? 'Pinned: ' : ''}
                              {conversation.title || 'New conversation'}
                            </strong>
                          </button>
                        )}
                        {!conversation.conversation_id.startsWith('default:') && editingConversationId !== conversation.conversation_id ? (
                          <button
                            type="button"
                            className="history-row-pin"
                            onClick={() => void applyConversationUpdate(conversation.conversation_id, { pinned: !conversation.pinned })}
                            aria-label={conversation.pinned ? 'Unpin conversation' : 'Pin conversation'}
                            data-tooltip={conversation.pinned ? 'Unpin conversation' : 'Pin conversation'}
                            disabled={updatingConversationId === conversation.conversation_id}
                          >
                            <Pin size={14} />
                          </button>
                        ) : null}
                        {!conversation.conversation_id.startsWith('default:') && editingConversationId !== conversation.conversation_id ? (
                          <button
                            type="button"
                            className="history-row-edit"
                            onClick={() => startRenameConversation(conversation)}
                            aria-label={`Rename ${conversation.title || 'conversation'}`}
                            data-tooltip="Rename conversation"
                          >
                            <Edit3 size={14} />
                          </button>
                        ) : null}
                        {!conversation.conversation_id.startsWith('default:') ? (
                          <button
                            type="button"
                            className="history-row-archive"
                            onClick={() => void applyConversationUpdate(conversation.conversation_id, { archived: true })}
                            aria-label={`Archive ${conversation.title || 'conversation'}`}
                            data-tooltip="Archive conversation"
                            disabled={updatingConversationId === conversation.conversation_id}
                          >
                            <Archive size={14} />
                          </button>
                        ) : null}
                        {!conversation.conversation_id.startsWith('default:') ? (
                          <button
                            type="button"
                            className="history-row-delete"
                            onClick={() => void handleDeleteConversation(conversation.conversation_id)}
                            aria-label={`Delete ${conversation.title || 'conversation'}`}
                            data-tooltip="Delete conversation"
                            disabled={deletingConversationId === conversation.conversation_id}
                          >
                            {deletingConversationId === conversation.conversation_id ? <Loader2 size={14} className="spin" /> : <Trash2 size={14} />}
                          </button>
                        ) : (
                          <span className="history-row-chevron">
                            <ChevronRight size={15} />
                          </span>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="onboarding-list">
                      <div className="onboarding-row">
                        <strong>{activeProject ? 'No project conversations yet' : 'No conversations yet'}</strong>
                        <span>Start one from the plus button above and it will stay grouped here.</span>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>

        <button className="soft-button" onClick={() => fileInputRef.current?.click()} disabled={isUploading} data-tooltip="Upload a document">
          {isUploading ? <Loader2 size={16} className="spin" /> : <FileUp size={16} />}
          Upload document
        </button>
        <button className="soft-button" onClick={signOut} data-tooltip="Sign out">
          <LogOut size={16} />
          Sign out
        </button>
        <input ref={fileInputRef} type="file" hidden onChange={uploadDocument} />
        </>
      }
      main={
        <>
        <header className="main-header">
          <div className="main-header-copy">
            <span className="eyebrow">{activeProject ? activeProject.name : 'General'}</span>
            <h2>Assistant</h2>
          </div>
          <div className="header-meta">
            <button
              type="button"
              className="theme-toggle"
              onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
              aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
              data-tooltip={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
            >
              {theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}
              <span>{theme === 'light' ? 'Dark mode' : 'Light mode'}</span>
            </button>
            <div
              className={`listening-indicator ${
                loadState === 'error'
                  ? 'listening-indicator-error'
                  : loadState === 'loading'
                    ? 'listening-indicator-loading'
                    : 'listening-indicator-live'
              }`}
            >
              <span className="listening-dot" />
              <span>
                {loadState === 'error'
                  ? 'Offline'
                  : loadState === 'loading'
                    ? 'Syncing'
                    : 'Connected'}
              </span>
            </div>
          </div>
        </header>

        {selectedDocuments.length > 0 ? (
          <section className="selection-bar">
            <span className="eyebrow">Attached</span>
            <div className="source-list">
              {selectedDocuments.map((document) => (
                <button
                  key={document.document_id}
                  className="source-chip source-chip-button"
                  onClick={() => toggleDocument(document)}
                  type="button"
                >
                  <strong>{document.title}</strong>
                  <span>Remove</span>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        {errorMessage ? <p className="error-banner">{errorMessage}</p> : null}

        <ThreadPane
          messages={messages}
          pendingQuery={pendingQuery}
          pendingAttachments={pendingAttachments}
          uploadFeed={uploadFeed}
          starterActions={starterActions}
          copiedMessageId={copiedMessageId}
          workbookViewLoadingId={workbookViewLoadingId}
          artifactPreviews={artifactPreviews}
          previewLoadingId={previewLoadingId}
          approvalNotes={approvalNotes}
          resolvingMessageId={resolvingMessageId}
          renderRichContent={renderRichContent}
          renderAnalysisPreview={renderAnalysisSpecPreview}
          messageRefs={messageRefs}
          threadEndRef={threadEndRef}
          onApplySuggestedPrompt={applySuggestedPrompt}
          onCopyResponse={copyResponse}
          onEnsureArtifactPreview={ensureArtifactPreview}
          onDownloadArtifact={downloadArtifact}
          onOpenWorkbook={openWorkbookViewer}
          onApprovalNoteChange={(messageId, value) =>
            setApprovalNotes((current) => ({
              ...current,
              [messageId]: value,
            }))
          }
          onResolveApproval={resolveApproval}
          onSubmitFollowUp={(message, text) => {
            const ctxParts: string[] = []
            const query = message.metadata?.query
            const title = message.answer.title
            const summary = message.presentation?.summary || message.answer.summary
            if (query) ctxParts.push(`Prior question: ${query}`)
            if (title) ctxParts.push(`Prior response: ${title}`)
            if (summary) ctxParts.push(summary)
            // Include key findings from structured sections
            const priorities = message.presentation?.priorities ?? []
            const actions = message.presentation?.recommended_actions ?? []
            for (const section of [...priorities.slice(0, 2), ...actions.slice(0, 2)]) {
              const items = (section.items ?? []).slice(0, 3).join('; ')
              if (items) ctxParts.push(`${section.title}: ${items}`)
            }
            // Finance context
            const fin = message.presentation?.finance
            if (fin?.headline) ctxParts.push(fin.headline)
            if (fin?.takeaways?.length) ctxParts.push(fin.takeaways.slice(0, 2).join('; '))
            const ctx = ctxParts.join(' | ').slice(0, 1200)
            const enriched = ctx ? `[Context: ${ctx}]\n\nFollow-up action: ${text}` : `Follow-up action: ${text}`
            const followUpContext = buildClarificationFollowUpContext(message, text)
            void submitQuery({
              requestText: enriched,
              displayText: text,
              follow_up_context: followUpContext,
            })
          }}
          onInlineAction={async (prompt, intent) => {
            const response = await authenticatedFetch('/assistant/quick', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ prompt, intent }),
            })
            if (!response.ok) throw new Error('Inline action failed')
            const data = await response.json() as { result: string }
            return data.result
          }}
          onIntegrationConnect={handleMessageIntegrationConnect}
        />

        <Suspense fallback={null}>
          <WorkbookWorkspace
            viewer={workbookViewer}
            view={workbookViewer ? workbookViews[workbookViewer.artifact.artifact_id] || null : null}
            activeArtifactSheets={activeArtifactSheets}
            activeArtifactViewTabs={activeArtifactViewTabs}
            artifactPaneWidths={artifactPaneWidths}
            activeProvenanceRows={activeProvenanceRows}
            messages={messages}
            onSetActiveArtifactSheets={setActiveArtifactSheets}
            onSetActiveArtifactViewTabs={setActiveArtifactViewTabs}
            onSetActiveProvenanceRows={setActiveProvenanceRows}
            onSetWorkbookViewer={setWorkbookViewer}
            onStartPaneResize={startPaneResize}
            onDownloadArtifact={downloadArtifact}
          />
        </Suspense>

        <footer className="composer">
          {selectedDocuments.length > 0 ? (
            <div className="attachment-indicator" aria-live="polite">
              <div className="attachment-token-list">
                {selectedDocuments.map((document) => (
                  <button
                    key={document.document_id}
                    type="button"
                    className="attachment-indicator-chip"
                    onClick={() => toggleDocument(document)}
                    data-tooltip={`Remove ${document.title}`}
                  >
                    <Paperclip size={13} />
                    <span>{document.title}</span>
                    <X size={12} />
                  </button>
                ))}
              </div>
              <span className="attachment-indicator-copy">
                {lastAttachedTitle ? `${lastAttachedTitle} uploaded and attached.` : 'Attached for the next request.'}
              </span>
            </div>
          ) : null}
          <div className="composer-shell">
            <textarea
              ref={textareaRef}
              className="composer-input"
              placeholder={
                activeProject
                  ? `Ask about ${activeProject.name}, attach context, or request a report.`
                  : 'Ask for a report, explain a document, or request a business implication brief.'
              }
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              rows={1}
            />
            <div className="composer-actions">
              <button className="soft-button inline" onClick={() => fileInputRef.current?.click()} disabled={isUploading} data-tooltip="Attach a document">
                <Paperclip size={16} />
              </button>
              <button className="send-button" onClick={() => void submitQuery()} disabled={isSending || !query.trim()} data-tooltip="Send request">
                {isSending ? <Loader2 size={16} className="spin" /> : <ArrowRight size={16} />}
                Send
              </button>
            </div>
          </div>
          <div className="composer-footer">
            {query.trim().length > 0 ? <span>Shift+Enter for a new line.</span> : <span>{activeProject ? `Working inside ${activeProject.name}.` : 'Start with one direct request.'}</span>}
            {selectedDocuments.length > 0 ? <span>{selectedDocuments.length} document{selectedDocuments.length === 1 ? '' : 's'} attached</span> : null}
          </div>
        </footer>

        {showScrollToBottom ? (
          <button
            type="button"
            className="scroll-to-bottom-button"
            onClick={jumpToBottom}
            aria-label="Jump to latest message"
            data-tooltip="Jump to latest message"
          >
            <ArrowDown size={18} />
          </button>
        ) : null}
        </>
      }
    />
  )
}
