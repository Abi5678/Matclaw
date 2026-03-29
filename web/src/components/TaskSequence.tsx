import { motion } from 'framer-motion'
import { CheckCircle2, Loader2, Circle, AlertCircle } from 'lucide-react'

export type PhaseStatus = 'pending' | 'active' | 'completed' | 'error'

export interface TaskPhase {
  id: string
  label: string
  description: string
  status: PhaseStatus
  icon: 'research' | 'plan' | 'execute' | 'done'
  detail?: string       // e.g. "Running MATLAB code..." or "Querying memory..."
  elapsed_ms?: number
}

interface TaskSequenceProps {
  phases: TaskPhase[]
  taskTitle?: string
}

// (PHASE_ICONS removed as it was unused in the sub-component)

export default function TaskSequence({ phases, taskTitle }: TaskSequenceProps) {
  if (phases.length === 0) return null

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{
        backgroundColor: 'var(--card-bg)',
        border: '1px solid var(--card-border)',
      }}
    >
      {/* Header */}
      <div
        className="px-4 py-2.5 flex items-center gap-2"
        style={{ borderBottom: '1px solid var(--border-subtle)' }}
      >
        <div className="w-2 h-2 rounded-full" style={{ backgroundColor: 'var(--accent)' }} />
        <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
          Task Sequence
        </span>
        {taskTitle && (
          <span className="text-xs truncate ml-2" style={{ color: 'var(--text-secondary)' }}>
            — {taskTitle}
          </span>
        )}
      </div>

      {/* Phase timeline */}
      <div className="px-4 py-3 space-y-0">
        {phases.map((phase, i) => {
          const isLast = i === phases.length - 1

          return (
            <div key={phase.id} className="flex gap-3">
              {/* Timeline column */}
              <div className="flex flex-col items-center w-6 flex-shrink-0">
                <PhaseIndicator status={phase.status} />
                {!isLast && (
                  <div
                    className="w-px flex-1 min-h-[16px]"
                    style={{
                      backgroundColor: phase.status === 'completed'
                        ? 'var(--accent)'
                        : 'var(--border-subtle)',
                    }}
                  />
                )}
              </div>

              {/* Content */}
              <div className={`flex-1 pb-3 ${isLast ? 'pb-0' : ''}`}>
                <div className="flex items-center gap-2">
                  <span
                    className="text-sm font-medium"
                    style={{
                      color: phase.status === 'active' ? 'var(--accent)'
                        : phase.status === 'completed' ? 'var(--text-primary)'
                        : phase.status === 'error' ? 'var(--error)'
                        : 'var(--text-muted)',
                    }}
                  >
                    {phase.label}
                  </span>
                  {phase.elapsed_ms != null && phase.status === 'completed' && (
                    <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      {phase.elapsed_ms}ms
                    </span>
                  )}
                </div>
                <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                  {phase.detail || phase.description}
                </p>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function PhaseIndicator({ status }: { status: PhaseStatus }) {
  if (status === 'active') {
    return (
      <motion.div
        className="w-6 h-6 rounded-full flex items-center justify-center"
        style={{
          backgroundColor: 'var(--accent-subtle)',
          border: '2px solid var(--accent)',
        }}
        animate={{ scale: [1, 1.1, 1] }}
        transition={{ repeat: Infinity, duration: 1.5 }}
      >
        <Loader2 className="w-3 h-3 animate-spin" style={{ color: 'var(--accent)' }} />
      </motion.div>
    )
  }

  if (status === 'completed') {
    return (
      <motion.div
        initial={{ scale: 0.8 }}
        animate={{ scale: 1 }}
        className="w-6 h-6 rounded-full flex items-center justify-center"
        style={{ backgroundColor: 'var(--accent)', color: 'var(--text-inverse)' }}
      >
        <CheckCircle2 className="w-3.5 h-3.5" />
      </motion.div>
    )
  }

  if (status === 'error') {
    return (
      <div
        className="w-6 h-6 rounded-full flex items-center justify-center"
        style={{ backgroundColor: 'rgba(239,68,68,0.15)', border: '2px solid var(--error)' }}
      >
        <AlertCircle className="w-3 h-3" style={{ color: 'var(--error)' }} />
      </div>
    )
  }

  // pending
  return (
    <div
      className="w-6 h-6 rounded-full flex items-center justify-center"
      style={{ border: '2px solid var(--border-default)' }}
    >
      <Circle className="w-3 h-3" style={{ color: 'var(--text-muted)' }} />
    </div>
  )
}
