// ── Session persistence (localStorage) ──────────────────────────────────────

export interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  skill?: string
  plots?: string[]
  metrics?: Record<string, unknown>
  elapsed_ms?: number
  ts: number
}

export interface Session {
  id: string
  title: string       // first user message (≤40 chars) or "New Session"
  createdAt: number   // ms timestamp
  updatedAt: number
  messages: Message[]
}

const SESSIONS_KEY = 'matclaw_sessions'
const ACTIVE_KEY   = 'matclaw_active_session'

// ── persistence helpers ──────────────────────────────────────────────────────

function readAll(): Session[] {
  try {
    return JSON.parse(localStorage.getItem(SESSIONS_KEY) || '[]')
  } catch {
    return []
  }
}

function writeAll(sessions: Session[]): void {
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions))
}

// ── public API ───────────────────────────────────────────────────────────────

export function listSessions(): Session[] {
  return readAll().sort((a, b) => b.updatedAt - a.updatedAt)
}

export function getSession(id: string): Session | null {
  return readAll().find(s => s.id === id) ?? null
}

export function saveSession(session: Session): void {
  const all = readAll().filter(s => s.id !== session.id)
  writeAll([session, ...all])
}

export function deleteSession(id: string): void {
  writeAll(readAll().filter(s => s.id !== id))
  if (getActiveSessionId() === id) {
    localStorage.removeItem(ACTIVE_KEY)
  }
}

export function createSession(): Session {
  const session: Session = {
    id: crypto.randomUUID(),
    title: 'New Session',
    createdAt: Date.now(),
    updatedAt: Date.now(),
    messages: [],
  }
  saveSession(session)
  setActiveSessionId(session.id)
  return session
}

export function searchSessions(query: string): Session[] {
  if (!query.trim()) return listSessions()
  const q = query.toLowerCase()
  return listSessions().filter(s =>
    s.title.toLowerCase().includes(q) ||
    s.messages.some(m => m.text.toLowerCase().includes(q))
  )
}

export function getActiveSessionId(): string | null {
  return localStorage.getItem(ACTIVE_KEY)
}

export function setActiveSessionId(id: string): void {
  localStorage.setItem(ACTIVE_KEY, id)
}

/** Get or create the active session. */
export function getOrCreateActiveSession(): Session {
  const id = getActiveSessionId()
  if (id) {
    const s = getSession(id)
    if (s) return s
  }
  return createSession()
}

/** Update title from first user message. */
export function autoTitle(session: Session): Session {
  if (session.title !== 'New Session') return session
  const first = session.messages.find(m => m.role === 'user')
  if (!first) return session
  return { ...session, title: first.text.slice(0, 42).trim() || 'New Session' }
}

// ── time grouping helpers ────────────────────────────────────────────────────

export type Group = 'Today' | 'Yesterday' | 'Last 7 days' | 'Older'

export function sessionGroup(s: Session): Group {
  const now = Date.now()
  const diff = now - s.updatedAt
  const day = 86_400_000
  if (diff < day)      return 'Today'
  if (diff < 2 * day)  return 'Yesterday'
  if (diff < 7 * day)  return 'Last 7 days'
  return 'Older'
}

export function groupedSessions(sessions: Session[]): Record<Group, Session[]> {
  const groups: Record<Group, Session[]> = {
    'Today': [], 'Yesterday': [], 'Last 7 days': [], 'Older': [],
  }
  for (const s of sessions) groups[sessionGroup(s)].push(s)
  return groups
}
