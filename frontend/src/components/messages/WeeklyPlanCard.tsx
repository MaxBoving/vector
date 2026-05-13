import React from 'react'

import { getWeeklyPlanPresentation } from './messagePresentation'
import type { AssistantMessage, RichRenderer } from './types'

type WeeklyPlanCardProps = {
  message: AssistantMessage
  renderRichContent: RichRenderer
}

const formatDateTime = (value?: string | null): { day: string; time: string } | null => {
  if (!value) {
    return null
  }

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return null
  }

  return {
    day: parsed.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }),
    time: parsed.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }),
  }
}

const formatTimeRange = (startsAt?: string | null, endsAt?: string | null, fallback?: string | null) => {
  const start = formatDateTime(startsAt)
  const end = formatDateTime(endsAt)
  if (start && end) {
    return `${start.time}-${end.time}`
  }
  if (start) {
    return start.time
  }
  return fallback || 'Open block'
}

const blockToneClass = (kind?: string | null) => {
  if (kind === 'meeting_prep') {
    return 'schedule-block-meeting'
  }
  if (kind === 'deadline') {
    return 'schedule-block-deadline'
  }
  if (kind === 'follow_up') {
    return 'schedule-block-follow-up'
  }
  if (kind === 'document_prep') {
    return 'schedule-block-document'
  }
  return 'schedule-block-focus'
}

export const WeeklyPlanCard: React.FC<WeeklyPlanCardProps> = ({ message, renderRichContent }) => {
  const weeklyPlan = getWeeklyPlanPresentation(message)
  const blocks = [...(weeklyPlan?.blocks || [])].sort((left, right) => {
    const leftTime = left.starts_at ? new Date(left.starts_at).getTime() : Number.MAX_SAFE_INTEGER
    const rightTime = right.starts_at ? new Date(right.starts_at).getTime() : Number.MAX_SAFE_INTEGER
    return leftTime - rightTime
  })
  const groups = blocks.reduce<Record<string, typeof blocks>>((accumulator, block) => {
    const key = block.day_label || formatDateTime(block.starts_at)?.day || 'Schedule'
    accumulator[key] = accumulator[key] || []
    accumulator[key].push(block)
    return accumulator
  }, {})
  const hasStructuredTimeline = blocks.length > 0

  return (
    <div className="weekly-plan-card">
      <section className="executive-block executive-block-highlight">
        <div className="executive-block-header">
          <span className="eyebrow">Schedule Summary</span>
          <h4>Planned time blocks</h4>
        </div>
        <div className="executive-summary executive-summary-compact">
          {renderRichContent(message.presentation?.summary || message.answer.summary) || <p>{message.presentation?.summary || message.answer.summary}</p>}
        </div>
        {weeklyPlan?.planning_window ? (
          <div className="schedule-window-meta">
            <span>{weeklyPlan.planning_window.horizon?.replace(/_/g, ' ') || 'schedule'}</span>
            {weeklyPlan.planning_window.timezone ? <span>{weeklyPlan.planning_window.timezone}</span> : null}
            {weeklyPlan.planning_window.workday_start && weeklyPlan.planning_window.workday_end ? (
              <span>
                {weeklyPlan.planning_window.workday_start}-{weeklyPlan.planning_window.workday_end}
              </span>
            ) : null}
          </div>
        ) : null}
      </section>

      {hasStructuredTimeline ? (
        <section className="executive-block">
          <div className="executive-block-header">
            <span className="eyebrow">Timeline</span>
            <h4>Calendar-style schedule</h4>
          </div>
          <div className="schedule-day-stack">
            {Object.entries(groups).map(([day, dayBlocks]) => (
              <section key={day} className="schedule-day-card">
                <div className="schedule-day-header">
                  <span className="eyebrow">Day</span>
                  <h5>{day}</h5>
                </div>
                <div className="schedule-block-stack">
                  {dayBlocks.map((block, index) => (
                    <article key={`${block.title}-${block.starts_at || index}`} className={`schedule-block-card ${blockToneClass(block.kind)}`}>
                      <div className="schedule-block-time">
                        <span>{formatTimeRange(block.starts_at, block.ends_at, block.time_window)}</span>
                      </div>
                      <div className="schedule-block-copy">
                        <strong>{block.title}</strong>
                        {block.reason ? <p>{block.reason}</p> : null}
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </section>
      ) : (
        <section className="executive-block">
          <div className="executive-block-header">
            <span className="eyebrow">Timeline</span>
            <h4>Schedule still forming</h4>
          </div>
          <p className="executive-section-copy">{message.answer.summary}</p>
        </section>
      )}

      <div className="schedule-support-grid">
        {weeklyPlan?.meetings && weeklyPlan.meetings.length > 0 ? (
          <section className="weekly-plan-column">
            <div className="executive-block-header">
              <span className="eyebrow">Meetings</span>
              <h4>Calendar commitments</h4>
            </div>
            <div className="schedule-support-stack">
              {weeklyPlan.meetings.map((meeting) => (
                <div key={`${meeting.title}-${meeting.starts_at || meeting.ends_at || 'meeting'}`} className="schedule-support-card">
                  <strong>{meeting.title}</strong>
                  <span>{formatTimeRange(meeting.starts_at, meeting.ends_at, meeting.starts_at)}</span>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {weeklyPlan?.deadlines && weeklyPlan.deadlines.length > 0 ? (
          <section className="weekly-plan-column">
            <div className="executive-block-header">
              <span className="eyebrow">Deadlines</span>
              <h4>What cannot slip</h4>
            </div>
            <ul className="executive-bullet-list">
              {weeklyPlan.deadlines.map((deadline) => (
                <li key={deadline}>{deadline}</li>
              ))}
            </ul>
          </section>
        ) : null}

        {weeklyPlan?.follow_ups && weeklyPlan.follow_ups.length > 0 ? (
          <section className="weekly-plan-column">
            <div className="executive-block-header">
              <span className="eyebrow">Follow-Ups</span>
              <h4>Recommended next actions</h4>
            </div>
            <ol className="executive-bullet-list executive-bullet-list-numbered">
              {weeklyPlan.follow_ups.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ol>
          </section>
        ) : null}
      </div>
    </div>
  )
}
