import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Mic, MicOff, Send, Cpu, Activity, RotateCcw, Eye, ChevronDown, Sparkles, FileText, Shield, Zap, Square } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useVoiceInput } from '../hooks/useVoiceInput'
import { useStreaming } from '../hooks/useStreaming'
import type { Message, Session } from '../lib/sessions'
import {
  getOrCreateActiveSession, getSession, saveSession,
  createSession, setActiveSessionId, listSessions,
  autoTitle, bootstrapFromServer,
} from '../lib/sessions'
import { getSkillMeta } from '../lib/skills'
import ActivityBar, { type ActivityTab } from '../components/ActivityBar'
import Sidebar from '../components/Sidebar'
import WorkspaceTabs, { type WorkspaceTab } from '../components/WorkspaceTabs'
import SettingsPanel from '../components/SettingsPanel'
import FlowDashboard from '../components/FlowDashboard'
import FileTree from '../components/FileTree'
import StreamingMessage from '../components/StreamingMessage'
import ThinkingBlock from '../components/ThinkingBlock'
import TerminalTab from '../components/TerminalTab'
import AgentsTab from './Agents'
import CodeEditor from '../components/CodeEditor'

const API = 'http://localhost:8000'

interface Model {
  id: string
  label: string
  active: boolean
}

// ── Sub-components ───────────────────────────────────────────────────────────

function SkillBadge({ skill }: { skill: string }) {
  const meta = getSkillMeta(skill)
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium"
      style={{
        backgroundColor: 'var(--accent-subtle)',
        color: 'var(--accent)',
      }}
    >
      {meta.icon} {meta.label}
    </span>
  )
}

function PlotCard({ url }: { url: string }) {
  const isGif = url.endsWith('.gif')
  return (
    <div
      className="mt-2 rounded-lg overflow-hidden max-w-md"
      style={{ border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-surface)' }}
    >
      <div
        className="px-3 py-1.5 flex items-center gap-2"
        style={{
          borderBottom: '1px solid var(--border-subtle)',
          backgroundColor: 'var(--bg-hover)',
        }}
      >
        <Eye className="w-3 h-3" style={{ color: 'var(--accent)' }} />
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {isGif ? '\u25B6 Animated' : '\uD83D\uDCCA Plot'}
        </span>
      </div>
      <img
        src={`${API}${url}`}
        alt="MATLAB output"
        className="w-full object-contain max-h-72"
        onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
      />
    </div>
  )
}

function MetricsRow({ elapsed_ms }: { elapsed_ms?: number }) {
  if (!elapsed_ms) return null
  return (
    <div className="flex items-center gap-2 mt-1">
      <Activity className="w-3 h-3" style={{ color: 'var(--text-muted)' }} />
      <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{elapsed_ms}ms</span>
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

export default function ControlPlane() {
  const navigate = useNavigate()

  // ── layout state ────────────────────────────────────────────
  const [activityTab, setActivityTab] = useState<ActivityTab>('chat')
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTab>('flow')
  const [followMode, setFollowMode] = useState(() => {
    return localStorage.getItem('matclaw-follow-mode') !== 'false'
  })

  const toggleFollowMode = useCallback(() => {
    setFollowMode(prev => {
      const next = !prev
      localStorage.setItem('matclaw-follow-mode', String(next))
      return next
    })
  }, [])

  // ── active model + model list ────────────────────────────────
  const [activeModelLabel, setActiveModelLabel] = useState('NVIDIA Nemotron Ultra 253B')
  const [modelList, setModelList] = useState<{id:string, label:string, active:boolean}[]>([])
  const [showModelPicker, setShowModelPicker] = useState(false)
  const modelPickerRef = useRef<HTMLDivElement>(null)

  const fetchModels = useCallback(() => {
    fetch(`${API}/api/settings/models`)
      .then(r => r.json())
      .then((data: Model[]) => {
        setModelList(data.map(m => ({ id: m.id, label: m.label, active: m.active })))
        const active = data.find(m => m.active)
        if (active) setActiveModelLabel(active.label)
      })
      .catch((err) => { console.error('Failed to fetch models:', err) })
    fetch(`${API}/api/settings/active-model`)
      .then(r => r.json())
      .then(data => { if (data.label) setActiveModelLabel(data.label) })
      .catch((err) => { console.error('Failed to fetch active model:', err) })
  }, [])

  useEffect(() => { fetchModels() }, [fetchModels])

  // Close model picker on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (modelPickerRef.current && !modelPickerRef.current.contains(e.target as Node))
        setShowModelPicker(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.addEventListener('mousedown', handler)
  }, [])

  const switchModel = async (id: string) => {
    await fetch(`${API}/api/settings/models/${id}/activate`, { method: 'POST' })
    setShowModelPicker(false)
    fetchModels()
  }

  // ── execution mode ─────────────────────────────────────────
  type ExecMode = 'ask' | 'auto' | 'plan' | 'agentic'
  const [execMode, setExecMode] = useState<ExecMode>(() => {
    return (localStorage.getItem('matclaw-exec-mode') as ExecMode) || 'auto'
  })
  const [showModePicker, setShowModePicker] = useState(false)
  const modePickerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    localStorage.setItem('matclaw-exec-mode', execMode)
  }, [execMode])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (modePickerRef.current && !modePickerRef.current.contains(e.target as Node))
        setShowModePicker(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const EXEC_MODES: { value: ExecMode; label: string; desc: string; icon: React.ReactNode }[] = [
    { value: 'ask', label: 'Ask', desc: 'Always ask before making changes', icon: <Shield className="w-3.5 h-3.5" /> },
    { value: 'auto', label: 'Auto', desc: 'Automatically accept all edits', icon: <Zap className="w-3.5 h-3.5" /> },
    { value: 'plan', label: 'Plan', desc: 'Create a plan before making changes', icon: <FileText className="w-3.5 h-3.5" /> },
    { value: 'agentic', label: 'Agentic', desc: 'Full autonomous agent with tool use', icon: <Sparkles className="w-3.5 h-3.5" /> },
  ]

  // Sync activity bar with workspace tabs
  const handleActivityTab = (tab: ActivityTab) => {
    setActivityTab(tab)
    if (tab === 'settings') setWorkspaceTab('settings')
    else if (tab === 'agents') setWorkspaceTab('agents')
    else setWorkspaceTab('flow')
  }

  const handleWorkspaceTab = (tab: WorkspaceTab) => {
    setWorkspaceTab(tab)
    if (tab === 'settings') setActivityTab('settings')
    else if (tab === 'agents') setActivityTab('agents')
    else setActivityTab('chat')
  }

  // ── editor state ───────────────────────────────────────────────
  const [editorFiles, setEditorFiles] = useState<import('../lib/sessions').ProjectFile[]>([])
  const openInEditor = useCallback((file: import('../lib/sessions').ProjectFile) => {
    setEditorFiles(prev => prev.some(f => f.filename === file.filename) ? prev : [...prev, file])
    setWorkspaceTab('editor')
  }, [])

  // ── session state ──────────────────────────────────────────────
  const [session, setSession] = useState<Session>(() => getOrCreateActiveSession())
  const [sidebarRefresh, setSidebarRefresh] = useState(0)

  const msgs = session.messages
  const setMsgs = useCallback((updater: (prev: Message[]) => Message[]) => {
    setSession(prev => {
      const updated: Session = {
        ...prev,
        messages: updater(prev.messages),
        updatedAt: Date.now(),
      }
      const titled = autoTitle(updated)
      saveSession(titled)
      setSidebarRefresh(r => r + 1)
      return titled
    })
  }, [])

  // ── other state ────────────────────────────────────────────────
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [matlabOnline, setMatlabOnline] = useState<boolean | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const { isListening, transcript, isSupported, startListening, stopListening, resetTranscript } = useVoiceInput()

  useEffect(() => { if (transcript) setInput(transcript) }, [transcript])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, loading])

  // health check
  const checkHealth = useCallback(() => {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 3000)

    fetch(`${API}/health`, { signal: controller.signal })
      .then(r => r.json())
      .then(d => {
        clearTimeout(timeoutId)
        setMatlabOnline(!!d.matlab)
      })
      .catch(() => {
        clearTimeout(timeoutId)
        setMatlabOnline(false)
      })
  }, [])

  const reconnectMatlab = useCallback(async () => {
    setMatlabOnline(null)
    try { await fetch(`${API}/api/connect`, { method: 'POST' }) } catch (err) { console.error('Failed to reconnect MATLAB:', err) }
    checkHealth()
  }, [checkHealth])

  useEffect(() => {
    checkHealth()
    const id = setInterval(checkHealth, 10000)
    return () => clearInterval(id)
  }, [checkHealth])

  // Bootstrap sessions from server on mount (restores history after server restart)
  useEffect(() => {
    bootstrapFromServer().then(() => setSidebarRefresh(r => r + 1))
  }, [])

  // ── session actions ────────────────────────────────────────────
  const handleNewSession = useCallback(() => {
    const s = createSession()
    setSession(s)
    setInput('')
    setSidebarRefresh(r => r + 1)
  }, [])

  const handleSelectSession = useCallback((id: string) => {
    const s = getSession(id)
    if (!s) return
    setActiveSessionId(id)
    setSession(s)
    setInput('')
  }, [])

  // ── streaming ─────────────────────────────────────────────────
  const { streamRun, cancel } = useStreaming()
  const pendingRef = useRef({ thinking: '', text: '', toolLabel: '' })
  const rafRef = useRef(0)
  const streamMsgIdRef = useRef('')

  const flushPending = useCallback(() => {
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      const p = pendingRef.current
      const msgId = streamMsgIdRef.current
      setMsgs(prev => {
        const idx = prev.findIndex(m => m.id === msgId)
        if (idx === -1) return prev
        const updated = { ...prev[idx], thinking: p.thinking, text: p.text }
        return [...prev.slice(0, idx), updated, ...prev.slice(idx + 1)]
      })
      rafRef.current = 0
    })
  }, [setMsgs])

  // ── send ───────────────────────────────────────────────────────
  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || loading) return
    stopListening()
    resetTranscript()
    setInput('')

    const userMsg: Message = { id: crypto.randomUUID(), role: 'user', text, ts: Date.now() }
    const asstId = crypto.randomUUID()
    const asstMsg: Message = {
      id: asstId, role: 'assistant', text: '', thinking: '',
      streamingPhase: 'thinking', ts: Date.now(),
    }
    streamMsgIdRef.current = asstId
    pendingRef.current = { thinking: '', text: '', toolLabel: '' }

    setMsgs(prev => [...prev, userMsg, asstMsg])
    setLoading(true)

    const history = msgs.slice(-10).map(m => ({ role: m.role, text: m.text }))

    try {
      await streamRun(text, session.id, history, {
        onThinking: (token) => {
          pendingRef.current.thinking += token
          flushPending()
        },
        onText: (token) => {
          pendingRef.current.text += token
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1 || prev[idx].streamingPhase === 'text') return prev
            return [...prev.slice(0, idx), { ...prev[idx], streamingPhase: 'text' as const }, ...prev.slice(idx + 1)]
          })
          flushPending()
        },
        onToolStart: (data) => {
          pendingRef.current.toolLabel = data.label
          // Follow Mode: auto-switch to relevant workspace tab
          if (followMode) {
            if (data.action === 'run_matlab') {
              setWorkspaceTab('terminal')
            }
          }
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            return [...prev.slice(0, idx), {
              ...prev[idx], streamingPhase: 'tool' as const,
              thinking: pendingRef.current.thinking,
              text: pendingRef.current.text,
            }, ...prev.slice(idx + 1)]
          })
        },
        onToolResult: (data) => {
          // Follow Mode: switch back to flow on result (especially with plots)
          if (followMode && data.plots?.length) {
            setWorkspaceTab('flow')
          }
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            const existing = prev[idx].text || ''
            // Append tool output text so agent node results appear in the chat body
            const appendedText = data.output
              ? `${existing}\n\n${data.output}`.trim()
              : existing
            return [...prev.slice(0, idx), {
              ...prev[idx],
              text: appendedText,
              plots: [...(prev[idx].plots || []), ...(data.plots || [])],
              files: [...(prev[idx].files || []), ...(data.files || [])],
            }, ...prev.slice(idx + 1)]
          })
        },
        onDone: (data) => {
          // Follow Mode: return to flow when task completes
          if (followMode) {
            setWorkspaceTab('flow')
          }
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            return [...prev.slice(0, idx), {
              ...prev[idx],
              text: data.reply || pendingRef.current.text || 'Done.',
              thinking: pendingRef.current.thinking,
              skill: data.skill,
              elapsed_ms: data.elapsed_ms,
              plots: data.plots?.length ? data.plots : (prev[idx].plots || []),
              files: data.files?.length ? data.files : (prev[idx].files || []),
              streamingPhase: 'done' as const,
            }, ...prev.slice(idx + 1)]
          })
          setLoading(false)
        },
        onError: (msg) => {
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            return [...prev.slice(0, idx), {
              ...prev[idx],
              text: pendingRef.current.text || `Error: ${msg}`,
              thinking: pendingRef.current.thinking,
              streamingPhase: 'done' as const,
            }, ...prev.slice(idx + 1)]
          })
          setLoading(false)
        },
      }, execMode)
    } catch (err) {
      setMsgs(prev => {
        const idx = prev.findIndex(m => m.id === asstId)
        if (idx === -1) return prev
        return [...prev.slice(0, idx), {
          ...prev[idx], text: `Connection error: ${err}`, streamingPhase: 'done' as const,
        }, ...prev.slice(idx + 1)]
      })
      setLoading(false)
    }
  }, [input, loading, session.id, msgs, stopListening, resetTranscript, setMsgs, streamRun, flushPending, followMode, execMode])

  const handleStop = useCallback(() => {
    cancel()
    // Mark the current streaming message as done
    const asstId = streamMsgIdRef.current
    if (asstId) {
      setMsgs(prev => {
        const idx = prev.findIndex(m => m.id === asstId)
        if (idx === -1) return prev
        return [...prev.slice(0, idx), {
          ...prev[idx],
          text: (prev[idx].text || pendingRef.current.text || '') + '\n\n*[Generation stopped]*',
          thinking: prev[idx].thinking || pendingRef.current.thinking || '',
          streamingPhase: 'done' as const,
        }, ...prev.slice(idx + 1)]
      })
    }
    setLoading(false)
  }, [cancel, setMsgs])

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  const taskCount = listSessions().length

  // ── derived: active assistant message for FlowDashboard ────────
  const activeMessage = useMemo(() => {
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') return msgs[i]
    }
    return null
  }, [msgs])

  // ── render ─────────────────────────────────────────────────────
  return (
    <div className="h-screen flex overflow-hidden" style={{ backgroundColor: 'var(--bg-base)', color: 'var(--text-primary)' }}>

      {/* ── Activity Bar (narrow left strip) ──────────────────── */}
      <ActivityBar
        activeTab={activityTab}
        onTabChange={handleActivityTab}
        onNewSession={handleNewSession}
        onNavigateVision={() => navigate('/vision')}
        taskCount={taskCount}
      />

      {/* ── Chat Sidebar (session list + agent chat) ──────────── */}
      {activityTab === 'chat' && (
        <Sidebar
          activeSessionId={session.id}
          onSelectSession={handleSelectSession}
          onNewSession={handleNewSession}
          refreshTrigger={sidebarRefresh}
        />
      )}

      {/* ── Main Workspace ────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* Workspace Tabs */}
        <WorkspaceTabs activeTab={workspaceTab} onTabChange={handleWorkspaceTab} />

        {/* Tab Content */}
        {workspaceTab === 'settings' ? (
          <SettingsPanel />
        ) : workspaceTab === 'agents' ? (
          <AgentsTab />
        ) : workspaceTab === 'terminal' ? (
          <TerminalTab />
        ) : workspaceTab === 'editor' ? (
          <CodeEditor
            files={editorFiles}
            onRunMatlab={(code, filename) => {
              setWorkspaceTab('flow')
              setInput(`Run this MATLAB code from ${filename}:\n\`\`\`matlab\n${code}\n\`\`\``)
            }}
          />
        ) : workspaceTab === 'flow' ? (
          /* Flow / Chat workspace */
          <div className="flex-1 flex flex-col overflow-hidden">

            {/* Header bar */}
            <header
              className="flex items-center justify-between px-4 py-2 flex-shrink-0 backdrop-blur-sm"
              style={{
                backgroundColor: 'var(--header-bg)',
                borderBottom: '1px solid var(--border-subtle)',
              }}
            >
              <div className="flex items-center gap-2">
                <Cpu className="w-4 h-4" style={{ color: 'var(--accent)' }} />
                <span className="text-sm font-semibold truncate max-w-xs" style={{ color: 'var(--text-primary)' }}>
                  {session.title === 'New Session' ? 'Control Plane' : session.title}
                </span>
              </div>
              <div className="flex items-center gap-3">
                {/* MATLAB status */}
                <button
                  onClick={matlabOnline === false ? reconnectMatlab : undefined}
                  className="flex items-center gap-1.5 text-xs transition-colors"
                  style={{ cursor: matlabOnline === false ? 'pointer' : 'default' }}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${matlabOnline === null ? 'animate-pulse' : ''}`}
                    style={{
                      backgroundColor: matlabOnline === true ? 'var(--success)' :
                        matlabOnline === false ? 'var(--error)' : 'var(--warning)'
                    }}
                  />
                  <span style={{
                    color: matlabOnline === false ? 'var(--error)' : 'var(--text-muted)'
                  }}>
                    MATLAB {matlabOnline === true ? 'online' : matlabOnline === false ? 'offline' : 'checking…'}
                  </span>
                </button>
                <button
                  onClick={() => setMsgs(() => [])}
                  className="flex items-center gap-1 text-xs transition-colors"
                  title="Clear messages"
                  style={{ color: 'var(--text-muted)' }}
                  onMouseEnter={e => e.currentTarget.style.color = 'var(--text-secondary)'}
                  onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                </button>
              </div>
            </header>

            {/* Flow Dashboard (only on Flow tab) */}
            {workspaceTab === 'flow' && activeMessage && (
              <div className="px-5 pt-3 flex-shrink-0">
                <FlowDashboard
                  activeMessage={activeMessage}
                  followMode={followMode}
                  onToggleFollowMode={toggleFollowMode}
                  toolLabel={pendingRef.current.toolLabel || undefined}
                />
              </div>
            )}

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 scroll-smooth">
              <AnimatePresence initial={false}>
                {msgs.length === 0 && (
                  <motion.div
                    key="empty"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex flex-col items-center justify-center h-64 gap-3"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    <Cpu className="w-8 h-8 opacity-30" />
                    <p className="text-sm">Ask MatClaw anything about your MATLAB workspace</p>
                    <div className="flex flex-wrap justify-center gap-2 mt-2 max-w-lg">
                      {[
                        'Plot a 3D surface of sin(x)·cos(y)',
                        'What is FFT and when should I use it?',
                        'Create a snake game in MATLAB',
                        'Animate the Lorenz attractor',
                      ].map(s => (
                        <button
                          key={s}
                          onClick={() => setInput(s)}
                          className="text-xs px-3 py-1.5 rounded-full transition-colors"
                          style={{
                            border: '1px solid var(--border-default)',
                            color: 'var(--text-secondary)',
                          }}
                          onMouseEnter={e => {
                            e.currentTarget.style.borderColor = 'var(--accent)'
                            e.currentTarget.style.color = 'var(--accent)'
                          }}
                          onMouseLeave={e => {
                            e.currentTarget.style.borderColor = 'var(--border-default)'
                            e.currentTarget.style.color = 'var(--text-secondary)'
                          }}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  </motion.div>
                )}

                {msgs.map(m => {
                  const isStreaming = m.role === 'assistant' && m.streamingPhase && m.streamingPhase !== 'done'

                  return (
                    <motion.div
                      key={m.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.2 }}
                      className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
                    >
                      <div className={`max-w-2xl flex flex-col gap-1 ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
                        {m.skill && m.role === 'assistant' && !isStreaming && <SkillBadge skill={m.skill} />}

                        {m.role === 'user' ? (
                          <div
                            className="px-4 py-2.5 rounded-2xl rounded-tr-sm text-sm leading-relaxed break-words whitespace-pre-wrap"
                            style={{
                              backgroundColor: 'var(--accent-subtle)',
                              border: '1px solid var(--accent)',
                              color: 'var(--text-primary)',
                            }}
                          >
                            {m.text}
                          </div>
                        ) : isStreaming ? (
                          <StreamingMessage
                            thinking={m.thinking || ''}
                            text={m.text}
                            streamingPhase={m.streamingPhase!}
                            toolLabel={pendingRef.current.toolLabel}
                          />
                        ) : (
                          <>
                            {m.thinking && (
                              <ThinkingBlock thinking={m.thinking} defaultCollapsed={true} />
                            )}
                            <div
                              className="px-4 py-2.5 rounded-2xl rounded-tl-sm text-sm leading-relaxed break-words prose prose-sm max-w-none prose-p:my-1 prose-pre:border"
                              style={{
                                backgroundColor: 'var(--bg-elevated)',
                                border: '1px solid var(--border-subtle)',
                                color: 'var(--text-primary)',
                              }}
                            >
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
                            </div>
                          </>
                        )}

                        {m.plots?.map(url => <PlotCard key={url} url={url} />)}
                        {m.files && m.files.length > 0 && (
                          <FileTree
                            files={m.files}
                            onRunMatlab={(code) => {
                              setInput(`run this MATLAB code:\n${code}`)
                            }}
                            onOpenInEditor={openInEditor}
                          />
                        )}
                        {!isStreaming && <MetricsRow elapsed_ms={m.elapsed_ms} />}
                      </div>
                    </motion.div>
                  )
                })}
              </AnimatePresence>
              <div ref={bottomRef} />
            </div>

            {/* Voice indicator */}
            <AnimatePresence>
              {isListening && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}
                  className="flex items-center justify-center gap-3 py-2"
                  style={{
                    backgroundColor: 'var(--accent-subtle)',
                    borderTop: '1px solid var(--accent)',
                  }}
                >
                  <div className="flex gap-0.5 items-end h-4">
                    {[3, 6, 4, 7, 3].map((h, i) => (
                      <motion.div
                        key={i}
                        className="w-1 rounded-full"
                        style={{ backgroundColor: 'var(--accent)' }}
                        animate={{ height: [`${h}px`, `${h * 2.5}px`, `${h}px`] }}
                        transition={{ repeat: Infinity, duration: 0.6, delay: i * 0.1 }}
                      />
                    ))}
                  </div>
                  <span className="text-xs font-medium" style={{ color: 'var(--accent)' }}>Listening…</span>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Input bar */}
            <div className="px-5 pb-4 pt-2 flex-shrink-0" style={{ borderTop: '1px solid var(--border-subtle)' }}>
              <div
                className="rounded-2xl px-3 py-2 transition-colors"
                style={{
                  backgroundColor: 'var(--bg-input)',
                  border: '1px solid var(--border-default)',
                }}
              >
                {/* Textarea + send row */}
                <div className="flex items-end gap-2">
                  <textarea
                    rows={1}
                    className="flex-1 bg-transparent resize-none outline-none text-sm max-h-32 leading-relaxed"
                    style={{
                      color: 'var(--text-primary)',
                      fontFamily: 'var(--font-ui)',
                    }}
                    placeholder="Ask MatClaw… (Shift+Enter for newline)"
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={onKey}
                  />
                  <button
                    onClick={isListening ? stopListening : startListening}
                    disabled={!isSupported}
                    title={!isSupported ? 'Voice not supported' : isListening ? 'Stop' : 'Voice input'}
                    className="p-1.5 rounded-lg transition-all"
                    style={{
                      color: isListening ? 'var(--accent)' : 'var(--text-muted)',
                      backgroundColor: isListening ? 'var(--accent-subtle)' : 'transparent',
                      opacity: !isSupported ? 0.3 : 1,
                      cursor: !isSupported ? 'not-allowed' : 'pointer',
                    }}
                  >
                    {isListening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
                  </button>
                  {loading ? (
                    <button
                      onClick={handleStop}
                      className="p-1.5 rounded-lg transition-all"
                      title="Stop generation"
                      style={{
                        backgroundColor: 'var(--error, #ef4444)',
                        border: '1px solid var(--error, #ef4444)',
                        color: 'white',
                        cursor: 'pointer',
                      }}
                    >
                      <Square className="w-4 h-4" fill="currentColor" />
                    </button>
                  ) : (
                    <button
                      onClick={send}
                      disabled={!input.trim()}
                      className="p-1.5 rounded-lg transition-all"
                      style={{
                        backgroundColor: 'var(--accent-subtle)',
                        border: '1px solid var(--accent)',
                        color: 'var(--accent)',
                        opacity: !input.trim() ? 0.3 : 1,
                        cursor: !input.trim() ? 'not-allowed' : 'pointer',
                      }}
                    >
                      <Send className="w-4 h-4" />
                    </button>
                  )}
                </div>

                {/* Bottom toolbar: mode picker + model selector */}
                <div className="flex items-center justify-between mt-1.5 pt-1.5" style={{ borderTop: '1px solid var(--border-subtle)' }}>
                  {/* Mode picker */}
                  <div className="relative" ref={modePickerRef}>
                    <button
                      onClick={() => setShowModePicker(p => !p)}
                      className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition"
                      style={{ color: 'var(--text-secondary)' }}
                      onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                      onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                    >
                      {EXEC_MODES.find(m => m.value === execMode)?.icon}
                      {EXEC_MODES.find(m => m.value === execMode)?.label}
                      <ChevronDown className="w-3 h-3" style={{ opacity: 0.5 }} />
                    </button>
                    {showModePicker && (
                      <div
                        className="absolute bottom-full left-0 mb-1 w-64 rounded-lg shadow-xl overflow-hidden z-50"
                        style={{
                          backgroundColor: 'var(--bg-elevated)',
                          border: '1px solid var(--border-default)',
                        }}
                      >
                        {EXEC_MODES.map(m => (
                          <button
                            key={m.value}
                            onClick={() => { setExecMode(m.value); setShowModePicker(false) }}
                            className="w-full flex items-start gap-3 px-3 py-2.5 text-left transition"
                            style={{
                              backgroundColor: execMode === m.value ? 'var(--accent-subtle)' : 'transparent',
                            }}
                            onMouseEnter={e => {
                              if (execMode !== m.value) e.currentTarget.style.backgroundColor = 'var(--bg-hover)'
                            }}
                            onMouseLeave={e => {
                              if (execMode !== m.value) e.currentTarget.style.backgroundColor = 'transparent'
                            }}
                          >
                            <div
                              className="w-8 h-8 rounded flex items-center justify-center flex-shrink-0"
                              style={{
                                backgroundColor: execMode === m.value ? 'var(--accent)' : 'var(--bg-hover)',
                                color: execMode === m.value ? 'white' : 'var(--text-muted)',
                              }}
                            >
                              {m.icon}
                            </div>
                            <div className="min-w-0">
                              <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{m.label}</p>
                              <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{m.desc}</p>
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Model picker */}
                  <div className="relative" ref={modelPickerRef}>
                    <button
                      onClick={() => setShowModelPicker(p => !p)}
                      className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition whitespace-nowrap"
                      style={{ color: 'var(--text-secondary)' }}
                      onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                      onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                    >
                      <Cpu className="w-3.5 h-3.5" style={{ color: 'var(--accent)' }} />
                      <span className="truncate max-w-[120px]">{activeModelLabel}</span>
                      <ChevronDown className="w-3 h-3" style={{ opacity: 0.5 }} />
                    </button>
                    {showModelPicker && (
                      <div
                        className="absolute bottom-full right-0 mb-1 w-56 rounded-lg shadow-xl overflow-hidden z-50"
                        style={{
                          backgroundColor: 'var(--bg-elevated)',
                          border: '1px solid var(--border-default)',
                        }}
                      >
                        <div className="px-3 py-2 border-b border-subtle bg-hover/30">
                          <p className="text-[10px] font-bold uppercase tracking-wider text-muted">Select Model</p>
                        </div>
                        <div className="max-h-64 overflow-y-auto">
                          {modelList.map(m => (
                            <button
                              key={m.id}
                              onClick={() => switchModel(m.id)}
                              className="w-full flex items-center justify-between px-3 py-2 text-left transition"
                              onMouseEnter={e => e.currentTarget.style.backgroundColor = 'var(--bg-hover)'}
                              onMouseLeave={e => e.currentTarget.style.backgroundColor = 'transparent'}
                            >
                              <span className="text-xs truncate mr-2" style={{ color: m.active ? 'var(--accent)' : 'var(--text-primary)' }}>
                                {m.label}
                              </span>
                              {m.active && <Shield className="w-3 h-3" style={{ color: 'var(--accent)' }} />}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
