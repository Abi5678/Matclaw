import { useRef, useCallback } from 'react'
import type { ProjectFile } from '../lib/sessions'

const API = 'http://localhost:8000'

export interface StreamCallbacks {
  onThinking: (token: string) => void
  onText: (token: string) => void
  onToolStart: (data: { action: string; label: string }) => void
  onToolResult: (data: { output: string; plots: string[]; files: ProjectFile[] }) => void
  onDone: (data: { skill: string; elapsed_ms: number; reply: string; plots: string[]; files: ProjectFile[] }) => void
  onError: (msg: string) => void
}

export function useStreaming() {
  const abortRef = useRef<AbortController | null>(null)

  const streamRun = useCallback(async (
    text: string,
    sessionId: string,
    history: { role: string; text: string }[],
    callbacks: StreamCallbacks,
    mode: string = 'auto',
  ) => {
    const controller = new AbortController()
    abortRef.current = controller

    // 90-second timeout guard — prevents infinite "Thinking..." if server hangs
    const timeoutId = setTimeout(() => {
      controller.abort()
      callbacks.onError('Request timed out after 90 seconds. The server may be busy.')
    }, 90_000)

    try {
      const response = await fetch(`${API}/api/run/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, session_id: sessionId, history, mode }),
        signal: controller.signal,
      })

      if (!response.ok || !response.body) {
        clearTimeout(timeoutId)
        callbacks.onError(`Server error: ${response.status}`)
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // Parse SSE lines from buffer
        const parts = buffer.split('\n')
        buffer = parts.pop()! // keep incomplete line

        let currentEvent = ''
        for (const line of parts) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ') && currentEvent) {
            try {
              const data = JSON.parse(line.slice(6))
              switch (currentEvent) {
                case 'thinking': callbacks.onThinking(data.token); break
                case 'text': callbacks.onText(data.token); break
                case 'tool_start': callbacks.onToolStart(data); break
                case 'tool_result': callbacks.onToolResult(data); break
                case 'done': callbacks.onDone(data); break
                case 'error': callbacks.onError(data.message); break
              }
            } catch {
              // skip malformed JSON
            }
            currentEvent = ''
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        callbacks.onError(`Stream failed: ${err}`)
      }
    }
  }, [])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  return { streamRun, cancel }
}
