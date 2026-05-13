import React from 'react'

import type { AssistantMessage } from '../messages/types'
import { Download, X } from './icons'
import type { WorkbookViewResponse, WorkbookViewTab, WorkbookViewerState } from './types'

type WorkbookWorkspaceProps = {
  viewer: WorkbookViewerState | null
  view: WorkbookViewResponse | null
  activeArtifactSheets: Record<string, string>
  activeArtifactViewTabs: Record<string, WorkbookViewTab>
  artifactPaneWidths: Record<string, number>
  activeProvenanceRows: Record<string, { tableTitle: string; rowIndex: number }>
  messages: AssistantMessage[]
  onSetActiveArtifactSheets: React.Dispatch<React.SetStateAction<Record<string, string>>>
  onSetActiveArtifactViewTabs: React.Dispatch<React.SetStateAction<Record<string, WorkbookViewTab>>>
  onSetActiveProvenanceRows: React.Dispatch<React.SetStateAction<Record<string, { tableTitle: string; rowIndex: number }>>>
  onSetWorkbookViewer: React.Dispatch<React.SetStateAction<WorkbookViewerState | null>>
  onStartPaneResize: (artifactId: string, startX: number) => void
  onDownloadArtifact: (message: AssistantMessage, artifact: AssistantMessage['artifacts'][number]) => void
}

const extractNumericValue = (value?: string) => {
  if (!value) return null
  const normalized = value.replace(/[$,%]/g, '').replace(/,/g, '').trim()
  const parsed = Number.parseFloat(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

const formatWorkbookCell = (value?: string) => {
  if (!value) return '—'
  const numericValue = extractNumericValue(value)
  if (numericValue === null) return value
  if (value.includes('%')) return `${numericValue}%`
  if (value.includes('$')) return value
  return Number.isInteger(numericValue)
    ? numericValue.toLocaleString()
    : numericValue.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

const buildPivotSnapshot = (table?: { columns: string[]; rows: string[][] }) => {
  if (!table || !table.rows.length || table.columns.length < 2) return []
  const grouped = new Map<string, number>()
  table.rows.forEach((row) => {
    const groupLabel = row[0] || 'Other'
    const numericCandidates = row
      .slice(1)
      .map((cell) => extractNumericValue(cell))
      .filter((value): value is number => value !== null)
    const numericValue = numericCandidates[numericCandidates.length - 1]
    if (numericValue === undefined) return
    grouped.set(groupLabel, (grouped.get(groupLabel) || 0) + numericValue)
  })
  return Array.from(grouped.entries())
    .map(([label, value]) => ({ label, value }))
    .sort((left, right) => right.value - left.value)
    .slice(0, 6)
}

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

const provenanceToneClass = (sourceType?: string) => {
  if (sourceType === 'company_state') return 'provenance-pill-state'
  if (sourceType === 'retrieved_document') return 'provenance-pill-document'
  if (sourceType === 'derived_metric') return 'provenance-pill-derived'
  return 'provenance-pill-fallback'
}

const summarizeWorkbookTab = (tab: WorkbookViewResponse['tabs'][number]) => {
  const primaryTable = tab.tables.find((table) => table.rows.length > 0) || tab.tables[0]
  return {
    metricCount: tab.metrics.length,
    tableCount: tab.tables.length,
    rowCount: primaryTable?.rows.length || 0,
    chartCount: tab.charts.length,
    pivotCount: tab.pivot_snapshots.length,
  }
}

const lastNumericValue = (row: string[]) => {
  const numericCandidates = row
    .slice(1)
    .map((cell) => extractNumericValue(cell))
    .filter((value): value is number => value !== null)
  return numericCandidates[numericCandidates.length - 1] ?? null
}

const buildChartCallouts = (rows: string[][]) => {
  const chartRows = rows
    .map((row) => ({ label: row[0] || 'Item', value: lastNumericValue(row) }))
    .filter((row): row is { label: string; value: number } => row.value !== null)
    .slice(0, 6)

  if (chartRows.length < 2) return []

  const sorted = [...chartRows].sort((left, right) => right.value - left.value)
  return [
    `Lead: ${sorted[0].label} at ${sorted[0].value.toLocaleString()}.`,
    `Range: ${(sorted[0].value - sorted[sorted.length - 1].value).toLocaleString()} across visible rows.`,
  ]
}

const buildSheetInsights = (
  tab: WorkbookViewResponse['tabs'][number],
  primaryTable: WorkbookViewResponse['tabs'][number]['tables'][number] | undefined,
  comparisonSummary: ReturnType<typeof summarizeComparisonBasis>,
) => {
  const insights: string[] = []
  if (tab.metrics[0]) insights.push(`${tab.metrics[0].label} is currently ${tab.metrics[0].value}.`)
  if (primaryTable?.rows.length) {
    const ranked = primaryTable.rows
      .map((row) => ({ label: row[0] || 'Item', value: lastNumericValue(row) }))
      .filter((row): row is { label: string; value: number } => row.value !== null)
      .sort((left, right) => right.value - left.value)
    if (ranked[0]) insights.push(`${ranked[0].label} is the strongest visible row in this tab.`)
    if (ranked.length > 1) insights.push(`${ranked[ranked.length - 1].label} is the weakest visible row and the first candidate for drilldown.`)
  }
  if (comparisonSummary.comparisonLabel) insights.push(`Comparison basis is ${comparisonSummary.comparisonLabel}.`)
  if (tab.charts.length > 0) insights.push(`${tab.charts.length} chart${tab.charts.length === 1 ? '' : 's'} available for the active tab.`)
  return insights.slice(0, 4)
}

const buildRowSnapshot = (columns: string[], row: string[]) =>
  columns.map((column, index) => ({ column, value: formatWorkbookCell(row[index]) }))

const renderSimpleChart = (
  chart: { title: string; chart_type: string },
  rows: string[][],
) => {
  const chartRows = rows
    .map((row) => ({ label: row[0] || 'Item', value: extractNumericValue(row[1]) }))
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
  const callouts = buildChartCallouts(rows)
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

export const WorkbookWorkspace: React.FC<WorkbookWorkspaceProps> = ({
  viewer,
  view,
  activeArtifactSheets,
  activeArtifactViewTabs,
  artifactPaneWidths,
  activeProvenanceRows,
  messages,
  onSetActiveArtifactSheets,
  onSetActiveArtifactViewTabs,
  onSetActiveProvenanceRows,
  onSetWorkbookViewer,
  onStartPaneResize,
  onDownloadArtifact,
}) => {
  if (!viewer || !view) return null

  if (!view.tabs.length) {
    return (
      <div className="workbook-viewer-overlay" role="presentation" onClick={() => onSetWorkbookViewer(null)}>
        <div className="workbook-viewer-modal" role="dialog" aria-modal="true" aria-label={viewer.title} onClick={(event) => event.stopPropagation()}>
          <div className="workbook-viewer-body">
            <div className="analysis-empty-state">
              <strong>No workbook tabs</strong>
              <span>This workbook view did not include any tabs.</span>
            </div>
          </div>
        </div>
      </div>
    )
  }

  const artifactId = viewer.artifact.artifact_id
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
  const sheetInsights = buildSheetInsights(activeTab, primaryTable, comparisonSummary)
  const selectedRow = activeProvenanceRows[artifactId]
  const selectedTable = selectedRow?.tableTitle
    ? activeTab.tables.find((table) => table.title === selectedRow.tableTitle)
    : null
  const selectedRowValues =
    selectedRow && selectedTable?.rows[selectedRow.rowIndex]
      ? buildRowSnapshot(selectedTable.columns, selectedTable.rows[selectedRow.rowIndex])
      : []
  const selectedProvenance =
    selectedRow && selectedTable?.row_provenance?.[selectedRow.rowIndex]
      ? selectedTable.row_provenance[selectedRow.rowIndex]
      : null

  return (
    <div className="workbook-viewer-overlay" role="presentation" onClick={() => onSetWorkbookViewer(null)}>
      <div
        className="workbook-viewer-modal"
        role="dialog"
        aria-modal="true"
        aria-label={viewer.title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="workbook-viewer-header">
          <div>
            <span className="eyebrow">Workbook Viewer</span>
            <h3>{viewer.title}</h3>
          </div>
          <div className="workbook-viewer-actions">
            <button
              type="button"
              className="mini-action"
              onClick={() => {
                const viewerMessage = messages.find((message) => message.message_id === viewer.messageId)
                if (viewerMessage) onDownloadArtifact(viewerMessage, viewer.artifact)
              }}
              data-tooltip="Download workbook (.xlsx)"
            >
              <Download size={14} />
              Download .xlsx
            </button>
            <button type="button" className="icon-action" onClick={() => onSetWorkbookViewer(null)} aria-label="Close workbook viewer" data-tooltip="Close workbook viewer">
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="workbook-viewer-body">
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
                      onSetActiveArtifactSheets((current) => ({
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
            {sheetInsights.length > 0 ? (
              <div className="analysis-sheet-insights">
                {sheetInsights.map((insight) => (
                  <div key={`${activeTab.name}-${insight}`} className="analysis-sheet-insight-card">
                    {insight}
                  </div>
                ))}
              </div>
            ) : null}

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

            <div className="analysis-view-tabs analysis-view-tabs-sticky" role="tablist" aria-label="Workbook views">
              <button
                type="button"
                className={`analysis-view-tab ${activeViewTab === 'sheet' ? 'analysis-view-tab-active' : ''}`}
                onClick={() => onSetActiveArtifactViewTabs((current) => ({ ...current, [artifactId]: 'sheet' }))}
              >
                Sheet
              </button>
              {activeTab.charts.length > 0 ? (
                <button
                  type="button"
                  className={`analysis-view-tab ${activeViewTab === 'charts' ? 'analysis-view-tab-active' : ''}`}
                  onClick={() => onSetActiveArtifactViewTabs((current) => ({ ...current, [artifactId]: 'charts' }))}
                >
                  Charts
                </button>
              ) : null}
              {derivedPivotSnapshot && derivedPivotSnapshot.rows.length > 0 ? (
                <button
                  type="button"
                  className={`analysis-view-tab ${activeViewTab === 'pivot' ? 'analysis-view-tab-active' : ''}`}
                  onClick={() => onSetActiveArtifactViewTabs((current) => ({ ...current, [artifactId]: 'pivot' }))}
                >
                  Pivot
                </button>
              ) : null}
            </div>

            <div className="analysis-sheet analysis-sheet-grid analysis-sheet-grid-with-inspector analysis-workbook-stage analysis-workbook-stage-viewer">
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
                onMouseDown={(event) => onStartPaneResize(artifactId, event.clientX)}
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
                                    onSetActiveProvenanceRows((current) => ({
                                      ...current,
                                      [artifactId]: { tableTitle: table.title, rowIndex },
                                    }))
                                  }
                                >
                                  {row.map((cell, cellIndex) => (
                                    <td
                                      key={`${activeTab.name}-${table.title}-${rowIndex}-${cellIndex}`}
                                      className={extractNumericValue(cell) !== null ? 'analysis-grid-cell-numeric' : undefined}
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
                  )) : (
                    <div className="analysis-empty-state">
                      <strong>No sheet preview</strong>
                      <span>This workbook view did not include any tables for the active tab.</span>
                    </div>
                  )
                ) : null}
              </div>

              <aside className="analysis-sheet-inspector">
                <div className="analysis-sheet-inspector-header">
                  <span className="artifact-preview-label">Provenance Inspector</span>
                  <strong>
                    {selectedRow
                      ? `${selectedRow.tableTitle} · Row ${selectedRow.rowIndex + 1}`
                      : 'Select a row'}
                  </strong>
                </div>

                {selectedProvenance ? (
                  <div className="analysis-sheet-inspector-body">
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

                    <div className="provenance-drawer">
                      <div className="provenance-drawer-header">
                        <strong>Source trail</strong>
                        <span className={`provenance-pill ${provenanceToneClass(selectedProvenance.source_type)}`}>
                          {selectedProvenance.source_type || 'Unknown source'}
                        </span>
                      </div>
                      <p className="provenance-ref">
                        {String(selectedProvenance.source_ref || selectedProvenance.source_type || 'Unknown')}
                      </p>
                      {selectedProvenance.source_excerpt ? (
                        <p className="provenance-excerpt">{String(selectedProvenance.source_excerpt)}</p>
                      ) : (
                        <p className="provenance-excerpt">No excerpt was attached to this row provenance entry.</p>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="analysis-sheet-inspector-empty">
                    <strong>No row selected</strong>
                    <span>Click a table row to inspect its values, source reference, and supporting excerpt.</span>
                  </div>
                )}
              </aside>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
