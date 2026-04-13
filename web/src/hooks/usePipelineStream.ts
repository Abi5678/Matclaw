import { useState, useRef, useCallback } from 'react'
import type { NodeStatus } from '../lib/pipeline'
import { cancelRun } from '../lib/pipeline'

import { API } from '../lib/constants'

export interface PipelineStreamState {
  nodeStatuses: Record<string, NodeStatus>
  isRunning: boolean
  runId: string | null
  error: string | null
  warnings: Record<string, string>
  totalNodes: number
  completedNodes: number
}

export function usePipelineStream() {
  const [state, setState] = useState<PipelineStreamState>({
    nodeStatuses: {},
    isRunning: false,
    runId: null,
    error: null,
    warnings: {},
    totalNodes: 0,
    completedNodes: 0,
  })

  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const reset = useCallback(() => {
    setState({
      nodeStatuses: {},
      isRunning: false,
      runId: null,
      error: null,
      warnings: {},
      totalNodes: 0,
      completedNodes: 0,
    })
  }, [])

  const handleEvent = useCallback((eventType: string, data: Record<string, unknown>) => {
    switch (eventType) {
      case 'pipeline_start':
        setState(s => ({ ...s, totalNodes: (data.total_nodes as number) || 0 }))
        break

      case 'node_start':
        setState(s => ({
          ...s,
          nodeStatuses: {
            ...s.nodeStatuses,
            [data.node_id as string]: { status: 'running', startedAt: Date.now() },
          },
        }))
        break

      case 'node_complete':
        setState(s => ({
          ...s,
          completedNodes: s.completedNodes + 1,
          nodeStatuses: {
            ...s.nodeStatuses,
            [data.node_id as string]: {
              status: 'completed',
              output: data.output as string | undefined,
              plots: data.plots as string[] | undefined,
              finishedAt: Date.now(),
            },
          },
        }))
        break

      case 'node_failed':
        setState(s => ({
          ...s,
          nodeStatuses: {
            ...s.nodeStatuses,
            [data.node_id as string]: {
              status: 'failed',
              error: data.error as string | undefined,
              finishedAt: Date.now(),
            },
          },
        }))
        break

      case 'node_warning':
        setState(s => ({
          ...s,
          warnings: {
            ...s.warnings,
            [data.node_id as string]: data.warning as string,
          },
        }))
        break

      case 'pipeline_complete':
        setState(s => ({ ...s, isRunning: false }))
        break

      case 'pipeline_error':
        setState(s => ({ ...s, isRunning: false, error: data.error as string }))
        break
    }
  }, [])

  const startStream = useCallback(async (runId: string) => {
    reset()
    setState(s => ({ ...s, isRunning: true, runId }))

    abortRef.current = new AbortController()
    const decoder = new TextDecoder()

    try {
      const res = await fetch(`${API}/api/pipelines/runs/${runId}/stream`, {
        signal: abortRef.current.signal,
      })
      if (!res.body) throw new Error('No response body')

      readerRef.current = res.body.getReader()
      let buffer = ''

      while (true) {
        const { done, value } = await readerRef.current.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // SSE format: "event: <type>\ndata: <json>\n\n"
        // Split on double newline to get complete events
        const events = buffer.split('\n\n')
        buffer = events.pop() ?? '' // keep incomplete trailing chunk

        for (const eventBlock of events) {
          const lines = eventBlock.split('\n')
          let eventType = ''
          let dataLine = ''

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              eventType = line.slice(7).trim()
            } else if (line.startsWith('data: ')) {
              dataLine = line.slice(6).trim()
            }
          }

          if (eventType && dataLine) {
            try {
              const data = JSON.parse(dataLine)
              handleEvent(eventType, data)
            } catch {
              // ignore malformed JSON
            }
          }
        }
      }
    } catch (err: unknown) {
      if ((err as Error).name !== 'AbortError') {
        setState(s => ({ ...s, error: (err as Error).message, isRunning: false }))
      }
    } finally {
      setState(s => ({ ...s, isRunning: false }))
    }
  }, [reset, handleEvent])

  const cancel = useCallback(async () => {
    abortRef.current?.abort()
    readerRef.current?.cancel()
    const runId = state.runId
    if (runId) {
      await cancelRun(runId).catch(() => {})
    }
    setState(s => ({ ...s, isRunning: false }))
  }, [state.runId])

  return { ...state, startStream, cancel, reset }
}
