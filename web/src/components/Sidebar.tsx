import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, Search, ChevronLeft, ChevronRight, Eye, Cpu, Trash2, MessageSquare } from 'lucide-react'
import type { Session, Group } from '../lib/sessions'
import {
  listSessions, deleteSession, searchSessions,
  groupedSessions,
} from '../lib/sessions'
import { getSkillMeta } from '../lib/skills'

interface SidebarProps {
  activeSessionId: string
  onSelectSession: (id: string) => void
  onNewSession: () => void
  onNavigateVision: () => void
  refreshTrigger?: number   // increment to force re-render
}

const GROUP_ORDER: Group[] = ['Today', 'Yesterday', 'Last 7 days', 'Older']

export default function Sidebar({
  activeSessionId,
  onSelectSession,
  onNewSession,
  onNavigateVision,
  refreshTrigger,
}: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [query, setQuery] = useState('')
  const [sessions, setSessions] = useState<Session[]>([])
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  // Reload sessions whenever trigger changes or on mount
  useEffect(() => {
    setSessions(query ? searchSessions(query) : listSessions())
  }, [query, refreshTrigger])

  const handleDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    deleteSession(id)
    setSessions(listSessions())
    if (id === activeSessionId) onNewSession()
  }

  const grouped = groupedSessions(sessions)

  return (
    <motion.aside
      animate={{ width: collapsed ? 56 : 260 }}
      transition={{ duration: 0.2, ease: 'easeInOut' }}
      className="relative flex flex-col h-full bg-[#0a0a12] border-r border-white/8 flex-shrink-0 overflow-hidden"
    >
      {/* ── header ──────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-white/8 flex-shrink-0">
        <AnimatePresence>
          {!collapsed && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex items-center gap-2 overflow-hidden"
            >
              <Cpu className="w-4 h-4 text-cyan-400 flex-shrink-0" />
              <span className="text-sm font-bold text-zinc-100 whitespace-nowrap">MatClaw</span>
            </motion.div>
          )}
        </AnimatePresence>
        <button
          onClick={() => setCollapsed(c => !c)}
          className={`p-1 rounded text-zinc-500 hover:text-zinc-200 transition-colors flex-shrink-0 ${collapsed ? 'mx-auto' : ''}`}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
        </button>
      </div>

      {/* ── new session button ──────────────────────────────────── */}
      <div className="px-2 py-2 flex-shrink-0">
        <button
          onClick={onNewSession}
          className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm
            bg-cyan-500/10 border border-cyan-500/20 text-cyan-400
            hover:bg-cyan-500/20 hover:border-cyan-500/30 transition-all`}
          title="New session"
        >
          <Plus className="w-4 h-4 flex-shrink-0" />
          <AnimatePresence>
            {!collapsed && (
              <motion.span
                initial={{ opacity: 0, width: 0 }}
                animate={{ opacity: 1, width: 'auto' }}
                exit={{ opacity: 0, width: 0 }}
                className="whitespace-nowrap overflow-hidden"
              >
                New Session
              </motion.span>
            )}
          </AnimatePresence>
        </button>
      </div>

      {/* ── search ──────────────────────────────────────────────── */}
      <AnimatePresence>
        {!collapsed && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="px-2 pb-2 flex-shrink-0"
          >
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 border border-white/8 focus-within:border-white/20 transition-colors">
              <Search className="w-3.5 h-3.5 text-zinc-500 flex-shrink-0" />
              <input
                ref={searchRef}
                type="text"
                placeholder="Search sessions…"
                value={query}
                onChange={e => setQuery(e.target.value)}
                className="flex-1 bg-transparent outline-none text-xs text-zinc-300 placeholder:text-zinc-600 min-w-0"
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── session list ─────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden py-1 space-y-0.5 px-2">
        {!collapsed ? (
          sessions.length === 0 ? (
            <p className="text-xs text-zinc-600 text-center py-8 px-3">
              {query ? 'No sessions match.' : 'No sessions yet.'}
            </p>
          ) : (
            GROUP_ORDER.map(group => {
              const items = grouped[group]
              if (!items.length) return null
              return (
                <div key={group} className="mb-2">
                  <p className="text-xs text-zinc-600 px-2 py-1 font-medium uppercase tracking-wider">
                    {group}
                  </p>
                  {items.map(s => (
                    <SessionRow
                      key={s.id}
                      session={s}
                      isActive={s.id === activeSessionId}
                      isHovered={hoveredId === s.id}
                      onHover={setHoveredId}
                      onSelect={() => onSelectSession(s.id)}
                      onDelete={e => handleDelete(e, s.id)}
                    />
                  ))}
                </div>
              )
            })
          )
        ) : (
          /* Collapsed: just dots for recent sessions */
          <div className="flex flex-col items-center gap-1 pt-2">
            {sessions.slice(0, 8).map(s => (
              <button
                key={s.id}
                onClick={() => onSelectSession(s.id)}
                title={s.title}
                className={`w-2 h-2 rounded-full transition-all ${
                  s.id === activeSessionId ? 'bg-cyan-400' : 'bg-zinc-600 hover:bg-zinc-400'
                }`}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── footer ──────────────────────────────────────────────── */}
      <div className="px-2 py-2 border-t border-white/8 flex-shrink-0">
        <button
          onClick={onNavigateVision}
          className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-zinc-400
            hover:text-cyan-400 hover:bg-white/5 transition-all`}
          title="Vision Gallery"
        >
          <Eye className="w-4 h-4 flex-shrink-0" />
          <AnimatePresence>
            {!collapsed && (
              <motion.span
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="whitespace-nowrap"
              >
                Vision Gallery
              </motion.span>
            )}
          </AnimatePresence>
        </button>
      </div>
    </motion.aside>
  )
}

// ── Individual session row ───────────────────────────────────────────────────
interface RowProps {
  session: Session
  isActive: boolean
  isHovered: boolean
  onHover: (id: string | null) => void
  onSelect: () => void
  onDelete: (e: React.MouseEvent) => void
}

function SessionRow({ session, isActive, isHovered, onHover, onSelect, onDelete }: RowProps) {
  // Show skill icon from last assistant message
  const lastSkill = [...session.messages].reverse().find(m => m.role === 'assistant' && m.skill)?.skill
  const meta = lastSkill ? getSkillMeta(lastSkill) : null

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={e => e.key === 'Enter' && onSelect()}
      onMouseEnter={() => onHover(session.id)}
      onMouseLeave={() => onHover(null)}
      className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left transition-all cursor-pointer ${
        isActive
          ? 'bg-cyan-500/10 border border-cyan-500/20'
          : 'hover:bg-white/5 border border-transparent'
      }`}
    >
      {/* skill/active indicator */}
      <span className={`flex-shrink-0 ${isActive ? 'text-cyan-400' : 'text-zinc-600'}`}>
        {meta ? meta.icon : <MessageSquare className="w-3 h-3 inline" />}
      </span>

      {/* title */}
      <span className={`flex-1 text-xs truncate ${isActive ? 'text-zinc-100' : 'text-zinc-400'}`}>
        {session.title}
      </span>

      {/* delete on hover */}
      {isHovered && (
        <button
          onClick={onDelete}
          className="p-0.5 rounded text-zinc-600 hover:text-red-400 transition-colors flex-shrink-0"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      )}
    </div>
  )
}
