import React from 'react'

import { getModeCopy } from '../messagePresentation'
import type { MessageRendererProps } from './types'

type SpreadsheetColumn = {
  key: string
  label: string
  width?: number | null
  align?: 'left' | 'center' | 'right' | null
}

type SpreadsheetCell = {
  value?: string | null
  kind?: string | null
  align?: 'left' | 'center' | 'right' | null
}

type SpreadsheetRow = {
  label?: string | null
  cells?: SpreadsheetCell[]
}

const deriveColumns = (columns: SpreadsheetColumn[] | undefined, rows: SpreadsheetRow[]): SpreadsheetColumn[] => {
  if (columns && columns.length > 0) {
    return columns
  }

  const maxCells = rows.reduce((max, row) => Math.max(max, row.cells?.length || 0), 0)
  return Array.from({ length: maxCells }, (_, index) => ({
    key: `column_${index + 1}`,
    label: `Column ${index + 1}`,
    align: 'left' as const,
    width: null,
  }))
}

const resolveAlign = (column: SpreadsheetColumn | undefined, cell: SpreadsheetCell | undefined) =>
  cell?.align || column?.align || 'left'

export const SpreadsheetRenderer: React.FC<MessageRendererProps> = ({ message, summary }) => {
  const sheet = message.presentation?.spreadsheet
  const copy = getModeCopy(message)
  const rows = sheet?.rows || []
  const columns = deriveColumns(sheet?.columns, rows)
  const hasRowLabels = rows.some((row) => Boolean(row.label))
  const hasData = rows.length > 0 && columns.length > 0

  return (
    <div className="mode-renderer-shell mode-renderer-spreadsheet">
      <div className="spreadsheet-renderer">
        <div className="spreadsheet-renderer-header">
          <span className="spreadsheet-renderer-kicker">{copy.bottomLineLabel}</span>
          {sheet?.title ? <h2 className="spreadsheet-renderer-title">{sheet.title}</h2> : null}
          {sheet?.subtitle ? <p className="spreadsheet-renderer-subtitle">{sheet.subtitle}</p> : null}
          {summary ? <p className="spreadsheet-renderer-summary">{summary}</p> : null}
        </div>

        {hasData ? (
          <div className="spreadsheet-grid-wrap">
            <table className="spreadsheet-grid" aria-label={sheet?.title || 'Spreadsheet preview'}>
              <thead>
                <tr>
                  {hasRowLabels ? <th className="spreadsheet-grid-stub">Row</th> : null}
                  {columns.map((column) => (
                    <th key={column.key} style={column.width ? { width: `${column.width}px` } : undefined}>
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rowIndex) => (
                  <tr key={`${row.label || 'row'}-${rowIndex}`}>
                    {hasRowLabels ? (
                      <th className="spreadsheet-grid-row-label">{row.label || `Row ${rowIndex + 1}`}</th>
                    ) : null}
                    {columns.map((column, columnIndex) => {
                      const cell = row.cells?.[columnIndex]
                      const align = resolveAlign(column, cell)
                      const kind = cell?.kind ? cell.kind.replace(/[^a-z0-9_-]/gi, '') : ''
                      return (
                        <td
                          key={`${column.key}-${rowIndex}`}
                          className={`spreadsheet-grid-cell spreadsheet-grid-cell-${align}${kind ? ` spreadsheet-grid-cell-kind-${kind}` : ''}`}
                        >
                          {cell?.value ?? ''}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="spreadsheet-empty-state">
            <p>{copy.emptyState}</p>
          </div>
        )}

        {sheet?.source_artifact_id ? (
          <div className="spreadsheet-renderer-footer">
            <span className="spreadsheet-renderer-source">Linked workbook: {sheet.source_artifact_id}</span>
          </div>
        ) : null}
      </div>
    </div>
  )
}
