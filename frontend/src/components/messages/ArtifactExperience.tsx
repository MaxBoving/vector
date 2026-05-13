import React, { useMemo, useState } from 'react'

import { ArrowDown, Download, Expand, Loader2 } from './icons'
import type { AssistantArtifact } from './types'
import type { ArtifactPreview } from '../dashboard/types'

type ArtifactExperienceProps = {
  artifacts: AssistantArtifact[]
  plannedArtifacts: AssistantArtifact[]
  artifactPreviews: Record<string, ArtifactPreview>
  previewLoadingId: string | null
  workbookLoading: boolean
  renderAnalysisPreview: (artifactId: string, content: string) => React.ReactNode
  onEnsureArtifactPreview: (artifact: AssistantArtifact) => Promise<ArtifactPreview | null>
  onDownloadArtifact: (artifact: AssistantArtifact) => void
  onOpenWorkbook: (artifact: AssistantArtifact) => void
}

type ParsedMemoPreview = {
  title: string
  summary: string
  sections: Array<{ heading: string; bullets: string[] }>
}

type ParsedDeckPreview = {
  title: string
  summary: string
  slides: Array<{ title: string; bullets: string[] }>
}

const stripFrontmatter = (content?: string): string => {
  if (!content) return ''
  if (!content.startsWith('---')) return content.trim()
  const match = content.match(/^---\n[\s\S]*?\n---\n?/)
  return match ? content.slice(match[0].length).trim() : content.trim()
}

const parseMemoPreview = (content?: string): ParsedMemoPreview | null => {
  const body = stripFrontmatter(content)
  if (!body) return null
  const lines = body.split('\n').map((line) => line.trim()).filter(Boolean)
  if (lines.length === 0) return null

  const title = lines[0]
  let summary = ''
  const sections: Array<{ heading: string; bullets: string[] }> = []
  let currentSection: { heading: string; bullets: string[] } | null = null

  for (const line of lines.slice(1)) {
    if (line.includes(':') && !line.startsWith('- ')) {
      const [rawHeading, ...rest] = line.split(':')
      const heading = rawHeading.trim()
      const remainder = rest.join(':').trim()

      if (!summary && heading.toLowerCase() === 'executive summary') {
        summary = remainder
        continue
      }

      currentSection = { heading, bullets: remainder ? [remainder] : [] }
      sections.push(currentSection)
      continue
    }

    if (line.startsWith('- ')) {
      if (!currentSection) {
        currentSection = { heading: 'Highlights', bullets: [] }
        sections.push(currentSection)
      }
      currentSection.bullets.push(line.replace(/^- /, '').trim())
      continue
    }

    if (!summary) {
      summary = line
    } else if (currentSection) {
      currentSection.bullets.push(line)
    }
  }

  return { title, summary, sections }
}

const parseDeckPreview = (content?: string): ParsedDeckPreview | null => {
  const body = stripFrontmatter(content)
  if (!body) return null
  const lines = body.split('\n').map((line) => line.trim()).filter(Boolean)
  if (lines.length === 0) return null

  const title = lines[0]
  const slides: Array<{ title: string; bullets: string[] }> = []
  let summary = ''
  let currentSlide: { title: string; bullets: string[] } | null = null

  for (const line of lines.slice(1)) {
    if (line.startsWith('Slide: ')) {
      currentSlide = { title: line.replace(/^Slide:\s*/, ''), bullets: [] }
      slides.push(currentSlide)
      continue
    }

    if (line.startsWith('- ')) {
      currentSlide?.bullets.push(line.replace(/^- /, '').trim())
      continue
    }

    if (!summary) {
      summary = line
    } else if (currentSlide) {
      currentSlide.bullets.push(line)
    }
  }

  return { title, summary, slides }
}

const formatLabel = (artifact: AssistantArtifact): string =>
  (artifact.format || artifact.artifact_type || 'file').replace(/^report_/, '').replace(/^analysis_/, '').toUpperCase()

const statusLabel = (status?: string): string => {
  if (!status) return 'Pending'
  if (status === 'completed' || status === 'generated' || status === 'ready') return 'Ready'
  if (status === 'planned') return 'Planned'
  return status.replace(/_/g, ' ')
}

const renderDocumentPreview = (artifact: AssistantArtifact, memo: ParsedMemoPreview, previewMode: 'inline' | 'modal') => (
  <div className={`artifact-preview-document-viewer artifact-preview-document-viewer-${previewMode}`}>
    <div className="artifact-preview-document-toolbar">
      <div className="artifact-preview-document-toolbar-left">
        <span className="artifact-preview-document-toolbar-kicker">PDF Preview</span>
        <strong>{artifact.label}</strong>
      </div>
      <div className="artifact-preview-document-toolbar-right">
        <span className="artifact-preview-document-toolbar-meta">{memo.sections.length} sections</span>
        <span className="artifact-preview-document-toolbar-divider" aria-hidden="true">•</span>
        <span className="artifact-preview-document-toolbar-meta">DOCX source</span>
      </div>
    </div>

    <div className="artifact-preview-document-stage">
      <div className="artifact-preview-document-sheet">
        <div className="artifact-preview-document-sheet-header">
          <span className="artifact-preview-document-sheet-kicker">Rendered preview</span>
          <strong>{memo.title}</strong>
          {memo.summary ? <p>{memo.summary}</p> : null}
        </div>
        <div className="artifact-preview-document-sheet-body">
          {memo.sections.map((section) => (
            <section key={section.heading} className="artifact-preview-document-sheet-section">
              <h4>{section.heading}</h4>
              <ul>
                {section.bullets.map((bullet) => (
                  <li key={bullet}>{bullet}</li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  </div>
)

const ArtifactWorkspaceModal: React.FC<{
  artifact: AssistantArtifact
  preview?: ArtifactPreview
  onClose: () => void
  renderAnalysisPreview: (artifactId: string, content: string) => React.ReactNode
}> = ({ artifact, preview, onClose, renderAnalysisPreview }) => {
  const memo = useMemo(() => parseMemoPreview(preview?.content), [preview?.content])
  const deck = useMemo(() => parseDeckPreview(preview?.content), [preview?.content])

  return (
    <div className="artifact-workspace-overlay" role="presentation" onClick={onClose}>
      <div
        className="artifact-workspace-modal"
        role="dialog"
        aria-modal="true"
        aria-label={artifact.label}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="artifact-workspace-header">
          <div>
            <span className="executive-section-label">Workspace</span>
            <h3>{artifact.label}</h3>
          </div>
          <button type="button" className="mini-action" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="artifact-workspace-body">
          {artifact.artifact_type === 'analysis_xlsx' && preview ? (
            renderAnalysisPreview(artifact.artifact_id, preview.content)
          ) : null}

          {artifact.artifact_type === 'report_docx' && memo ? (
            renderDocumentPreview(artifact, memo, 'modal')
          ) : null}

          {artifact.artifact_type === 'report_pptx' && deck ? (
            <div className="artifact-preview-deck artifact-preview-deck-modal">
              <div className="artifact-preview-deck-stage">
                {deck.slides.map((slide, index) => (
                  <section key={`${slide.title}-${index}`} className="artifact-preview-slide">
                    <div className="artifact-preview-slide-frame">
                      <div className="artifact-preview-slide-kicker">Slide {index + 1}</div>
                      <h4>{slide.title}</h4>
                      <ul>
                        {slide.bullets.map((bullet) => (
                          <li key={bullet}>{bullet}</li>
                        ))}
                      </ul>
                    </div>
                  </section>
                ))}
              </div>
            </div>
          ) : null}

          {preview && artifact.artifact_type === 'executive_canvas' ? (
            <div className="artifact-preview-canvas artifact-preview-canvas-modal">
              <iframe
                title={artifact.label}
                className="artifact-preview-canvas-frame artifact-preview-canvas-frame-modal"
                srcDoc={preview.content}
              />
            </div>
          ) : null}

          {preview &&
          artifact.artifact_type !== 'analysis_xlsx' &&
          artifact.artifact_type !== 'executive_canvas' &&
          artifact.artifact_type !== 'report_docx' &&
          artifact.artifact_type !== 'report_pptx' ? (
            <div className="artifact-preview-generic">
              <pre>{stripFrontmatter(preview.content)}</pre>
            </div>
          ) : null}

          {!preview ? (
            <div className="artifact-preview-empty-state">Preview unavailable.</div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export const ArtifactExperience: React.FC<ArtifactExperienceProps> = ({
  artifacts,
  plannedArtifacts,
  artifactPreviews,
  previewLoadingId,
  workbookLoading,
  renderAnalysisPreview,
  onEnsureArtifactPreview,
  onDownloadArtifact,
  onOpenWorkbook,
}) => {
  const [workspaceArtifactId, setWorkspaceArtifactId] = useState<string | null>(null)

  const workspaceArtifact = artifacts.find((artifact) => artifact.artifact_id === workspaceArtifactId) || null
  const workspacePreview = workspaceArtifact ? artifactPreviews[workspaceArtifact.artifact_id] : undefined

  const openWorkspace = async (artifact: AssistantArtifact) => {
    if (artifact.artifact_type === 'analysis_xlsx') {
      onOpenWorkbook(artifact)
      return
    }
    if (!artifactPreviews[artifact.artifact_id]) {
      await onEnsureArtifactPreview(artifact)
    }
    setWorkspaceArtifactId(artifact.artifact_id)
  }

  return (
    <>
      {artifacts.length > 0 || plannedArtifacts.length > 0 ? (
        <section className="executive-artifact-block">
          <div className="executive-artifact-block-header">
            <div>
              <p className="executive-artifact-intro">
                The assistant narration stays in the thread. Generated files render below as outputs with their own preview surfaces and workspace actions.
              </p>
            </div>
          </div>

          <div className="executive-artifact-list">
            {artifacts.map((artifact) => {
              const preview = artifactPreviews[artifact.artifact_id]
              const memo = artifact.artifact_type === 'report_docx' ? parseMemoPreview(preview?.content) : null
              const deck = artifact.artifact_type === 'report_pptx' ? parseDeckPreview(preview?.content) : null
              const isLoading = previewLoadingId === artifact.artifact_id

              return (
                <div key={artifact.artifact_id} className="executive-artifact-card">
                  <div className="executive-artifact-shell">
                    <div className="executive-artifact-copy">
                      <div className="executive-artifact-meta-row">
                        <span className="executive-artifact-format">{formatLabel(artifact)}</span>
                        <span className="executive-artifact-status">{statusLabel(artifact.status)}</span>
                      </div>
                      <strong>{artifact.label}</strong>
                      <span>{artifact.purpose || `Generated ${formatLabel(artifact).toLowerCase()} output ready for review.`}</span>
                    </div>
                    <div className="executive-artifact-actions">
                      <button type="button" className="mini-action" onClick={() => void openWorkspace(artifact)}>
                        {artifact.artifact_type === 'analysis_xlsx'
                          ? (workbookLoading ? <Loader2 size={14} className="spin" /> : <Expand size={14} />)
                          : <Expand size={14} />}
                        {artifact.artifact_type === 'analysis_xlsx' ? 'Workspace' : 'Open'}
                      </button>
                      <button type="button" className="mini-action" onClick={() => onDownloadArtifact(artifact)}>
                        {artifact.artifact_type === 'analysis_xlsx' ? <ArrowDown size={14} /> : <Download size={14} />}
                        Download
                      </button>
                    </div>
                  </div>

                  <div className="executive-artifact-preview">
                    {isLoading ? (
                      <div className="artifact-preview-loading">
                        <Loader2 size={16} className="spin" />
                        <span>Preparing preview…</span>
                      </div>
                    ) : null}

                    {!isLoading && artifact.artifact_type === 'analysis_xlsx' && preview ? (
                      renderAnalysisPreview(artifact.artifact_id, preview.content)
                    ) : null}

                    {!isLoading && artifact.artifact_type === 'report_docx' && memo ? (
                      renderDocumentPreview(artifact, memo, 'inline')
                    ) : null}

                    {!isLoading && artifact.artifact_type === 'report_pptx' && deck ? (
                      <div className="artifact-preview-deck">
                        <div className="artifact-preview-deck-header">
                          <strong>{deck.title}</strong>
                          {deck.summary ? <p>{deck.summary}</p> : null}
                        </div>
                        <div className="artifact-preview-slide-strip">
                          {deck.slides.slice(0, 3).map((slide, index) => (
                            <section key={`${slide.title}-${index}`} className="artifact-preview-slide artifact-preview-slide-compact">
                              <div className="artifact-preview-slide-frame">
                                <div className="artifact-preview-slide-kicker">Slide {index + 1}</div>
                                <h4>{slide.title}</h4>
                                <ul>
                                  {slide.bullets.slice(0, 3).map((bullet) => (
                                    <li key={bullet}>{bullet}</li>
                                  ))}
                                </ul>
                              </div>
                            </section>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {!isLoading && artifact.artifact_type === 'executive_canvas' && preview ? (
                      <div className="artifact-preview-canvas">
                        <div className="artifact-preview-canvas-header">
                          <strong>{artifact.label}</strong>
                          <p>Interactive one-pager preview rendered from the generated HTML artifact.</p>
                        </div>
                        <iframe
                          title={artifact.label}
                          className="artifact-preview-canvas-frame"
                          srcDoc={preview.content}
                        />
                      </div>
                    ) : null}

                    {!isLoading &&
                    preview &&
                    artifact.artifact_type !== 'analysis_xlsx' &&
                    artifact.artifact_type !== 'executive_canvas' &&
                    artifact.artifact_type !== 'report_docx' &&
                    artifact.artifact_type !== 'report_pptx' ? (
                      <div className="artifact-preview-generic">
                        <pre>{stripFrontmatter(preview.content).slice(0, 1200)}</pre>
                      </div>
                    ) : null}

                    {!isLoading && !preview ? (
                      <div className="artifact-preview-empty-state">
                        <span>No inline preview is available yet.</span>
                        <button type="button" className="mini-action" onClick={() => void onEnsureArtifactPreview(artifact)}>
                          <Expand size={14} />
                          Load preview
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              )
            })}

            {plannedArtifacts.map((artifact) => (
              <div key={artifact.artifact_id} className="executive-artifact-card executive-artifact-card-planned">
                <div className="executive-artifact-shell">
                  <div className="executive-artifact-copy">
                    <div className="executive-artifact-meta-row">
                      <span className="executive-artifact-format">{formatLabel(artifact)}</span>
                      <span className="executive-artifact-status executive-artifact-status-planned">{statusLabel(artifact.status)}</span>
                    </div>
                    <strong>{artifact.label}</strong>
                    <span>{artifact.ready_when || artifact.blocking_reason || 'This export is planned but not ready yet.'}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {workspaceArtifact ? (
        <ArtifactWorkspaceModal
          artifact={workspaceArtifact}
          preview={workspacePreview}
          onClose={() => setWorkspaceArtifactId(null)}
          renderAnalysisPreview={renderAnalysisPreview}
        />
      ) : null}
    </>
  )
}
