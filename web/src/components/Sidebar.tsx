import { useState, useRef } from 'react'
import { Search, Trash2, MessageSquare } from 'lucide-react'
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
  refreshTrigger?: number
  collapsed?: boolean
}

const GROUP_ORDER: Group[] = ['Today', 'Yesterday', 'Last 7 days', 'Older']

export default function Sidebar({
  activeSessionId,
  onSelectSession,
  onNewSession,
  refreshTrigger,
  collapsed = false,
}: SidebarProps) {
  const [query, setQuery] = useState('')
  const [sessions, setSessions] = useState<Session[]>(() => listSessions())
  const [prevQuery, setPrevQuery] = useState('')
  const [prevRefresh, setPrevRefresh] = useState(refreshTrigger)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  if (query !== prevQuery || refreshTrigger !== prevRefresh) {
    setPrevQuery(query)
    setPrevRefresh(refreshTrigger)
    setSessions(query ? searchSessions(query) : listSessions())
  }

  const handleDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    deleteSession(id)
    setSessions(listSessions())
    if (id === activeSessionId) onNewSession()
  }

  const grouped = groupedSessions(sessions)

  if (collapsed) return null

  return (
    <div
      className="flex flex-col h-full w-[240px] flex-shrink-0 overflow-hidden"
      style={{
        backgroundColor: 'var(--sidebar-bg)',
        borderRight: '1px solid var(--border-subtle)',
      }}
    >
      {/* Search */}
      <div className="px-2 py-2 flex-shrink-0">
        <div
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg transition-colors"
          style={{
            backgroundColor: 'var(--bg-hover)',
            border: '1px solid var(--border-subtle)',
          }}
        >
          <Search className="w-3.5 h-3.5 flex-shrink-0" style={{ color: 'var(--text-muted)' }} />
          <input
            ref={searchRef}
            type="text"
            placeholder="Search sessions..."
            value={query}
            onChange={e => setQuery(e.target.value)}
            className="flex-1 bg-transparent outline-none text-xs min-w-0"
            style={{ color: 'var(--text-secondary)' }}
          />
        </div>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden py-1 space-y-0.5 px-2">
        {sessions.length === 0 ? (
          <p className="text-xs text-center py-8 px-3" style={{ color: 'var(--text-muted)' }}>
            {query ? 'No sessions match.' : 'No sessions yet.'}
          </p>
        ) : (
          GROUP_ORDER.map(group => {
            const items = grouped[group]
            if (!items.length) return null
            return (
              <div key={group} className="mb-2">
                <p
                  className="text-[10px] px-2 py-1 font-medium uppercase tracking-wider"
                  style={{ color: 'var(--text-muted)' }}
                >
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
        )}
      </div>
    </div>
  )
}

// ── Session row ──────────────────────────────────────────────────────────────

interface RowProps {
  session: Session
  isActive: boolean
  isHovered: boolean
  onHover: (id: string | null) => void
  onSelect: () => void
  onDelete: (e: React.MouseEvent) => void
}

function SessionRow({ session, isActive, isHovered, onHover, onSelect, onDelete }: RowProps) {
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
      className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left transition-all duration-150 cursor-pointer"
      style={{
        backgroundColor: isActive ? 'var(--accent-subtle)' : isHovered ? 'var(--bg-hover)' : 'transparent',
        border: isActive ? '1px solid var(--accent)' : '1px solid transparent',
        color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
      }}
    >
      <span className="flex-shrink-0" style={{ color: isActive ? 'var(--accent)' : 'var(--text-muted)' }}>
        {meta ? meta.icon : <MessageSquare className="w-3 h-3 inline" />}
      </span>
      <span className="flex-1 text-xs truncate" style={{ color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
        {session.title}
      </span>
      {isHovered && (
        <button
          onClick={onDelete}
          className="p-0.5 rounded transition-colors flex-shrink-0"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => e.currentTarget.style.color = 'var(--error)'}
          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
        >
          <Trash2 className="w-3 h-3" />
        </button>
      )}
    </div>
  )
}
