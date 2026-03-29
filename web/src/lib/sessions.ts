// ── Session persistence (localStorage + server write-through) ────────────────

const API = 'http://localhost:8000'

/** Fire-and-forget sync to backend. Never throws. */
function _syncToServer(session: Session): void {
  fetch(`${API}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(session),
  }).catch(() => { /* backend unavailable — localStorage is the fallback */ })
}

/** Fire-and-forget delete on backend. */
function _deleteFromServer(id: string): void {
  fetch(`${API}/api/sessions/${id}`, { method: 'DELETE' })
    .catch(() => {})
}

/**
 * Bootstrap: on app start, fetch all sessions from the backend and merge
 * them into localStorage. Server is source of truth when available.
 * Falls back gracefully if the server is unreachable.
 */
export async function bootstrapFromServer(): Promise<void> {
  try {
    const resp = await fetch(`${API}/api/sessions`)
    if (!resp.ok) return
    const serverSessions: Session[] = await resp.json()
    if (!serverSessions.length) return

    const local = readAll()
    const localById = Object.fromEntries(local.map(s => [s.id, s]))
    const serverById = Object.fromEntries(serverSessions.map(s => [s.id, s]))

    // Merge: take the version with the latest updatedAt
    const merged = Object.values({
      ...localById,
      ...Object.fromEntries(
        serverSessions
          .filter(s => !localById[s.id] || s.updatedAt >= localById[s.id].updatedAt)
          .map(s => [s.id, s]),
      ),
    }).sort((a, b) => b.updatedAt - a.updatedAt)

    writeAll(merged as Session[])

    // Push any local-only sessions up to the server
    for (const s of local) {
      if (!serverById[s.id]) _syncToServer(s)
    }
  } catch {
    // Server not reachable — silently continue with localStorage
  }
}

export interface ProjectFile {
  path: string
  filename: string
  language: string
  content: string
  url: string
}

export type StreamingPhase = 'thinking' | 'text' | 'tool' | 'done' | null

export interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  thinking?: string
  streamingPhase?: StreamingPhase
  skill?: string
  plots?: string[]
  metrics?: Record<string, unknown>
  elapsed_ms?: number
  files?: ProjectFile[]
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
  _syncToServer(session)
}

export function deleteSession(id: string): void {
  writeAll(readAll().filter(s => s.id !== id))
  if (getActiveSessionId() === id) {
    localStorage.removeItem(ACTIVE_KEY)
  }
  _deleteFromServer(id)
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
