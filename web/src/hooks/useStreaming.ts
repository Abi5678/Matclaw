import { useRef, useCallback, useEffect } from 'react'
import type { ProjectFile } from '../lib/sessions'

import { API } from '../lib/constants'

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
  quality?: Record<string, unknown>
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
  onDone: (data: {
    skill: string
    elapsed_ms: number
    reply: string
    plots: string[]
    files: ProjectFile[]
    execution?: Record<string, unknown>
    budget?: Record<string, unknown>
  }) => void
  onError: (msg: string) => void
  onSentryUpdate?: (data: SentryUpdate) => void
  onAgentStep?: (data: AgentStep) => void
  onAgentResult?: (data: AgentResult) => void
  onAgentThought?: (step: number, text: string) => void
  onDoctorEvent?: (data: DoctorEvent) => void
}

export function useStreaming() {
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => { abortRef.current?.abort() }
  }, [])

  const streamRun = useCallback(async (
    text: string,
    sessionId: string,
    history: { role: string; text: string }[],
    callbacks: StreamCallbacks,
    mode: string = 'auto',
    sentryMode: boolean = false,
    doctorMode: boolean = false,
    forceRuntime?: string | null,
  ) => {
    const controller = new AbortController()
    abortRef.current = controller

    // 5-minute timeout guard — complex MATLAB simulations + physics take time
    const timeoutId = setTimeout(() => {
      controller.abort()
      callbacks.onError('Request timed out after 5 minutes. The simulation may be too complex — try simplifying.')
    }, 300_000)

    let receivedServerEvent = false  // any SSE event received = stream was established
    try {
      const body: Record<string, unknown> = {
        text,
        session_id: sessionId,
        history,
        mode,
        sentry_mode: sentryMode,
        doctor_mode: doctorMode,
      }
      if (forceRuntime) body.force_runtime = forceRuntime

      const response = await fetch(`${API}/api/run/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
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
        if (done) {
          buffer += decoder.decode()
          break
        }
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
                case 'thinking': receivedServerEvent = true; callbacks.onThinking(data.token); break
                case 'text': receivedServerEvent = true; callbacks.onText(data.token); break
                case 'tool_start': receivedServerEvent = true; callbacks.onToolStart(data); break
                case 'tool_result': receivedServerEvent = true; callbacks.onToolResult(data); break
                case 'done': receivedServerEvent = true; callbacks.onDone(data); break
                case 'error': receivedServerEvent = true; callbacks.onError(data.message); break
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

      if (buffer.trim()) {
        const remaining = buffer.split('\n')
        let currentEvent = ''
        for (const line of remaining) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ') && currentEvent) {
            try {
              const data = JSON.parse(line.slice(6))
              if (currentEvent === 'done') { callbacks.onDone(data) }
              else if (currentEvent === 'error') { callbacks.onError(data.message) }
            } catch { /* skip */ }
            currentEvent = ''
          }
        }
      }
    } catch (err) {
      const name = (err as Error).name
      const msg = (err as Error).message ?? ''
      // Suppress AbortError (user cancelled) and Safari's spurious "Load failed" /
      // "Failed to fetch" errors that fire whenever the server closes an SSE stream,
      // including after a clean error event (receivedServerEvent = true).
      const isSafariStreamClose = msg.includes('Load failed') || msg.includes('Failed to fetch')
      if (name !== 'AbortError' && !(isSafariStreamClose && receivedServerEvent)) {
        callbacks.onError(`Stream failed: ${err}`)
      }
    } finally {
      clearTimeout(timeoutId)
      abortRef.current = null
    }
  }, [])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  return { streamRun, cancel }
}
