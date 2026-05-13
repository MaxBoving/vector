import { useState } from 'react'

export type InlineActionStatus = 'idle' | 'loading' | 'done'

export type InlineActionState = {
  status: InlineActionStatus
  activeIntent: string | null
  result: string | null
  trigger: (intent: string, prompt: string) => Promise<void>
  dismiss: () => void
}

export function useInlineAction(
  onInlineAction?: (prompt: string, intent: string) => Promise<string>,
): InlineActionState {
  const [status, setStatus] = useState<InlineActionStatus>('idle')
  const [activeIntent, setActiveIntent] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  const trigger = async (intent: string, prompt: string) => {
    if (!onInlineAction || status === 'loading') return
    setStatus('loading')
    setActiveIntent(intent)
    setResult(null)
    try {
      const text = await onInlineAction(prompt, intent)
      setResult(text)
      setStatus('done')
    } catch {
      setResult('Something went wrong. Try again.')
      setStatus('done')
    }
  }

  const dismiss = () => {
    setResult(null)
    setStatus('idle')
    setActiveIntent(null)
  }

  return { status, activeIntent, result, trigger, dismiss }
}
