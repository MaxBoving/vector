import React, { useState } from 'react'
import { ChevronRight } from '../dashboard/icons'

// ── Gap resolution map ────────────────────────────────────────
// Maps the tool label prefix from missing_context entries to
// a human-readable description and optional connect action.

type GapDef = {
  what: string
  how: string
  connectValue?: string
}

const GAP_DEFS: Record<string, GapDef> = {
  'email:not_connected': {
    what: 'Your inbox',
    how: 'Connect Gmail or Outlook to include email context',
    connectValue: 'gmail',
  },
  'calendar:not_connected': {
    what: 'Your calendar',
    how: 'Connect Google Calendar or Outlook Calendar to include your schedule',
    connectValue: 'google_calendar',
  },
  'CRM / pipeline:not_connected': {
    what: 'Pipeline data',
    how: 'Connect HubSpot or Salesforce to include deal and contact context',
    connectValue: 'hubspot',
  },
  'Slack:not_connected': {
    what: 'Slack',
    how: 'Connect Slack to include channel and message context',
    connectValue: 'slack',
  },
  'Google Drive:not_connected': {
    what: 'Google Drive',
    how: 'Connect Google Drive to search documents and files',
    connectValue: 'google_drive',
  },
  'signals:not_connected': {
    what: 'Recent signals',
    how: 'Signals data unavailable — check your integration status',
  },
  'signals:error': {
    what: 'Recent signals',
    how: 'Signals feed returned an error — check your integration status',
  },
  'company state:not_connected': {
    what: 'Company profile',
    how: 'Add company profile data to improve financial and strategic answers',
  },
  'entity context:not_connected': {
    what: 'Entity details',
    how: 'Add documents or notes about this person or company to improve context',
  },
  'Inbox context is thin.': {
    what: 'Your inbox',
    how: 'Only a few actionable threads were available, so the answer leans on a thinner inbox signal',
  },
  'Calendar context is thin.': {
    what: 'Your calendar',
    how: 'Only a few calendar signals were available, so the schedule view is lighter',
  },
  'Supporting context is narrow.': {
    what: 'Supporting context',
    how: 'Only a narrow set of sources was available for this answer',
  },
  'Planning candidates need more structure before they can be placed cleanly.': {
    what: 'Planning candidates',
    how: 'The schedule inputs need a bit more structure before they can be placed cleanly',
  },
  'Top inbox evidence is suppressed or promotional.': {
    what: 'Top inbox evidence',
    how: 'The most visible inbox evidence was suppressed or promotional, so it was down-weighted',
  },
  'Compound planning evidence is only partially assembled.': {
    what: 'Compound planning evidence',
    how: 'The planner assembled some, but not all, of the supporting steps',
  },
  'Weekly evidence was thin, so the plan used a lighter guidance path.': {
    what: 'Planning guidance',
    how: 'The weekly evidence set was thin, so the plan used a lighter guidance path',
  },
}

const FALLBACK_GAP = (entry: string): GapDef => ({
  what: 'Context gap',
  how: entry.includes('not_connected')
    ? 'Connect the relevant source to include this context.'
    : 'Additional supporting context is needed to sharpen this answer.',
})

// ── Component ─────────────────────────────────────────────────

type Props = {
  missingContext: string[]
  onIntegrationConnect?: (value: string) => void
}

export const AccuracyGapsPanel: React.FC<Props> = ({ missingContext, onIntegrationConnect }) => {
  const [open, setOpen] = useState(false)

  const gaps = missingContext
    .map((entry) => ({ entry, def: GAP_DEFS[entry] ?? FALLBACK_GAP(entry) }))
    .filter(({ def }) => def.what)

  if (!gaps.length) return null

  return (
    <div className="accuracy-gaps-panel">
      <button
        type="button"
        className="accuracy-gaps-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="accuracy-gaps-label">
          How to make this more accurate
        </span>
        <span className="accuracy-gaps-count">{gaps.length} gap{gaps.length === 1 ? '' : 's'}</span>
        <ChevronRight size={13} className={`accuracy-gaps-chevron ${open ? 'accuracy-gaps-chevron-open' : ''}`} />
      </button>

      {open ? (
        <ul className="accuracy-gaps-list">
          {gaps.map(({ entry, def }) => (
            <li key={entry} className="accuracy-gaps-item">
              <span className="accuracy-gaps-what">{def.what}</span>
              <span className="accuracy-gaps-how">{def.how}</span>
              {def.connectValue && onIntegrationConnect ? (
                <button
                  type="button"
                  className="accuracy-gaps-connect"
                  onClick={() => onIntegrationConnect(def.connectValue!)}
                >
                  Connect
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
