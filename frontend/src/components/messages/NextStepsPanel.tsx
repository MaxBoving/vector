import React, { useMemo, useState } from 'react'

import { ArrowRight, ChevronDown } from './icons'
import { cleanRichText } from './messagePresentation'
import type { AssistantMessage, ExecutiveGrouping } from './types'

type NextStepsPanelProps = {
  message: AssistantMessage
  grouping: ExecutiveGrouping
  artifactOnly?: boolean
  onFollowUp?: (text: string) => void
}

const ACTION_LEADING_VERBS = [
  'draft',
  'build',
  'prepare',
  'turn',
  'format',
  'create',
  'send',
  'finalize',
  'review',
  'approve',
  'write',
  'expand',
  'convert',
  'summarize',
  'map',
  'outline',
  'develop',
  'package',
  'schedule',
  'call',
  'delegate',
  'decide',
]

const isActionLike = (value: string) => {
  const text = cleanRichText(value)
  if (!text) return false
  const lowered = text.toLowerCase()
  if (lowered.includes(' due ') || lowered.includes('deadline') || lowered.includes('expires ') || lowered.includes('goes live')) {
    return false
  }
  return ACTION_LEADING_VERBS.some((verb) => lowered.startsWith(`${verb} `))
}

const buildActions = (grouping: ExecutiveGrouping) => {
  const items = grouping.recommendedActions?.sections.flatMap((section) => section.items || []) || []
  const seen = new Set<string>()
  return items
    .map((item) => cleanRichText(item))
    .filter((item) => item && !/^\[Context:/i.test(item) && isActionLike(item))
    .filter((item) => {
      const normalized = item.toLowerCase().replace(/\s+/g, ' ').replace(/[.!?]+$/, '')
      if (seen.has(normalized)) {
        return false
      }
      seen.add(normalized)
      return true
    })
    .slice(0, 3)
}

export const NextStepsPanel: React.FC<NextStepsPanelProps> = ({
  grouping,
  artifactOnly = false,
  onFollowUp,
}) => {
  const [panelOpen, setPanelOpen] = useState(true)
  const actions = useMemo(() => buildActions(grouping), [grouping])
  if (actions.length === 0) {
    return null
  }

  return (
    <section className={`next-steps-panel${artifactOnly ? ' next-steps-panel-artifact' : ''}`}>
      <button
        type="button"
        className="next-steps-header"
        onClick={() => setPanelOpen((current) => !current)}
        aria-expanded={panelOpen}
      >
        <div className="next-steps-header-copy">
          <strong>{artifactOnly ? 'Tighten the deliverable or keep the work moving.' : 'Choose the fastest way to move this forward.'}</strong>
        </div>
        <ChevronDown size={14} className={`mixed-evidence-chevron${panelOpen ? ' mixed-evidence-chevron-open' : ''}`} />
      </button>

      {panelOpen ? (
        <div className="next-steps-body">
          <div className="next-steps-group">
            <div className="followup-chips">
              {actions.map((action) => (
                <button key={action} type="button" className="followup-chip" onClick={() => onFollowUp?.(action)}>
                  {action}
                  <ArrowRight size={12} className="followup-chip-icon" />
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  )
}
