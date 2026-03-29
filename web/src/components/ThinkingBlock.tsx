import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Brain, ChevronDown, ChevronRight } from 'lucide-react'

interface ThinkingBlockProps {
  thinking: string
  isStreaming?: boolean
  defaultCollapsed?: boolean
}

export default function ThinkingBlock({ thinking, isStreaming, defaultCollapsed = false }: ThinkingBlockProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)

  if (!thinking) return null

  return (
    <div
      className="mt-1 mb-2 rounded-lg overflow-hidden"
      style={{
        border: '1px solid var(--border-subtle)',
        backgroundColor: 'var(--bg-hover)',
      }}
    >
      <button
        onClick={() => setCollapsed(c => !c)}
        className="w-full flex items-center gap-2 px-3 py-1.5 transition-colors"
        style={{ color: 'var(--text-muted)' }}
        onMouseEnter={e => e.currentTarget.style.backgroundColor = 'var(--bg-hover)'}
        onMouseLeave={e => e.currentTarget.style.backgroundColor = 'transparent'}
      >
        <Brain className="w-3 h-3 flex-shrink-0" />
        <span className="text-xs flex-1 text-left">
          {isStreaming ? 'Thinking...' : 'Thought process'}
        </span>
        {isStreaming && (
          <motion.span
            className="w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: 'var(--accent)' }}
            animate={{ opacity: [1, 0.3, 1] }}
            transition={{ repeat: Infinity, duration: 1.2 }}
          />
        )}
        {collapsed ? (
          <ChevronRight className="w-3 h-3" />
        ) : (
          <ChevronDown className="w-3 h-3" />
        )}
      </button>
      <AnimatePresence initial={false}>
        {!collapsed && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div
              className="px-3 py-2 text-xs leading-relaxed font-mono whitespace-pre-wrap max-h-48 overflow-y-auto"
              style={{
                color: 'var(--text-muted)',
                borderTop: '1px solid var(--border-subtle)',
              }}
            >
              {thinking}
              {isStreaming && (
                <motion.span
                  className="inline-block w-1.5 h-3 ml-0.5"
                  style={{ backgroundColor: 'var(--accent)', opacity: 0.6 }}
                  animate={{ opacity: [1, 0, 1] }}
                  transition={{ repeat: Infinity, duration: 0.8 }}
                />
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
