/**
 * CalendarGrid — data-driven calendar widget.
 * Renders whenever a message has presentation.calendar.events populated.
 * Not tied to any specific workflow_type or presentation mode.
 */

import React from 'react'

type CalEvent = {
  title: string
  starts_at?: string | null
  ends_at?: string | null
  day_label?: string | null
  attendees?: string[]
  location?: string | null
  kind?: string | null
}

// ── time helpers ──────────────────────────────────────────────────────────────

const GRID_START_HOUR = 8
const GRID_END_HOUR   = 20
const GRID_HOURS      = GRID_END_HOUR - GRID_START_HOUR
const ROW_HEIGHT_PX   = 56

function parseIso(ts: string | null | undefined): Date | null {
  if (!ts) return null
  try { return new Date(ts) } catch { return null }
}

function toMinutesFromMidnight(d: Date): number {
  return d.getHours() * 60 + d.getMinutes()
}

function fmtHour(h: number): string {
  const suffix = h < 12 ? 'AM' : 'PM'
  const display = h % 12 === 0 ? 12 : h % 12
  return `${display} ${suffix}`
}

function fmtTime(d: Date): string {
  const h = d.getHours()
  const m = d.getMinutes()
  const suffix = h < 12 ? 'AM' : 'PM'
  const displayH = h % 12 === 0 ? 12 : h % 12
  const displayM = m === 0 ? '' : `:${String(m).padStart(2, '0')}`
  return `${displayH}${displayM} ${suffix}`
}

function fmtDayLabel(d: Date): string {
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
}

// ── overlap-aware positioning ─────────────────────────────────────────────────

type PositionedEvent = CalEvent & {
  topPct: number
  heightPct: number
  startMin: number
  endMin: number
  startDate: Date
  endDate: Date | null
  column: number
  columnCount: number
}

function positionEvents(events: CalEvent[]): PositionedEvent[] {
  const gridStartMin = GRID_START_HOUR * 60
  const gridEndMin   = GRID_END_HOUR * 60
  const gridSpan     = gridEndMin - gridStartMin

  const timed = events
    .map((ev) => {
      const startDate = parseIso(ev.starts_at)
      const endDate   = parseIso(ev.ends_at)
      if (!startDate) return null
      const startMin = toMinutesFromMidnight(startDate)
      const endMin   = endDate ? toMinutesFromMidnight(endDate) : startMin + 60
      const clampedStart = Math.max(startMin, gridStartMin)
      const clampedEnd   = Math.min(endMin,   gridEndMin)
      if (clampedStart >= clampedEnd) return null
      return {
        ...ev,
        topPct:    ((clampedStart - gridStartMin) / gridSpan) * 100,
        heightPct: ((clampedEnd   - clampedStart) / gridSpan) * 100,
        startMin, endMin, startDate, endDate,
        column: 0, columnCount: 1,
      }
    })
    .filter(Boolean) as PositionedEvent[]

  // Assign columns to overlapping events
  for (let i = 0; i < timed.length; i++) {
    const ev = timed[i]
    const overlapping = timed.filter((o, j) => j !== i && o.startMin < ev.endMin && o.endMin > ev.startMin)
    const used = new Set(overlapping.map((o) => o.column))
    let col = 0
    while (used.has(col)) col++
    ev.column = col
  }
  const maxCol = timed.reduce((m, ev) => Math.max(m, ev.column), 0)
  for (const ev of timed) {
    const siblings = timed.filter((o) => o.startMin < ev.endMin && o.endMin > ev.startMin)
    ev.columnCount = siblings.length > 1 ? maxCol + 1 : 1
  }
  return timed
}

// ── group events by calendar date ─────────────────────────────────────────────

type DayGroup = { label: string; date: Date; events: CalEvent[] }

function groupByDay(events: CalEvent[]): DayGroup[] {
  const map = new Map<string, DayGroup>()
  for (const ev of events) {
    const d   = parseIso(ev.starts_at)
    const key = d ? d.toDateString() : 'unscheduled'
    if (!map.has(key)) {
      map.set(key, { label: d ? fmtDayLabel(d) : 'Unscheduled', date: d ?? new Date(), events: [] })
    }
    map.get(key)!.events.push(ev)
  }
  return [...map.values()].sort((a, b) => a.date.getTime() - b.date.getTime())
}

// ── color assignment ──────────────────────────────────────────────────────────

const BLOCK_COLORS = ['cal-event-blue', 'cal-event-teal', 'cal-event-violet', 'cal-event-amber', 'cal-event-rose']

// ── single day column ─────────────────────────────────────────────────────────

function DayGrid({ label, events }: { label: string; events: CalEvent[] }) {
  const positioned = positionEvents(events)
  const totalHeight = GRID_HOURS * ROW_HEIGHT_PX
  const hours = Array.from({ length: GRID_HOURS + 1 }, (_, i) => GRID_START_HOUR + i)

  return (
    <div className="cal-day-section">
      <div className="cal-day-header">
        <span className="cal-day-label">{label}</span>
      </div>
      <div className="cal-day-grid" style={{ height: `${totalHeight}px` }}>
        {/* Time gutter */}
        <div className="cal-time-gutter">
          {hours.map((h) => (
            <div
              key={h}
              className="cal-hour-row"
              style={{ height: h < GRID_END_HOUR ? `${ROW_HEIGHT_PX}px` : '0' }}
            >
              <span className="cal-hour-label">{fmtHour(h)}</span>
            </div>
          ))}
        </div>

        {/* Grid lines */}
        <div className="cal-grid-lines" aria-hidden="true">
          {hours.slice(0, -1).map((h) => (
            <div key={h} className="cal-grid-line" style={{ height: `${ROW_HEIGHT_PX}px` }} />
          ))}
        </div>

        {/* Event blocks */}
        <div className="cal-events-layer">
          {positioned.map((ev, i) => {
            const color    = BLOCK_COLORS[i % BLOCK_COLORS.length]
            const colCount = ev.columnCount > 1 ? ev.columnCount : 1
            const widthPct = colCount > 1 ? (90 / colCount) : 96
            const leftPct  = colCount > 1 ? (ev.column * (90 / colCount)) : 2
            const compact  = (ev.endMin - ev.startMin) <= 30

            return (
              <div
                key={`${ev.title}-${i}`}
                className={`cal-event-block ${color}${compact ? ' cal-event-compact' : ''}`}
                style={{ top: `${ev.topPct}%`, height: `${ev.heightPct}%`, left: `${leftPct}%`, width: `${widthPct}%` }}
                title={ev.location ? `${ev.title} · ${ev.location}` : ev.title}
              >
                <span className="cal-event-title">{ev.title}</span>
                {!compact ? (
                  <span className="cal-event-time">
                    {fmtTime(ev.startDate)}
                    {ev.endDate ? ` – ${fmtTime(ev.endDate)}` : ''}
                  </span>
                ) : null}
                {!compact && ev.attendees && ev.attendees.length > 0 ? (
                  <span className="cal-event-attendees">
                    {ev.attendees.slice(0, 2).join(', ')}
                    {ev.attendees.length > 2 ? ` +${ev.attendees.length - 2}` : ''}
                  </span>
                ) : null}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ── public component ──────────────────────────────────────────────────────────

type CalendarGridProps = {
  events: CalEvent[]
  followUps?: string[]
}

export const CalendarGrid: React.FC<CalendarGridProps> = ({ events, followUps = [] }) => {
  const days = groupByDay(events)
  if (days.length === 0) return null

  return (
    <div className="cal-grid-widget">
      <div className="cal-days">
        {days.map((day) => (
          <DayGrid key={day.label} label={day.label} events={day.events} />
        ))}
      </div>
      {followUps.length > 0 ? (
        <div className="cal-followups">
          <span className="cal-followups-label">Follow-ups</span>
          <ul className="cal-followups-list">
            {followUps.map((f) => <li key={f}>{f}</li>)}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
