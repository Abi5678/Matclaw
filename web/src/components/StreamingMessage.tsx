import { motion } from 'framer-motion'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Loader2 } from 'lucide-react'
import ThinkingBlock from './ThinkingBlock'
import type { StreamingPhase } from '../lib/sessions'

interface StreamingMessageProps {
  thinking: string
  text: string
  streamingPhase: StreamingPhase
  toolLabel?: string
}

export default function StreamingMessage({ thinking, text, streamingPhase, toolLabel }: StreamingMessageProps) {
  const isThinking = streamingPhase === 'thinking'
  const isToolRunning = streamingPhase === 'tool'

  return (
    <div className="flex flex-col gap-1 items-start">
      {/* Thinking block */}
      {thinking && (
        <ThinkingBlock
          thinking={thinking}
          isStreaming={isThinking}
          defaultCollapsed={false}
        />
      )}

      {/* Streaming text */}
      {text && (
        <div
          className="px-4 py-2.5 rounded-2xl rounded-tl-sm text-sm leading-relaxed break-words prose prose-sm max-w-none prose-p:my-1 prose-pre:border"
          style={{
            backgroundColor: 'var(--bg-elevated)',
            border: '1px solid var(--border-subtle)',
            color: 'var(--text-primary)',
          }}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          {streamingPhase === 'text' && (
            <motion.span
              className="inline-block w-1.5 h-4 ml-0.5 align-text-bottom"
              style={{ backgroundColor: 'var(--accent)' }}
              animate={{ opacity: [1, 0, 1] }}
              transition={{ repeat: Infinity, duration: 0.6 }}
            />
          )}
        </div>
      )}

      {/* Tool execution indicator */}
      {isToolRunning && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs"
          style={{
            backgroundColor: 'var(--accent-subtle)',
            border: '1px solid var(--accent)',
            color: 'var(--accent)',
          }}
        >
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>{toolLabel || 'Executing...'}</span>
        </motion.div>
      )}

      {/* Initial thinking state */}
      {isThinking && !thinking && !text && (
        <div className="flex items-center gap-2 px-3 py-2 text-xs" style={{ color: 'var(--text-muted)' }}>
          <motion.span
            className="w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: 'var(--accent)' }}
            animate={{ opacity: [1, 0.3, 1] }}
            transition={{ repeat: Infinity, duration: 1.2 }}
          />
          <span>Thinking...</span>
        </div>
      )}
    </div>
  )
}
