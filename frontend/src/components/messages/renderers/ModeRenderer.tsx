import React, { Suspense, lazy } from 'react'

import { getResolvedPresentationMode } from '../messagePresentation'
import { CompactLayout } from './CompactLayout'
import { DecisionRenderer } from './DecisionRenderer'
import { DraftRenderer } from './DraftRenderer'
import { ReportLayout } from './ReportLayout'
import type { MessageRendererProps } from './types'

const ArtifactRenderer = lazy(() =>
  import('./ArtifactRenderer').then((module) => ({ default: module.ArtifactRenderer })),
)
const CalendarRenderer = lazy(() =>
  import('./CalendarRenderer').then((module) => ({ default: module.CalendarRenderer })),
)
const CanvasRenderer = lazy(() =>
  import('./CanvasRenderer').then((module) => ({ default: module.CanvasRenderer })),
)
const FinanceRenderer = lazy(() =>
  import('./FinanceRenderer').then((module) => ({ default: module.FinanceRenderer })),
)
const MediaRenderer = lazy(() =>
  import('./MediaRenderer').then((module) => ({ default: module.MediaRenderer })),
)
const ScheduleRenderer = lazy(() =>
  import('./ScheduleRenderer').then((module) => ({ default: module.ScheduleRenderer })),
)
const SpreadsheetRenderer = lazy(() =>
  import('./SpreadsheetRenderer').then((module) => ({ default: module.SpreadsheetRenderer })),
)

const withSuspense = (node: React.ReactNode) => (
  <Suspense fallback={<div className="mode-renderer-loading">Loading section…</div>}>{node}</Suspense>
)

export const ModeRenderer: React.FC<MessageRendererProps> = (props) => {
  const mode = getResolvedPresentationMode(props.message)

  switch (mode) {
    case 'brief':
      return <CompactLayout {...props} />
    case 'calendar':
      return withSuspense(<CalendarRenderer {...props} />)
    case 'canvas':
      return withSuspense(<CanvasRenderer {...props} />)
    case 'schedule':
      return withSuspense(<ScheduleRenderer {...props} />)
    case 'decision':
      return <DecisionRenderer {...props} />
    case 'draft':
      return <DraftRenderer {...props} />
    case 'finance':
      return withSuspense(<FinanceRenderer {...props} />)
    case 'artifact':
      return withSuspense(<ArtifactRenderer {...props} />)
    case 'media':
      return withSuspense(<MediaRenderer {...props} />)
    case 'spreadsheet':
      return withSuspense(<SpreadsheetRenderer {...props} />)
    case 'report':
    default:
      return <ReportLayout {...props} />
  }
}
