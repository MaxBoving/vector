import React from 'react'

import { CalendarGrid } from '../CalendarGrid'
import { getExecutiveLeadText, getModeCopy, getPresentationVariantLabel, getTimedOverlapHighlights } from '../messagePresentation'
import type { MessageRendererProps } from './types'

export const CalendarRenderer: React.FC<MessageRendererProps> = ({ message, summary, onFollowUp }) => {
  const cal = message.presentation?.calendar
  const events = cal?.events ?? []
  // Suppress static follow-ups when clickable chips are rendered by the parent
  const followUps = onFollowUp ? [] : (cal?.follow_ups ?? [])
  const copy = getModeCopy(message)
  const variantLabel = getPresentationVariantLabel(message)
  const lead = getExecutiveLeadText(message) || summary
  const conflicts = getTimedOverlapHighlights(events.map((event) => ({
    title: event.title,
    starts_at: event.starts_at,
    ends_at: event.ends_at,
    day_label: event.day_label,
  })))

  // Data guard — if no structured events, render a minimal text fallback
  if (events.length === 0) {
    return (
      <div className="mode-renderer-shell mode-renderer-calendar">
        <div className="calendar-lead-block">
          <span className="calendar-lead-label">{copy.bottomLineLabel}</span>
          {variantLabel ? <span className="mode-variant-pill">{variantLabel}</span> : null}
          <p className="cal-summary">{lead}</p>
        </div>
        <p className="cal-empty">{copy.emptyState}</p>
      </div>
    )
  }

  return (
    <div className="mode-renderer-shell mode-renderer-calendar">
      <div className="calendar-lead-block">
        <span className="calendar-lead-label">{copy.bottomLineLabel}</span>
        {variantLabel ? <span className="mode-variant-pill">{variantLabel}</span> : null}
        <p className="cal-summary">{lead}</p>
      </div>
      {conflicts.length > 0 ? (
        <div className="calendar-conflict-strip">
          <span className="calendar-conflict-label">{copy.implicationLabel}</span>
          <ul className="calendar-conflict-list">
            {conflicts.slice(0, 3).map((conflict) => (
              <li key={conflict} className="calendar-conflict-item">{conflict}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <CalendarGrid events={events} followUps={followUps} />
    </div>
  )
}
