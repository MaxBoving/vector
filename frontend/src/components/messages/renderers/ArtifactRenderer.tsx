import React from 'react'
import { FileSpreadsheet, FileText, Layout } from '../icons'

import { getModeCopy, getPresentationVariantLabel } from '../messagePresentation'
import type { AssistantArtifact } from '../types'
import type { MessageRendererProps } from './types'

type FormatKey = 'docx' | 'xlsx' | 'pptx' | 'html' | 'default'

const FORMAT_CONFIGS: Record<FormatKey, { label: string; colorClass: string; Icon: typeof FileText }> = {
  docx: { label: 'DOCX', colorClass: 'artifact-format-docx', Icon: FileText },
  xlsx: { label: 'XLSX', colorClass: 'artifact-format-xlsx', Icon: FileSpreadsheet },
  pptx: { label: 'PPTX', colorClass: 'artifact-format-pptx', Icon: FileText },
  html: { label: 'CANVAS', colorClass: 'artifact-format-html', Icon: Layout },
  default: { label: 'FILE', colorClass: 'artifact-format-default', Icon: FileText },
}

function resolveFormat(artifact: AssistantArtifact): FormatKey {
  const fmt = (artifact.format || '').toLowerCase()
  const type = (artifact.artifact_type || '').toLowerCase()
  if (fmt === 'docx' || type.includes('memo') || type.includes('docx')) return 'docx'
  if (fmt === 'xlsx' || type.includes('workbook') || type.includes('xlsx')) return 'xlsx'
  if (fmt === 'pptx' || type.includes('deck') || type.includes('pptx')) return 'pptx'
  if (fmt === 'html' || type.includes('canvas') || type.includes('html')) return 'html'
  return 'default'
}

function statusText(status?: string): string {
  if (!status) return 'Pending'
  if (status === 'ready' || status === 'completed') return 'Ready'
  if (status === 'generating') return 'Generating'
  if (status === 'planned') return 'Planned'
  return status.replace(/_/g, ' ')
}

function templateLabel(templateId?: string): string | null {
  if (!templateId) return null
  return templateId.replace(/_v\d+$/, '').replace(/_/g, ' ')
}

export const ArtifactRenderer: React.FC<MessageRendererProps> = ({ message }) => {
  const copy = getModeCopy(message)
  const variantLabel = getPresentationVariantLabel(message)
  const summary = message.presentation?.summary || message.answer.summary
  const artifacts = message.artifacts

  return (
    <div className="mode-renderer-shell mode-renderer-artifact">
      <div className="artifact-renderer-header">
        <span className="artifact-renderer-kicker">{copy.bottomLineLabel}</span>
        {variantLabel ? <span className="mode-variant-pill">{variantLabel}</span> : null}
        <p className="artifact-renderer-headline">{summary || copy.emptyState}</p>
      </div>

      {artifacts.length > 0 ? (
        <div className="artifact-renderer-list">
          {artifacts.map((artifact) => {
            const fmt = resolveFormat(artifact)
            const { label, colorClass, Icon } = FORMAT_CONFIGS[fmt]
            const isReady = artifact.status === 'ready' || artifact.status === 'completed'
            const tmpl = templateLabel(artifact.metadata?.template_id)

            return (
              <div key={artifact.artifact_id} className={`artifact-renderer-row ${colorClass}`}>
                <div className="artifact-renderer-icon-wrap">
                  <Icon size={17} strokeWidth={1.7} />
                  <span className="artifact-renderer-fmt-badge">{label}</span>
                </div>

                <div className="artifact-renderer-body">
                  <strong className="artifact-renderer-label">{artifact.label}</strong>
                  {artifact.purpose ? (
                    <div className="artifact-renderer-meta-group">
                      <span className="artifact-renderer-meta-label">Why it matters</span>
                      <span className="artifact-renderer-purpose">{artifact.purpose}</span>
                    </div>
                  ) : null}
                  {tmpl ? (
                    <div className="artifact-renderer-meta-group">
                      <span className="artifact-renderer-meta-label">Workstream</span>
                      <span className="artifact-renderer-tag">{tmpl}</span>
                    </div>
                  ) : null}
                </div>

                <div className={`artifact-renderer-status ${isReady ? 'artifact-renderer-status-ready' : 'artifact-renderer-status-pending'}`}>
                  <span className="artifact-renderer-meta-label">State</span>
                  <span className="artifact-renderer-status-row">
                    <span className="artifact-renderer-status-dot" />
                    <span>{statusText(artifact.status)}</span>
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <p className="artifact-renderer-empty">{copy.emptyState}</p>
      )}
    </div>
  )
}
