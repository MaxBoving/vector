import React from 'react'

type ConfidenceBandProps = {
  confidence: 'low' | 'medium' | 'high'
  label: string
}

export const ConfidenceBand: React.FC<ConfidenceBandProps> = ({ confidence, label }) => (
  <div className={`confidence-band confidence-band-${confidence}`}>
    <span className="confidence-band-label">Confidence</span>
    <p>{label}</p>
  </div>
)
