import { useMemo } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import TaskSequence, { type TaskPhase } from './TaskSequence'
import type { Message, StreamingPhase } from '../lib/sessions'

interface FlowDashboardProps {
  /** The currently streaming message (or last assistant message) */
  activeMessage: Message | null
  /** Whether follow mode is enabled */
  followMode: boolean
  /** Toggle follow mode */
  onToggleFollowMode: () => void
  /** Current pending tool label */
  toolLabel?: string
}

/**
 * Derive RPI-style task phases from SSE streaming state.
 * Maps streamingPhase → Research/Plan/Execute/Complete phases.
 * Exported so ControlPlane can use it for inline step strips.
 */
export function derivePhases(msg: Message | null, toolLabel?: string): TaskPhase[] {
  if (!msg || msg.role !== 'assistant') return []

  const phase = msg.streamingPhase as StreamingPhase
  const hasThinking = !!msg.thinking
  const hasText = !!msg.text
  const hasPlots = (msg.plots?.length ?? 0) > 0
  const hasTool = phase === 'tool' || !!toolLabel
  const isDone = phase === 'done' || phase === null || phase === undefined

  const phases: TaskPhase[] = []

  // Phase 1: Research (thinking/reasoning)
  phases.push({
    id: 'research',
    label: 'Research',
    description: 'Analyzing request and retrieving context',
    icon: 'research',
    status: isDone || hasText || hasTool
      ? 'completed'
      : phase === 'thinking'
        ? 'active'
        : 'pending',
    detail: phase === 'thinking'
      ? 'Reasoning through the problem...'
      : hasThinking
        ? 'Context analyzed'
        : 'Analyzed request',
  })

  // Phase 2: Plan (text generation — LLM outputs structured plan)
  phases.push({
    id: 'plan',
    label: 'Plan',
    description: 'Formulating response and execution plan',
    icon: 'plan',
    status: isDone || hasTool
      ? 'completed'
      : phase === 'text'
        ? 'active'
        : hasText
          ? 'completed'
          : 'pending',
    detail: phase === 'text'
      ? 'Generating response...'
      : hasText
        ? 'Plan formulated'
        : 'Waiting for research',
  })

  // Phase 3: Execute (tool execution — run_matlab, project_gen, etc.)
  const actionLabel = toolLabel || (msg.skill === 'run_matlab' ? 'MATLAB' : msg.skill || '')
  if (hasTool || (isDone && msg.skill && msg.skill !== 'chat')) {
    phases.push({
      id: 'execute',
      label: 'Execute',
      description: `Running ${actionLabel}`,
      icon: 'execute',
      status: isDone
        ? (msg.skill ? 'completed' : 'pending')
        : phase === 'tool'
          ? 'active'
          : 'pending',
      detail: phase === 'tool'
        ? toolLabel || 'Executing...'
        : isDone && msg.skill
          ? `${actionLabel} completed`
          : undefined,
      elapsed_ms: isDone ? msg.elapsed_ms : undefined,
    })
  }

  // Phase 4: Complete
  phases.push({
    id: 'complete',
    label: 'Complete',
    description: 'Task finished',
    icon: 'done',
    status: isDone ? 'completed' : 'pending',
    detail: isDone
      ? `${hasPlots ? 'Results with plots' : 'Response delivered'}${msg.elapsed_ms ? ` (${msg.elapsed_ms}ms)` : ''}`
      : 'Waiting...',
    elapsed_ms: isDone ? msg.elapsed_ms : undefined,
  })

  return phases
}

export default function FlowDashboard({
  activeMessage,
  followMode,
  onToggleFollowMode,
  toolLabel,
}: FlowDashboardProps) {
  const phases = useMemo(
    () => derivePhases(activeMessage, toolLabel),
    [activeMessage, toolLabel]
  )

  const isActive = activeMessage?.streamingPhase != null
    && activeMessage.streamingPhase !== 'done'

  if (phases.length === 0) return null

  return (
    <div className="space-y-3">
      {/* Follow Mode toggle */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
          {isActive ? 'Agent working...' : 'Last task'}
        </span>
        <button
          onClick={onToggleFollowMode}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all duration-200"
          style={{
            backgroundColor: followMode ? 'var(--accent-subtle)' : 'var(--bg-hover)',
            color: followMode ? 'var(--accent)' : 'var(--text-muted)',
            border: followMode ? '1px solid var(--accent)' : '1px solid var(--border-subtle)',
          }}
          title={followMode ? 'Follow Mode: ON — view auto-switches to active tool' : 'Follow Mode: OFF — stay on current view'}
        >
          {followMode ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
          Follow Mode
        </button>
      </div>

      {/* Task sequence */}
      <TaskSequence
        phases={phases}
        taskTitle={activeMessage?.text?.slice(0, 60) || undefined}
      />
    </div>
  )
}
