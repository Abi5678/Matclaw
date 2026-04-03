import { useRef, useCallback } from 'react'
import type { ProjectFile } from '../lib/sessions'

const API = 'http://localhost:8000'

export interface SentryUpdate {
  type: 'start' | 'issue' | 'done'
  attempt?: number
  max?: number
  message: string
  issues?: string[]
  quality?: 'good' | 'poor'
}

export interface AgentStep {
  step: number
  tool: string
  inputs: Record<string, unknown>
  label: string
}

export interface AgentResult {
  step: number
  tool: string
  success: boolean
  output: string
  plots: string[]
}

export interface DoctorEvent {
  type: 'start' | 'issue' | 'fix' | 'rerun' | 'done'
  round?: number
  issueType?: string
  severity?: string
  description?: string
  message?: string
  fixed?: boolean
  roundsTaken?: number
  plotStd?: number
}

export interface StreamCallbacks {
  onThinking: (token: string) => void
  onText: (token: string) => void
  onToolStart: (data: { action: string; label: string }) => void
  onToolResult: (data: { output: string; plots: string[]; files: ProjectFile[]; code?: string }) => void
  onDone: (data: { skill: string; elapsed_ms: number; reply: string; plots: string[]; files: ProjectFile[] }) => void
  onError: (msg: string) => void
  onSentryUpdate?: (data: SentryUpdate) => void
  onAgentStep?: (data: AgentStep) => void
  onAgentResult?: (data: AgentResult) => void
  onAgentThought?: (step: number, text: string) => void
  onDoctorEvent?: (data: DoctorEvent) => void
}

export function useStreaming() {
  const abortRef = useRef<AbortController | null>(null)

  const streamRun = useCallback(async (
    text: string,
    sessionId: string,
    history: { role: string; text: string }[],
    callbacks: StreamCallbacks,
    mode: string = 'auto',
    sentryMode: boolean = false,
    doctorMode: boolean = true,
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
        body: JSON.stringify({ text, session_id: sessionId, history, mode, sentry_mode: sentryMode, doctor_mode: doctorMode }),
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
                case 'sentry_start':
                  callbacks.onSentryUpdate?.({ type: 'start', message: data.message }); break
                case 'sentry_issue':
                  callbacks.onSentryUpdate?.({
                    type: 'issue', attempt: data.attempt, max: data.max_attempts,
                    issues: data.issues, message: data.message,
                  }); break
                case 'sentry_done':
                  callbacks.onSentryUpdate?.({
                    type: 'done', quality: data.quality, message: data.message,
                  }); break
                // Agentic loop events
                case 'agent_thought':
                  callbacks.onAgentThought?.(data.step, data.text); break
                case 'agent_step':
                  callbacks.onAgentStep?.(data); break
                case 'agent_result':
                  callbacks.onAgentResult?.(data); break
                // CodeDoctor events
                case 'doctor_start':
                  callbacks.onDoctorEvent?.({ type: 'start', message: data.message }); break
                case 'doctor_issue':
                  callbacks.onDoctorEvent?.({
                    type: 'issue', round: data.round,
                    issueType: data.type, severity: data.severity,
                    description: data.description,
                  }); break
                case 'doctor_fix':
                  callbacks.onDoctorEvent?.({
                    type: 'fix', round: data.round, description: data.description,
                  }); break
                case 'doctor_rerun':
                  callbacks.onDoctorEvent?.({
                    type: 'rerun', round: data.round, message: data.message,
                  }); break
                case 'doctor_done':
                  callbacks.onDoctorEvent?.({
                    type: 'done', fixed: data.fixed, message: data.message,
                    roundsTaken: data.rounds_taken, plotStd: data.plot_std,
                  }); break
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
