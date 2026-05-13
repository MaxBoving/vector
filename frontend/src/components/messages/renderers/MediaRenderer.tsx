import React from 'react'

import { ReportLayout } from './ReportLayout'
import type { MessageRendererProps } from './types'

export const MediaRenderer: React.FC<MessageRendererProps> = (props) => (
  <div className="mode-renderer-shell mode-renderer-media">
    <ReportLayout {...props} />
  </div>
)
