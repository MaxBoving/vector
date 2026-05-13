import React from 'react'

type InlineResultProps = {
  label: string
  result: string
  onDismiss: () => void
  className?: string
}

export const InlineResult: React.FC<InlineResultProps> = ({ label, result, onDismiss, className }) => (
  <div className={`inline-result-card ${className ?? ''}`}>
    <span className="inline-result-label">{label}</span>
    <p className="inline-result-body">{result}</p>
    <button type="button" className="inline-result-dismiss" onClick={onDismiss}>
      Dismiss
    </button>
  </div>
)
