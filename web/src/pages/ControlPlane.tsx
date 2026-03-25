import { useState, useRef, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Mic, MicOff, Send, Cpu, Activity, RotateCcw, Eye } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useVoiceInput } from '../hooks/useVoiceInput'
import { getSkillMeta } from '../lib/skills'
import type { Message, Session } from '../lib/sessions'
import {
  getOrCreateActiveSession, getSession, saveSession,
  createSession, setActiveSessionId,
  autoTitle,
} from '../lib/sessions'
import Sidebar from '../components/Sidebar'

const API = 'http://localhost:8000'

// ── Sub-components ───────────────────────────────────────────────────────────

function SkillBadge({ skill }: { skill: string }) {
  const meta = getSkillMeta(skill)
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${meta.color} ${meta.bgColor}`}>
      {meta.icon} {meta.label}
    </span>
  )
}

function PlotCard({ url }: { url: string }) {
  const isGif = url.endsWith('.gif')
  return (
    <div className="mt-2 rounded-lg overflow-hidden border border-white/10 bg-black/30 max-w-md">
      <div className="px-3 py-1.5 flex items-center gap-2 border-b border-white/10 bg-white/5">
        <Eye className="w-3 h-3 text-cyan-400" />
        <span className="text-xs text-zinc-400">{isGif ? '▶ Animated' : '📊 Plot'}</span>
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
      <Activity className="w-3 h-3 text-zinc-500" />
      <span className="text-xs text-zinc-500">{elapsed_ms}ms</span>
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

export default function ControlPlane() {
  const navigate = useNavigate()

  // ── session state ──────────────────────────────────────────────
  const [session, setSession] = useState<Session>(() => getOrCreateActiveSession())
  const [sidebarRefresh, setSidebarRefresh] = useState(0)

  // messages live inside the session object
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

  // sync voice transcript → input
  useEffect(() => { if (transcript) setInput(transcript) }, [transcript])

  // scroll to bottom
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, loading])

  // health check
  const checkHealth = useCallback(() => {
    fetch(`${API}/health`)
      .then(r => r.json())
      .then(d => setMatlabOnline(d.matlab))
      .catch(() => setMatlabOnline(false))
  }, [])

  const reconnectMatlab = useCallback(async () => {
    setMatlabOnline(null)
    try { await fetch(`${API}/api/connect`, { method: 'POST' }) } catch {}
    checkHealth()
  }, [checkHealth])

  useEffect(() => {
    checkHealth()
    const id = setInterval(checkHealth, 10000)
    return () => clearInterval(id)
  }, [checkHealth])

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

  // ── send ───────────────────────────────────────────────────────
  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || loading) return
    stopListening()
    resetTranscript()
    setInput('')

    const userMsg: Message = { id: crypto.randomUUID(), role: 'user', text, ts: Date.now() }
    setMsgs(prev => [...prev, userMsg])
    setLoading(true)

    try {
      const res = await fetch(`${API}/api/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, session_id: session.id }),
      })
      const data = await res.json()
      const asstMsg: Message = {
        id: crypto.randomUUID(),
        role: 'assistant',
        text: data.output || 'No response generated.',
        skill: data.skill,
        plots: data.plots || [],
        metrics: data.metrics || {},
        elapsed_ms: data.elapsed_ms,
        ts: Date.now(),
      }
      setMsgs(prev => [...prev, asstMsg])
    } catch (err) {
      setMsgs(prev => [...prev, {
        id: crypto.randomUUID(), role: 'assistant',
        text: `Connection error: ${err}`, ts: Date.now(),
      }])
    } finally {
      setLoading(false)
    }
  }, [input, loading, session.id, stopListening, resetTranscript, setMsgs])

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  // ── render ─────────────────────────────────────────────────────
  return (
    <div className="h-screen bg-[#0d0d14] text-zinc-100 flex overflow-hidden">

      {/* ── sidebar ──────────────────────────────────────────────── */}
      <Sidebar
        activeSessionId={session.id}
        onSelectSession={handleSelectSession}
        onNewSession={handleNewSession}
        onNavigateVision={() => navigate('/vision')}
        refreshTrigger={sidebarRefresh}
      />

      {/* ── main panel ───────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* header */}
        <header className="flex items-center justify-between px-5 py-3 border-b border-white/8 bg-[#10101a]/80 backdrop-blur flex-shrink-0">
          <div className="flex items-center gap-2">
            <Cpu className="w-4 h-4 text-cyan-400" />
            <span className="text-sm font-semibold truncate max-w-xs text-zinc-200">
              {session.title === 'New Session' ? 'Control Plane' : session.title}
            </span>
          </div>
          <div className="flex items-center gap-3">
            {/* MATLAB status */}
            <button
              onClick={matlabOnline === false ? reconnectMatlab : undefined}
              title={matlabOnline === false ? 'Click to reconnect MATLAB' : undefined}
              className={`flex items-center gap-1.5 text-xs transition-colors ${matlabOnline === false ? 'cursor-pointer hover:text-yellow-300' : 'cursor-default'}`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${
                matlabOnline === true ? 'bg-green-400' :
                matlabOnline === false ? 'bg-red-400' : 'bg-yellow-400 animate-pulse'
              }`} />
              <span className={matlabOnline === true ? 'text-zinc-400' : matlabOnline === false ? 'text-red-400' : 'text-zinc-400'}>
                MATLAB {matlabOnline === true ? 'online' : matlabOnline === false ? 'offline' : 'checking…'}
              </span>
            </button>
            {/* clear session */}
            <button
              onClick={() => setMsgs(() => [])}
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
              title="Clear messages"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>
          </div>
        </header>

        {/* messages */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4 scroll-smooth">
          <AnimatePresence initial={false}>
            {msgs.length === 0 && (
              <motion.div
                key="empty"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex flex-col items-center justify-center h-64 gap-3 text-zinc-600"
              >
                <Cpu className="w-8 h-8 opacity-30" />
                <p className="text-sm">Ask MatClaw anything about your MATLAB workspace</p>
                <div className="flex flex-wrap justify-center gap-2 mt-2 max-w-lg">
                  {[
                    'Plot a 3D surface of sin(x)·cos(y)',
                    'Animate the Lorenz attractor',
                    'Tune my PID controller',
                    'What did I run recently?',
                  ].map(s => (
                    <button
                      key={s}
                      onClick={() => setInput(s)}
                      className="text-xs px-3 py-1.5 rounded-full border border-white/10 hover:border-cyan-400/30 hover:text-cyan-400 transition-colors"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </motion.div>
            )}

            {msgs.map(m => (
              <motion.div
                key={m.id}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2 }}
                className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div className={`max-w-2xl flex flex-col gap-1 ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
                  {m.skill && m.role === 'assistant' && <SkillBadge skill={m.skill} />}
                  <div className={`px-4 py-2.5 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap break-words ${
                    m.role === 'user'
                      ? 'bg-cyan-500/15 border border-cyan-500/20 text-zinc-100 rounded-tr-sm'
                      : 'bg-white/5 border border-white/8 text-zinc-200 rounded-tl-sm'
                  }`}>
                    {m.text}
                  </div>
                  {m.plots?.map(url => <PlotCard key={url} url={url} />)}
                  <MetricsRow elapsed_ms={m.elapsed_ms} />
                </div>
              </motion.div>
            ))}

            {loading && (
              <motion.div key="loading" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex justify-start">
                <div className="bg-white/5 border border-white/8 rounded-2xl rounded-tl-sm px-4 py-3 flex gap-1.5 items-center">
                  {[0, 1, 2].map(i => (
                    <motion.span key={i} className="w-1.5 h-1.5 bg-cyan-400 rounded-full"
                      animate={{ y: [0, -4, 0] }}
                      transition={{ repeat: Infinity, duration: 0.8, delay: i * 0.15 }} />
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
          <div ref={bottomRef} />
        </div>

        {/* voice indicator */}
        <AnimatePresence>
          {isListening && (
            <motion.div
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}
              className="flex items-center justify-center gap-3 py-2 bg-cyan-400/5 border-t border-cyan-400/20"
            >
              <div className="flex gap-0.5 items-end h-4">
                {[3, 6, 4, 7, 3].map((h, i) => (
                  <motion.div key={i} className="w-1 bg-cyan-400 rounded-full"
                    animate={{ height: [`${h}px`, `${h * 2.5}px`, `${h}px`] }}
                    transition={{ repeat: Infinity, duration: 0.6, delay: i * 0.1 }} />
                ))}
              </div>
              <span className="text-xs text-cyan-400 font-medium">Listening…</span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* input bar */}
        <div className="px-5 pb-4 pt-2 flex-shrink-0 border-t border-white/5">
          <div className="flex items-end gap-2 bg-white/5 border border-white/10 rounded-2xl px-3 py-2 focus-within:border-cyan-400/30 transition-colors">
            <textarea
              rows={1}
              className="flex-1 bg-transparent resize-none outline-none text-sm text-zinc-100 placeholder:text-zinc-600 max-h-32 leading-relaxed"
              placeholder="Ask MatClaw… (Shift+Enter for newline)"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onKey}
            />
            <button
              onClick={isListening ? stopListening : startListening}
              disabled={!isSupported}
              title={!isSupported ? 'Voice not supported' : isListening ? 'Stop' : 'Voice input'}
              className={`p-1.5 rounded-lg transition-all ${
                !isSupported ? 'opacity-30 cursor-not-allowed text-zinc-600' :
                isListening ? 'text-cyan-400 bg-cyan-400/10 border border-cyan-400/40 animate-pulse' :
                'text-zinc-400 hover:text-zinc-200 border border-transparent hover:border-white/10'
              }`}
            >
              {isListening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
            </button>
            <button
              onClick={send}
              disabled={!input.trim() || loading}
              className="p-1.5 rounded-lg bg-cyan-500/20 border border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
          <p className="text-center text-xs text-zinc-600 mt-1.5">
            Powered by NVIDIA Nemotron 3 · 120B · 1M context
          </p>
        </div>
      </div>
    </div>
  )
}
