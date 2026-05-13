import React from 'react'
import { CalendarDays, Loader2 } from '../icons'

import {
  cleanRichText,
  getExecutiveLeadText,
  getModeCopy,
  getPresentationVariantLabel,
  getScheduleLayoutMode,
  getTimedOverlapHighlights,
  getWeeklyPlanPresentation,
} from '../messagePresentation'
import { InlineResult } from './InlineResult'
import { useInlineAction } from './useInlineAction'
import type { MessageRendererProps } from './types'

// ── helpers ────────────────────────────────────────────────

function parseTime(value?: string | null): Date | null {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d
}

function fmtTime(value?: string | null): string | null {
  const d = parseTime(value)
  if (!d) return null
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

function fmtTimeRange(startsAt?: string | null, endsAt?: string | null, fallback?: string | null): string | null {
  const s = fmtTime(startsAt)
  const e = fmtTime(endsAt)
  if (s && e) return `${s}–${e}`
  if (s) return s
  return fallback || null
}

function fmtDuration(startsAt?: string | null, endsAt?: string | null): string | null {
  const s = parseTime(startsAt)
  const e = parseTime(endsAt)
  if (!s || !e) return null
  const mins = Math.round((e.getTime() - s.getTime()) / 60000)
  if (mins < 60) return `${mins} min`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

function isToday(dayLabel?: string | null, startsAt?: string | null): boolean {
  if (dayLabel && /\btoday\b/i.test(dayLabel)) return true
  if (!startsAt) return false
  const d = parseTime(startsAt)
  if (!d) return false
  const now = new Date()
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
}

function formatDayHeader(dayLabel?: string | null, startsAt?: string | null): { label: string; date: string; today: boolean } {
  const today = isToday(dayLabel, startsAt)
  const d = parseTime(startsAt) || new Date()
  const dateStr = d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
  if (today) return { label: 'Today', date: dateStr, today: true }
  if (dayLabel) return { label: dayLabel, date: dateStr, today: false }
  return { label: dateStr, date: '', today: false }
}

function formatOverviewDayLabel(startsAt?: string | null): string {
  const d = parseTime(startsAt)
  if (!d) return 'Unscheduled'
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

function chunkArray<T>(items: T[], size: number): T[][] {
  if (size <= 0) return [items]
  const chunks: T[][] = []
  for (let index = 0; index < items.length; index += size) {
    chunks.push(items.slice(index, index + size))
  }
  return chunks
}

type OverviewSummary = {
  label: string
  meta: string
  text: string
}

function buildMeetingSummaries(meetings: Array<{ title: string; starts_at?: string | null; ends_at?: string | null }>): OverviewSummary[] {
  const grouped = meetings.reduce<Record<string, { label: string; items: Array<{ title: string; starts_at?: string | null; ends_at?: string | null }> }>>(
    (acc, meeting) => {
      const label = formatOverviewDayLabel(meeting.starts_at)
      acc[label] = acc[label] || { label, items: [] }
      acc[label].items.push(meeting)
      return acc
    },
    {},
  )

  return Object.values(grouped)
    .slice(0, 4)
    .map((group) => {
      const titles = group.items.slice(0, 2).map((meeting) => meeting.title)
      const remainder = group.items.length - titles.length
      const titleText = titles.join(' · ')
      const tail = remainder > 0 ? ` +${remainder} more` : ''
      return {
        label: group.label,
        meta: `${group.items.length} ${group.items.length === 1 ? 'meeting' : 'meetings'}`,
        text: `${titleText}${tail}`,
      }
    })
}

function buildDeadlineSummaries(deadlines: string[]): OverviewSummary[] {
  return chunkArray(deadlines.slice(0, 6), 3).map((chunk, index) => ({
    label: index === 0 ? 'Deadline signals' : 'Additional signals',
    meta: `${chunk.length} ${chunk.length === 1 ? 'item' : 'items'}`,
    text: chunk.join(' · '),
  }))
}

type SlotKind = 'focus' | 'meeting' | 'deadline' | 'follow_up' | 'document_prep'

function slotKind(kind?: string | null): SlotKind {
  if (kind === 'meeting') return 'meeting'
  if (kind === 'meeting_prep') return 'focus'
  if (kind === 'deadline') return 'deadline'
  if (kind === 'follow_up') return 'follow_up'
  if (kind === 'document_prep') return 'document_prep'
  return 'focus'
}

function slotKindLabel(kind: SlotKind): string {
  if (kind === 'meeting') return 'Meeting'
  if (kind === 'deadline') return 'Deadline'
  if (kind === 'follow_up') return 'Follow-up'
  if (kind === 'document_prep') return 'Doc prep'
  return 'Focus'
}

// Unified slot type — blocks + meetings merged
type TimeSlot = {
  title: string
  kind: SlotKind
  starts_at?: string | null
  ends_at?: string | null
  time_window?: string | null
  reason?: string | null
  day_label?: string | null
  attendees?: string[]
  source: 'block' | 'meeting'
}

// ── SlotCard ───────────────────────────────────────────────

type SlotCardProps = {
  slot: TimeSlot
  index: number
  compact?: boolean
  onInlineAction?: MessageRendererProps['onInlineAction']
}

const SlotCard: React.FC<SlotCardProps> = ({ slot, index, compact, onInlineAction }) => {
  const { status, result, trigger, dismiss } = useInlineAction(onInlineAction)
  const timeRange = fmtTimeRange(slot.starts_at, slot.ends_at, slot.time_window)
  const duration = fmtDuration(slot.starts_at, slot.ends_at)
  const kind = slot.kind
  const showPrep = onInlineAction && (kind === 'focus' || kind === 'meeting')

  if (compact) {
    return (
      <article className={`sched-slot sched-slot-${kind} sched-slot-compact`}>
        <div className="sched-slot-accent" />
        {timeRange ? <span className="sched-slot-time-compact">{timeRange}</span> : null}
        <span className="sched-slot-badge">{slotKindLabel(kind)}</span>
        <strong className="sched-slot-title">{slot.title}</strong>
        {slot.reason ? <p className="sched-slot-reason">{slot.reason}</p> : null}
        {showPrep ? (
          <button
            type="button"
            className="schedule-block-action"
            disabled={status === 'loading'}
            onClick={() => trigger('schedule-prep', `Prepare a focused brief for "${slot.title}". Key objectives, critical context, questions, and risks.`)}
          >
            {status === 'loading' ? <Loader2 size={11} className="spin" /> : 'Prep brief'}
          </button>
        ) : null}
        {result ? <InlineResult label="Prep Brief" result={result} onDismiss={dismiss} className="schedule-inline-result" /> : null}
      </article>
    )
  }

  return (
    <article className={`sched-slot sched-slot-${kind}`} data-index={index}>
      <div className="sched-slot-accent" />
      <div className="sched-slot-time-col">
        {timeRange ? (
          <>
            <span className="sched-slot-time-start">{timeRange.split('–')[0]}</span>
            {timeRange.includes('–') ? <span className="sched-slot-time-end">–{timeRange.split('–')[1]}</span> : null}
          </>
        ) : (
          <span className="sched-slot-time-index">{String(index + 1).padStart(2, '0')}</span>
        )}
        {duration ? <span className="sched-slot-duration">{duration}</span> : null}
      </div>
      <div className="sched-slot-body">
        <div className="sched-slot-body-top">
          <span className="sched-slot-badge">{slotKindLabel(kind)}</span>
          {showPrep ? (
            <button
              type="button"
              className="schedule-block-action"
              disabled={status === 'loading'}
              onClick={() => trigger('schedule-prep', `Prepare a focused brief for "${slot.title}". Key objectives, critical context, questions, and risks.`)}
            >
              {status === 'loading' ? <Loader2 size={11} className="spin" /> : 'Prep brief'}
            </button>
          ) : null}
        </div>
        <strong className="sched-slot-title">{slot.title}</strong>
        {slot.reason ? <p className="sched-slot-reason">{slot.reason}</p> : null}
        {slot.attendees && slot.attendees.length > 0 ? (
          <p className="sched-slot-attendees">{slot.attendees.join(', ')}</p>
        ) : null}
        {result ? <InlineResult label="Prep Brief" result={result} onDismiss={dismiss} className="schedule-inline-result" /> : null}
      </div>
    </article>
  )
}

// ── Day header ─────────────────────────────────────────────

type DayHeaderProps = {
  dayLabel?: string | null
  startsAt?: string | null
  totalSlots: number
}

const DayHeader: React.FC<DayHeaderProps> = ({ dayLabel, startsAt, totalSlots }) => {
  const { label, date, today } = formatDayHeader(dayLabel, startsAt)
  return (
    <div className={`sched-day-header${today ? ' sched-day-header-today' : ''}`}>
      <div className="sched-day-header-left">
        <CalendarDays size={14} className="sched-day-header-icon" />
        <span className="sched-day-header-label">{label}</span>
      </div>
      <div className="sched-day-header-right">
        {date ? <span className="sched-day-header-date">{date}</span> : null}
        <span className="sched-day-header-count">{totalSlots} {totalSlots === 1 ? 'block' : 'blocks'}</span>
      </div>
    </div>
  )
}

// ── Main renderer ───────────────────────────────────────────

export const ScheduleRenderer: React.FC<MessageRendererProps> = ({ message, onInlineAction, onFollowUp }) => {
  const weeklyPlan = getWeeklyPlanPresentation(message)
  const copy = getModeCopy(message)
  const variantLabel = getPresentationVariantLabel(message)
  const layoutMode = getScheduleLayoutMode(message)
  const isTimelineView = layoutMode === 'timeline'
  const isOverview = layoutMode === 'overview'
  const reportItemLimit = isOverview ? 2 : 4
  const summary = getExecutiveLeadText(message) || message.answer.summary

  const rawBlocks = weeklyPlan?.blocks || []
  const rawMeetings = weeklyPlan?.meetings || []
  const deadlines = (weeklyPlan?.deadlines || []).filter(
    (d) => !/(no (clear |known )?(deadlines?|tasks?)( (were |have been )?detected| found)|nothing (detected|found|scheduled))/i.test(d)
  )
  // Compact report layouts include follow-ups inline; detailed timelines suppress them when clickable chips are mounted elsewhere.
  const followUps = isTimelineView ? (onFollowUp ? [] : (weeklyPlan?.follow_ups || [])) : (weeklyPlan?.follow_ups || [])

  // Convert meetings to TimeSlot and merge with blocks
  const meetingSlots: TimeSlot[] = rawMeetings.map((m) => ({
    title: m.title,
    kind: 'meeting' as SlotKind,
    starts_at: m.starts_at,
    ends_at: m.ends_at,
    attendees: m.attendees,
    source: 'meeting',
  }))

  const blockSlots: TimeSlot[] = rawBlocks.map((b) => ({
    title: b.title,
    kind: slotKind(b.kind),
    starts_at: b.starts_at,
    ends_at: b.ends_at,
    time_window: b.time_window,
    reason: b.reason,
    day_label: b.day_label,
    source: 'block',
  }))

  // Merge and sort by start time
  const allSlots: TimeSlot[] = [...blockSlots, ...meetingSlots].sort((a, b) => {
    const at = a.starts_at ? new Date(a.starts_at).getTime() : Number.MAX_SAFE_INTEGER
    const bt = b.starts_at ? new Date(b.starts_at).getTime() : Number.MAX_SAFE_INTEGER
    return at - bt
  })
  const conflicts = getTimedOverlapHighlights(allSlots.map((slot) => ({
    title: slot.title,
    starts_at: slot.starts_at,
    ends_at: slot.ends_at,
    day_label: slot.day_label,
  })))

  // Group by day
  const dayGroups = allSlots.reduce<Record<string, TimeSlot[]>>((acc, slot) => {
    const key = slot.day_label || formatDayHeader(null, slot.starts_at).date || 'Schedule'
    acc[key] = acc[key] || []
    acc[key].push(slot)
    return acc
  }, {})

  const dayEntries = Object.entries(dayGroups)
  const isMultiDay = dayEntries.length > 1
  const window = weeklyPlan?.planning_window
  const topBlocks = [...rawBlocks].slice(0, 4)
  const leadLabel = isTimelineView ? copy.bottomLineLabel : 'This period at a glance'
  const weeklyOpenTasks = topBlocks.length > 0 ? topBlocks : rawBlocks.slice(0, 4)
  const reportGridClass = `schedule-weekly-report-grid schedule-weekly-report-grid-${layoutMode}`
  const overviewMeetingSummaries = isOverview ? buildMeetingSummaries(rawMeetings) : []
  const overviewDeadlineSummaries = isOverview ? buildDeadlineSummaries(deadlines) : []

  return (
    <div className="mode-renderer-shell mode-renderer-schedule">
      <div className="schedule-renderer">
        <div className="schedule-lead-block">
          <span className="schedule-lead-label">{leadLabel}</span>
          {variantLabel ? <span className="mode-variant-pill">{variantLabel}</span> : null}
          <p className="schedule-renderer-summary">{summary}</p>
        </div>

        {conflicts.length > 0 || deadlines.length > 0 ? (
          <div className="schedule-conflict-strip">
            {conflicts.length > 0 ? (
              <div className="schedule-conflict-group">
                <span className="schedule-conflict-label">{copy.implicationLabel}</span>
                <ul className="schedule-conflict-list">
                  {conflicts.slice(0, 3).map((conflict) => (
                    <li key={conflict} className="schedule-conflict-item">{cleanRichText(conflict)}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {deadlines.length > 0 ? (
              <div className="schedule-conflict-group">
                <span className="schedule-conflict-label">{copy.watchLabel}</span>
                <ul className="schedule-conflict-list">
                  {deadlines.slice(0, 3).map((deadline) => (
                    <li key={deadline} className="schedule-conflict-item">{cleanRichText(deadline)}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        {!isTimelineView ? (
          <div className={reportGridClass}>
            <section className={`schedule-weekly-report-card${isOverview ? ' schedule-weekly-report-card-overview' : ''}`}>
              <div className="schedule-weekly-report-card-header">
                <span className="schedule-weekly-report-label">Open tasks</span>
                <h4>{isOverview ? 'Priority items to protect' : 'What needs time in this period'}</h4>
              </div>
              {weeklyOpenTasks.length > 0 ? (
                <ul className="schedule-weekly-report-list">
                  {weeklyOpenTasks.slice(0, reportItemLimit).map((block) => (
                    <li key={`${block.title}-${block.starts_at || block.day_label || block.kind}`} className="schedule-weekly-report-item">
                      <strong>{block.title}</strong>
                      {!isOverview ? <span>{block.reason}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="schedule-weekly-report-empty">No open tasks were scheduled from current signals.</p>
              )}
            </section>

            <section className={`schedule-weekly-report-card${isOverview ? ' schedule-weekly-report-card-overview' : ''}`}>
              <div className="schedule-weekly-report-card-header">
                <span className="schedule-weekly-report-label">Meetings</span>
                <h4>{isOverview ? 'Grouped by day' : 'Calendar commitments'}</h4>
              </div>
              {rawMeetings.length > 0 ? (
                isOverview ? (
                  <div className="schedule-weekly-report-summary-stack">
                    {overviewMeetingSummaries.length > 0 ? (
                      overviewMeetingSummaries.map((meeting) => (
                        <article key={`${meeting.label}-${meeting.meta}`} className="schedule-weekly-report-summary">
                          <div className="schedule-weekly-report-summary-head">
                            <span className="schedule-weekly-report-summary-label">{meeting.label}</span>
                            <span className="schedule-weekly-report-summary-meta">{meeting.meta}</span>
                          </div>
                          <p className="schedule-weekly-report-summary-text">{meeting.text}</p>
                        </article>
                      ))
                    ) : (
                      <p className="schedule-weekly-report-empty">No meetings were placed in the current planning window.</p>
                    )}
                  </div>
                ) : (
                  <ul className="schedule-weekly-report-list">
                    {rawMeetings.slice(0, reportItemLimit).map((meeting) => (
                      <li key={`${meeting.title}-${meeting.starts_at || meeting.ends_at || 'meeting'}`} className="schedule-weekly-report-item">
                        <strong>{meeting.title}</strong>
                        <span>{fmtTimeRange(meeting.starts_at, meeting.ends_at, meeting.starts_at)}</span>
                      </li>
                    ))}
                  </ul>
                )
              ) : (
                <p className="schedule-weekly-report-empty">No meetings were placed in the current planning window.</p>
              )}
            </section>

            <section className={`schedule-weekly-report-card${isOverview ? ' schedule-weekly-report-card-overview' : ''}`}>
              <div className="schedule-weekly-report-card-header">
                <span className="schedule-weekly-report-label">{copy.watchLabel}</span>
                <h4>{isOverview ? 'Signals to watch' : 'Deadlines and commitments'}</h4>
              </div>
              {deadlines.length > 0 ? (
                isOverview ? (
                  <div className="schedule-weekly-report-summary-stack">
                    {overviewDeadlineSummaries.length > 0 ? (
                      overviewDeadlineSummaries.map((deadline) => (
                        <article key={`${deadline.label}-${deadline.meta}-${deadline.text}`} className="schedule-weekly-report-summary">
                          <div className="schedule-weekly-report-summary-head">
                            <span className="schedule-weekly-report-summary-label">{deadline.label}</span>
                            <span className="schedule-weekly-report-summary-meta">{deadline.meta}</span>
                          </div>
                          <p className="schedule-weekly-report-summary-text">{cleanRichText(deadline.text)}</p>
                        </article>
                      ))
                    ) : (
                      <p className="schedule-weekly-report-empty">No deadline signals were available for this period.</p>
                    )}
                  </div>
                ) : (
                  <ul className="schedule-weekly-report-list">
                    {deadlines.slice(0, reportItemLimit).map((deadline) => (
                      <li key={deadline} className="schedule-weekly-report-item">
                        <strong>{cleanRichText(deadline)}</strong>
                      </li>
                    ))}
                  </ul>
                )
              ) : (
                <p className="schedule-weekly-report-empty">No deadline signals were available for this period.</p>
              )}
            </section>

            <section className={`schedule-weekly-report-card${isOverview ? ' schedule-weekly-report-card-overview' : ''}`}>
              <div className="schedule-weekly-report-card-header">
                <span className="schedule-weekly-report-label">{copy.actionLabel}</span>
                <h4>Recommended next steps</h4>
              </div>
              {followUps.length > 0 ? (
                <ol className="schedule-weekly-report-list schedule-weekly-report-list-numbered">
                  {followUps.slice(0, reportItemLimit).map((item) => (
                    <li key={item} className="schedule-weekly-report-item">
                      {isOverview ? cleanRichText(item) : <>{cleanRichText(item)}</>}
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="schedule-weekly-report-empty">No next-step actions were generated.</p>
              )}
            </section>
          </div>
        ) : null}

        {/* ── Window metadata (multi-day header) ─────────── */}
        {window && isTimelineView && isMultiDay ? (
          <div className="schedule-calendar-header">
            {window.horizon ? (
              <span className="schedule-calendar-horizon">{window.horizon.replace(/_/g, ' ')}</span>
            ) : null}
            <div className="schedule-calendar-meta">
              {window.timezone ? <span className="schedule-calendar-chip">{window.timezone}</span> : null}
              {window.start_date && window.end_date ? (
                <span className="schedule-calendar-chip">
                  {new Date(window.start_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                  {' – '}
                  {new Date(window.end_date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                </span>
              ) : null}
            </div>
          </div>
        ) : null}

        {/* ── Multi-day grid ──────────────────────────────── */}
        {allSlots.length > 0 && isTimelineView ? (
          isMultiDay ? (
            <div
              className="schedule-cal-grid"
              style={{ gridTemplateColumns: `repeat(${Math.min(dayEntries.length, 5)}, minmax(0, 1fr))` }}
            >
              {dayEntries.map(([day, daySlots]) => (
                <div key={day} className="schedule-cal-col">
                  <div className="schedule-cal-col-head">
                    <span className="schedule-cal-day-name">{day}</span>
                    {daySlots.length > 1 ? (
                      <span className="schedule-cal-day-count">{daySlots.length}</span>
                    ) : null}
                  </div>
                  <div className="schedule-cal-col-body">
                    {daySlots.map((slot, i) => (
                      <SlotCard
                        key={`${slot.title}-${slot.starts_at || i}`}
                        slot={slot}
                        index={i}
                        compact
                        onInlineAction={onInlineAction}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* ── Single-day: full day planner view ─────── */
            <div className="sched-day-view">
              {dayEntries.map(([day, daySlots]) => {
                const firstSlot = daySlots[0]
                return (
                  <div key={day} className="sched-day-section">
                    <DayHeader
                      dayLabel={firstSlot?.day_label || day}
                      startsAt={firstSlot?.starts_at}
                      totalSlots={daySlots.length}
                    />
                    <div className="sched-slot-list">
                      {daySlots.map((slot, i) => (
                        <SlotCard
                          key={`${slot.title}-${slot.starts_at || i}`}
                          slot={slot}
                          index={i}
                          onInlineAction={onInlineAction}
                        />
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          )
        ) : (
          <p className="schedule-renderer-empty">{copy.emptyState}</p>
        )}

        {/* ── Deadlines / follow-ups (no longer shows meetings) ─ */}
        {followUps.length > 0 && isTimelineView ? (
          <div className="schedule-renderer-support">
            <div className="schedule-renderer-support-group">
              <span className="schedule-renderer-support-title">{copy.actionLabel}</span>
              <ol className="schedule-renderer-support-list schedule-renderer-plain-list">
                {followUps.map((f) => <li key={f}>{cleanRichText(f)}</li>)}
              </ol>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
