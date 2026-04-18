import { useState, useRef, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Mic, MicOff, Send, Cpu, Activity, RotateCcw, Eye, ChevronDown, Sparkles, FileText, Shield, ShieldCheck, Zap, Square, Code2, Copy, Check, ChevronUp, AlertTriangle, CheckCircle, Loader2, Bot, ChevronRight, Stethoscope } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useVoiceInput } from '../hooks/useVoiceInput'
import { useStreaming } from '../hooks/useStreaming'
import type { Message, Session } from '../lib/sessions'
import {
  getOrCreateActiveSession, getSession, saveSession,
  createSession, setActiveSessionId,
  autoTitle, bootstrapFromServer,
} from '../lib/sessions'
import { getSkillMeta } from '../lib/skills'
import ActivityBar, { type ActivityTab } from '../components/ActivityBar'
import Sidebar from '../components/Sidebar'
import WorkspaceTabs, { type WorkspaceTab } from '../components/WorkspaceTabs'
import SettingsPanel from '../components/SettingsPanel'
import { derivePhases } from '../components/FlowDashboard'
import { TaskStepStrip } from '../components/TaskSequence'
import FileTree from '../components/FileTree'
import StreamingMessage from '../components/StreamingMessage'
import ThinkingBlock from '../components/ThinkingBlock'
import TerminalTab from '../components/TerminalTab'
import AgentsTab from './Agents'
import CodeEditor from '../components/CodeEditor'
import PipelineCanvas from '../components/PipelineCanvas'

import { API } from '../lib/constants'

type MatlabUiStatus = 'checking' | 'online' | 'busy' | 'offline'

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
  const isHtml = url.endsWith('.html')
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
          {isHtml ? 'Interactive (HTML)' : isGif ? '\u25B6 Animated' : '\uD83D\uDCCA Plot'}
        </span>
      </div>
      {isHtml ? (
        <iframe
          title="Interactive plot"
          src={`${API}${url}`}
          className="w-full border-0 bg-white"
          style={{ height: '22rem', minHeight: '280px' }}
          sandbox="allow-scripts allow-same-origin"
        />
      ) : (
        <img
          src={`${API}${url}`}
          alt="Run output"
          className="w-full object-contain max-h-72"
          onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
        />
      )}
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

function ExecutionQualityRow({
  execution,
  budget,
}: {
  execution?: Record<string, unknown>
  budget?: Record<string, unknown>
}) {
  if (!execution && !budget) return null
  const runOk = execution?.run_success === true
  const specOk = execution?.spec_satisfied === true
  const violations = Array.isArray(execution?.violations) ? execution.violations as string[] : []
  const within = budget?.within_wall_budget !== false
  const wallAbort = budget?.wall_abort === true
  const costAbort = budget?.cost_abort === true
  const bms = typeof budget?.wall_clock_ms === 'number' ? budget.wall_clock_ms : null
  const cap = typeof budget?.wall_clock_budget_ms === 'number' ? budget.wall_clock_budget_ms : null
  const tok = typeof budget?.total_tokens === 'number' ? budget.total_tokens : null
  const est = typeof budget?.estimated_cost_usd === 'number' ? budget.estimated_cost_usd : null
  const border = specOk && runOk ? 'var(--success)22' : 'var(--warning)44'
  return (
    <div
      className="mt-2 px-3 py-2 rounded-lg text-xs space-y-1"
      style={{ backgroundColor: 'var(--bg-surface)', border: `1px solid ${border}` }}
    >
      <div className="font-medium" style={{ color: 'var(--text-primary)' }}>Run quality</div>
      {execution && (
        <div style={{ color: 'var(--text-muted)' }}>
          <span>Execution: </span>
          <span style={{ color: runOk ? 'var(--success)' : 'var(--error)' }}>{runOk ? 'ok' : 'failed'}</span>
          <span className="mx-1">·</span>
          <span>Spec: </span>
          <span style={{ color: specOk ? 'var(--success)' : 'var(--warning)' }}>{specOk ? 'satisfied' : 'issues'}</span>
        </div>
      )}
      {violations.length > 0 && (
        <ul className="list-disc list-inside" style={{ color: 'var(--text-muted)' }}>
          {violations.map((v, i) => <li key={i}>{v}</li>)}
        </ul>
      )}
      {budget && (
        <div style={{ color: 'var(--text-muted)' }} className="space-y-0.5">
          <div>
            Wall time
            {bms != null ? ` ${bms} ms` : ''}
            {cap != null ? ` / budget ${cap} ms` : ''}
            {!within && ' — over budget'}
            {wallAbort && ' (hard stop)'}
          </div>
          {(tok != null && tok > 0) && (
            <div>LLM tokens (prompt+completion): {tok}</div>
          )}
          {(est != null && est > 0) && (
            <div>
              Est. USD: {est.toFixed(4)}
              {costAbort && ' — soft cap stop'}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── SentryStatusChip ─────────────────────────────────────────────────────────

interface SentryStatusProps {
  status: {
    type: 'checking' | 'issue' | 'retrying' | 'done'
    message: string
    quality?: 'good' | 'poor'
    attempt?: number
    issues?: string[]
  }
}

function SentryStatusChip({ status }: SentryStatusProps) {
  const isGood = status.type === 'done' && status.quality === 'good'
  const isPoor = status.type === 'done' && status.quality === 'poor'
  const isIssue = status.type === 'issue'
  const isActive = status.type === 'checking' || status.type === 'retrying'

  const color = isGood ? 'var(--success)' : isPoor || isIssue ? 'var(--warning)' : 'var(--accent)'

  return (
    <div
      className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs mt-1"
      style={{ backgroundColor: 'var(--bg-surface)', border: `1px solid ${color}22`, color }}
    >
      <span className="flex-shrink-0 mt-0.5">
        {isGood && <CheckCircle className="w-3.5 h-3.5" />}
        {isPoor && <AlertTriangle className="w-3.5 h-3.5" />}
        {isIssue && <AlertTriangle className="w-3.5 h-3.5" />}
        {isActive && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
      </span>
      <div className="flex flex-col gap-0.5">
        <span className="font-medium">{status.message}</span>
        {status.issues && status.issues.length > 0 && (
          <ul className="list-disc list-inside" style={{ color: 'var(--text-muted)' }}>
            {status.issues.map((iss, i) => <li key={i}>{iss}</li>)}
          </ul>
        )}
      </div>
    </div>
  )
}

// ── AgentStepsPanel ───────────────────────────────────────────────────────────

interface AgentStepEntry {
  step: number
  tool: string
  label: string
  status: 'running' | 'done' | 'error'
  output?: string
  plots?: string[]
}

function AgentStepsPanel({ steps, isLive }: { steps: AgentStepEntry[]; isLive?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  if (!steps.length) return null

  const done   = steps.filter(s => s.status === 'done').length
  const errors = steps.filter(s => s.status === 'error').length
  const running = steps.find(s => s.status === 'running')

  return (
    <div
      className="rounded-xl overflow-hidden text-xs mt-1"
      style={{ border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-surface)', maxWidth: '520px' }}
    >
      {/* Summary header */}
      <button
        className="w-full flex items-center justify-between px-3 py-2 gap-2"
        style={{ backgroundColor: 'var(--bg-hover)' }}
        onClick={() => setExpanded(e => !e)}
      >
        <div className="flex items-center gap-2">
          <Bot className="w-3.5 h-3.5 flex-shrink-0" style={{ color: 'var(--accent)' }} />
          <span className="font-medium" style={{ color: 'var(--text-secondary)' }}>
            Agentic — {steps.length} step{steps.length !== 1 ? 's' : ''}
          </span>
          {isLive && running && (
            <span className="flex items-center gap-1" style={{ color: 'var(--accent)' }}>
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>{running.tool}…</span>
            </span>
          )}
          {!isLive && (
            <span style={{ color: errors ? 'var(--warning)' : 'var(--success)' }}>
              {errors ? `${errors} error${errors>1?'s':''}` : `${done} completed`}
            </span>
          )}
        </div>
        <ChevronRight
          className="w-3 h-3 transition-transform flex-shrink-0"
          style={{
            color: 'var(--text-muted)',
            transform: expanded ? 'rotate(90deg)' : 'none',
          }}
        />
      </button>

      {/* Step list */}
      {expanded && (
        <div className="divide-y" style={{ borderTop: '1px solid var(--border-subtle)' }}>
          {steps.map((s) => (
            <div key={`${s.step}-${s.tool}`} className="px-3 py-2 flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span
                  className="w-4 h-4 flex items-center justify-center flex-shrink-0"
                  style={{ color: s.status === 'done' ? 'var(--success)' : s.status === 'error' ? 'var(--warning)' : 'var(--accent)' }}
                >
                  {s.status === 'done'  && <CheckCircle className="w-3 h-3" />}
                  {s.status === 'error' && <AlertTriangle className="w-3 h-3" />}
                  {s.status === 'running' && <Loader2 className="w-3 h-3 animate-spin" />}
                </span>
                <span className="font-mono font-medium" style={{ color: 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--accent)' }}>{s.tool}</span>
                </span>
                <span style={{ color: 'var(--text-muted)' }} className="truncate">{s.label}</span>
              </div>
              {s.output && (
                <pre
                  className="px-2 py-1 rounded text-[10px] leading-relaxed overflow-x-auto"
                  style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-muted)', maxHeight: '80px', overflow: 'auto' }}
                >
                  {s.output.slice(0, 400)}{s.output.length > 400 ? '…' : ''}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── CodeDoctorPanel ───────────────────────────────────────────────────────────

interface DoctorLogEntry {
  type: 'start' | 'issue' | 'fix' | 'rerun' | 'done'
  round?: number
  severity?: string
  issueType?: string
  description?: string
  message?: string
  fixed?: boolean
  roundsTaken?: number
}

function CodeDoctorPanel({ log, isLive }: { log: DoctorLogEntry[]; isLive?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  if (!log.length) return null

  const done    = log.find(e => e.type === 'done')
  const issues  = log.filter(e => e.type === 'issue')
  const fixes   = log.filter(e => e.type === 'fix')
  const rerunning = isLive && log.some(e => e.type === 'rerun') && !done

  const statusColor = done
    ? (done.fixed ? 'var(--success)' : 'var(--warning)')
    : 'var(--accent)'

  return (
    <div
      className="rounded-xl overflow-hidden text-xs mt-1"
      style={{ border: `1px solid ${statusColor}33`, backgroundColor: 'var(--bg-surface)', maxWidth: '520px' }}
    >
      {/* Header */}
      <button
        className="w-full flex items-center justify-between px-3 py-2 gap-2"
        style={{ backgroundColor: 'var(--bg-hover)' }}
        onClick={() => setExpanded(e => !e)}
      >
        <div className="flex items-center gap-2">
          <Wrench2 className="w-3.5 h-3.5 flex-shrink-0" style={{ color: statusColor }} />
          <span className="font-medium" style={{ color: 'var(--text-secondary)' }}>CodeDoctor</span>
          {rerunning && (
            <span className="flex items-center gap-1" style={{ color: 'var(--accent)' }}>
              <Loader2 className="w-3 h-3 animate-spin" /> re-running…
            </span>
          )}
          {done && (
            <span style={{ color: statusColor }}>
              {done.fixed ? `✓ Fixed in ${done.roundsTaken ?? '?'} round(s)` : '⚠ Manual review needed'}
            </span>
          )}
          {!done && !rerunning && (
            <span style={{ color: 'var(--accent)' }}>
              {issues.length} issue(s) · {fixes.length} fix(es) applied
            </span>
          )}
        </div>
        <ChevronRight
          className="w-3 h-3 transition-transform flex-shrink-0"
          style={{ color: 'var(--text-muted)', transform: expanded ? 'rotate(90deg)' : 'none' }}
        />
      </button>

      {/* Log entries */}
      {expanded && (
        <div className="divide-y" style={{ borderTop: `1px solid ${statusColor}22` }}>
          {log.map((entry, idx) => {
            const icon = entry.type === 'issue'
              ? <AlertTriangle className="w-3 h-3 flex-shrink-0" style={{ color: entry.severity === 'critical' ? 'var(--error)' : 'var(--warning)' }} />
              : entry.type === 'fix'
              ? <CheckCircle className="w-3 h-3 flex-shrink-0" style={{ color: 'var(--success)' }} />
              : entry.type === 'rerun'
              ? <Loader2 className="w-3 h-3 flex-shrink-0 animate-spin" style={{ color: 'var(--accent)' }} />
              : entry.type === 'done'
              ? (entry.fixed
                  ? <CheckCircle className="w-3 h-3 flex-shrink-0" style={{ color: 'var(--success)' }} />
                  : <AlertTriangle className="w-3 h-3 flex-shrink-0" style={{ color: 'var(--warning)' }} />)
              : <Bot className="w-3 h-3 flex-shrink-0" style={{ color: 'var(--accent)' }} />

            const label = entry.type === 'issue'
              ? `[${entry.severity?.toUpperCase()}] ${entry.issueType}: ${entry.description}`
              : entry.description || entry.message || entry.type

            return (
              <div key={idx} className="flex items-start gap-2 px-3 py-1.5">
                <span className="mt-0.5">{icon}</span>
                <span style={{ color: 'var(--text-muted)', lineHeight: 1.5 }}>{label}</span>
                {entry.round && (
                  <span className="ml-auto flex-shrink-0 opacity-50">r{entry.round}</span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// Minimal wrench icon (lucide doesn't have Wrench2)
function Wrench2({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <Bot className={className} style={style} />
}

// ── CodePreviewCard ───────────────────────────────────────────────────────────

interface CodePreviewCardProps {
  code: string
  filename: string
  onOpenInEditor: (code: string, filename: string) => void
}

function CodePreviewCard({ code, filename, onOpenInEditor }: CodePreviewCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
  const allLines = code.split('\n')
  const previewLines = allLines.slice(0, 4)
  const hasMore = allLines.length > 4

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  return (
    <div
      className="rounded-xl overflow-hidden text-xs font-mono mt-1"
      style={{
        border: '1px solid var(--border-subtle)',
        backgroundColor: 'var(--bg-surface)',
        maxWidth: '520px',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 gap-2"
        style={{ borderBottom: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-hover)' }}
      >
        <div className="flex items-center gap-1.5 min-w-0">
          <Code2 className="w-3 h-3 flex-shrink-0" style={{ color: 'var(--accent)' }} />
          <span className="truncate" style={{ color: 'var(--text-secondary)' }}>{filename}</span>
          <span className="flex-shrink-0" style={{ color: 'var(--text-muted)' }}>· {allLines.length} lines</span>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={handleCopy}
            className="flex items-center gap-1 px-1.5 py-1 rounded transition-colors"
            style={{ color: copied ? 'var(--accent)' : 'var(--text-muted)' }}
            title="Copy code"
            onMouseEnter={e => { if (!copied) e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={e => { if (!copied) e.currentTarget.style.color = 'var(--text-muted)' }}
          >
            {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
          </button>
          <button
            onClick={() => onOpenInEditor(code, filename)}
            className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium transition-all"
            style={{
              backgroundColor: 'var(--accent)',
              color: 'var(--bg-base)',
            }}
            title="Open in Editor tab to edit and re-run"
          >
            <Code2 className="w-2.5 h-2.5" />
            Edit &amp; Run
          </button>
        </div>
      </div>

      {/* Code body */}
      <div
        className="overflow-hidden"
        style={{ maxHeight: expanded ? '320px' : 'none', overflowY: expanded ? 'auto' : 'hidden' }}
      >
        <pre
          className="px-3 py-2.5 text-[11px] leading-relaxed overflow-x-auto"
          style={{ color: 'var(--text-secondary)', margin: 0, tabSize: 2 }}
        >
          {expanded
            ? code
            : previewLines.join('\n') + (hasMore ? '\n…' : '')}
        </pre>
      </div>

      {/* Expand / Collapse toggle */}
      {hasMore && (
        <button
          onClick={() => setExpanded(e => !e)}
          className="w-full flex items-center justify-center gap-1 py-1.5 text-[10px] transition-colors"
          style={{
            borderTop: '1px solid var(--border-subtle)',
            color: 'var(--text-muted)',
            backgroundColor: 'var(--bg-hover)',
          }}
          onMouseEnter={e => e.currentTarget.style.color = 'var(--accent)'}
          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
        >
          {expanded
            ? <><ChevronUp className="w-3 h-3" /> Collapse</>
            : <><ChevronDown className="w-3 h-3" /> Show all {allLines.length} lines</>}
        </button>
      )}
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

  const [sentryMode, setSentryMode] = useState(() => {
    return localStorage.getItem('matclaw-sentry-mode') === 'true'
  })

  const toggleSentryMode = useCallback(() => {
    setSentryMode(prev => {
      const next = !prev
      localStorage.setItem('matclaw-sentry-mode', String(next))
      return next
    })
  }, [])

  const [doctorMode, setDoctorMode] = useState(() => {
    return localStorage.getItem('matclaw-doctor-mode') === 'true'
  })

  const toggleDoctorMode = useCallback(() => {
    setDoctorMode(prev => {
      const next = !prev
      localStorage.setItem('matclaw-doctor-mode', String(next))
      return next
    })
  }, [])

  // ── active model + model list ────────────────────────────────
  const [activeModelLabel, setActiveModelLabel] = useState('Loading...')
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
    return () => document.removeEventListener('mousedown', handler)
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
    else if (tab === 'pipeline') setActivityTab('chat')
    else setActivityTab('chat')
  }

  // ── editor state ───────────────────────────────────────────────
  const [editorFiles, setEditorFiles] = useState<import('../lib/sessions').ProjectFile[]>([])
  const openInEditor = useCallback((file: import('../lib/sessions').ProjectFile) => {
    setEditorFiles(prev => prev.some(f => f.filename === file.filename) ? prev : [...prev, file])
    setWorkspaceTab('editor')
  }, [])

  // Open generated MATLAB code directly in editor tab
  const openCodeInEditor = useCallback((code: string, filename: string) => {
    const file: import('../lib/sessions').ProjectFile = {
      path: filename, filename, language: 'matlab', content: code, url: '',
    }
    openInEditor(file)
  }, [openInEditor])

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
  const [matlabStatus, setMatlabStatus] = useState<MatlabUiStatus>('checking')
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-grow textarea with content
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`
  }, [input])

  const { isListening, transcript, isSupported, startListening, stopListening, resetTranscript } = useVoiceInput()

  useEffect(() => { if (transcript) setInput(transcript) }, [transcript])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, loading])

  // health check — 8s client timeout; matlab_busy distinguishes long runs from disconnects
  const checkHealth = useCallback(() => {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 8000)

    fetch(`${API}/health`, { signal: controller.signal })
      .then(r => r.json())
      .then((d: {
        matlab?: boolean | { healthy?: boolean; busy?: boolean }
        matlab_busy?: boolean
      }) => {
        clearTimeout(timeoutId)
        const raw = d.matlab
        let connected = false
        let busy = false
        if (typeof raw === 'boolean') {
          connected = raw
          busy = !!d.matlab_busy
        } else if (raw && typeof raw === 'object') {
          connected = !!raw.healthy
          busy = !!raw.busy
        }
        if (!connected) setMatlabStatus('offline')
        else if (busy) setMatlabStatus('busy')
        else setMatlabStatus('online')
      })
      .catch(() => {
        clearTimeout(timeoutId)
        // If a request is in flight, backend may be busy with MATLAB — show 'busy' not 'offline'
        setMatlabStatus(prev => (prev === 'online' || prev === 'busy') ? 'busy' : 'offline')
      })
  }, [])

  const reconnectMatlab = useCallback(async () => {
    setMatlabStatus('checking')
    try { await fetch(`${API}/api/connect`, { method: 'POST' }) } catch (err) { console.error('Failed to reconnect MATLAB:', err) }
    checkHealth()
  }, [checkHealth])

  useEffect(() => {
    checkHealth()
    const intervalMs = loading ? 3000 : 10000
    const id = setInterval(checkHealth, intervalMs)
    return () => clearInterval(id)
  }, [checkHealth, loading])

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

  useEffect(() => {
    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = 0
      }
    }
  }, [])

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

  // ── stream execution (chat send or editor "Run" with optional force_runtime) ──
  const executeStream = useCallback(async (params: {
    apiText: string
    userDisplay: string
    forceRuntime?: string
  }) => {
    const { apiText, userDisplay, forceRuntime } = params
    if (!apiText || loading) return
    stopListening()
    resetTranscript()

    const userMsg: Message = { id: crypto.randomUUID(), role: 'user', text: userDisplay, ts: Date.now() }
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
      await streamRun(apiText, session.id, history, {
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
          // Follow Mode: only switch tabs when the visible surface matches the tool.
          // MATLAB/Python run on the server bridge/runtimes — output streams in Flow, not the PTY Terminal tab.
          if (followMode && data.action === 'run_shell') {
            setWorkspaceTab('terminal')
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
          // Capture code + store plots/files. Reply text comes via onDone.
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            const existing = prev[idx]
            // Generate a timestamped filename for the editor tab
            const codeFilename = data.code
              ? (existing.codeFilename || `matlab_${Date.now()}.m`)
              : existing.codeFilename
            return [...prev.slice(0, idx), {
              ...existing,
              plots: [...(existing.plots || []), ...(data.plots || [])],
              files: [...(existing.files || []), ...(data.files || [])],
              ...(data.code ? { code: data.code, codeFilename } : {}),
            }, ...prev.slice(idx + 1)]
          })
        },
        onDone: (data) => {
          // Follow Mode: return to flow when task completes
          if (followMode) {
            setWorkspaceTab('flow')
          }
          // Prefer done.reply (canonical); strip raw JSON wrapper if parser leaked it
          const safeReply = (() => {
            const r = data.reply || pendingRef.current.text || 'Done.'
            const trimmed = r.trimStart()
            if (trimmed.startsWith('{')) {
              try {
                const parsed = JSON.parse(trimmed)
                return parsed.reply || r
              } catch {
                // Partial JSON — strip everything from first { onwards as fallback
                const before = r.indexOf('{')
                return before > 0 ? r.slice(0, before).trim() : 'Done.'
              }
            }
            return r
          })()
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            return [...prev.slice(0, idx), {
              ...prev[idx],
              text: safeReply,
              thinking: pendingRef.current.thinking,
              skill: data.skill,
              elapsed_ms: data.elapsed_ms,
              plots: data.plots?.length ? data.plots : (prev[idx].plots || []),
              files: data.files?.length ? data.files : (prev[idx].files || []),
              streamingPhase: 'done' as const,
              ...(data.execution ? { executionSummary: data.execution as Record<string, unknown> } : {}),
              ...(data.budget ? { budgetSummary: data.budget as Record<string, unknown> } : {}),
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
        onSentryUpdate: (data) => {
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            const typeMap = { start: 'checking', issue: 'issue', done: 'done' } as const
            const mappedType = typeMap[data.type] ?? 'checking'
            return [...prev.slice(0, idx), {
              ...prev[idx],
              sentryStatus: {
                type: mappedType as 'checking' | 'issue' | 'retrying' | 'done',
                message: data.message,
                quality: data.quality,
                attempt: data.attempt,
                issues: data.issues,
              },
            }, ...prev.slice(idx + 1)]
          })
        },
        onAgentStep: (data) => {
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            const existing = prev[idx]
            const steps = [...(existing.agentSteps || [])]
            // mark any previous step as done if not already
            const updated = steps.map(s => s.status === 'running' ? { ...s, status: 'done' as const } : s)
            updated.push({ step: data.step, tool: data.tool, label: data.label, status: 'running' as const })
            return [...prev.slice(0, idx), { ...existing, agentSteps: updated }, ...prev.slice(idx + 1)]
          })
        },
        onAgentResult: (data) => {
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            const existing = prev[idx]
            const steps = (existing.agentSteps || []).map(s =>
              s.step === data.step && s.tool === data.tool
                ? {
                    ...s,
                    status: (data.success ? 'done' : 'error') as 'done' | 'error',
                    output: data.output,
                    plots: data.plots,
                    ...(data.quality ? { quality: data.quality as Record<string, unknown> } : {}),
                  }
                : s
            )
            return [...prev.slice(0, idx), { ...existing, agentSteps: steps }, ...prev.slice(idx + 1)]
          })
        },
        onAgentThought: (_step, text) => {
          // Agent thoughts go into thinking panel, not the main text
          pendingRef.current.thinking += (pendingRef.current.thinking ? '\n\n' : '') + text
          flushPending()
        },
        onDoctorEvent: (data) => {
          setMsgs(prev => {
            const idx = prev.findIndex(m => m.id === asstId)
            if (idx === -1) return prev
            const existing = prev[idx]
            const log = [...(existing.doctorLog || []), data]
            return [...prev.slice(0, idx), { ...existing, doctorLog: log }, ...prev.slice(idx + 1)]
          })
        },
      }, execMode, sentryMode, doctorMode, forceRuntime ?? null)
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
  }, [loading, session.id, msgs, stopListening, resetTranscript, setMsgs, streamRun, flushPending, followMode, execMode, sentryMode, doctorMode])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    await executeStream({ apiText: text, userDisplay: text })
  }, [input, loading, executeStream])

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

  const taskCount = msgs.length

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
        ) : workspaceTab === 'pipeline' ? (
          <PipelineCanvas />
        ) : workspaceTab === 'terminal' ? (
          <TerminalTab />
        ) : workspaceTab === 'editor' ? (
          <CodeEditor
            files={editorFiles}
            onRunMatlab={(code, filename) => {
              setWorkspaceTab('flow')
              void executeStream({
                apiText: code,
                userDisplay: `Run \`${filename}\` in MATLAB (direct)`,
                forceRuntime: 'matlab',
              })
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
                  onClick={matlabStatus === 'offline' ? reconnectMatlab : undefined}
                  className="flex items-center gap-1.5 text-xs transition-colors"
                  style={{ cursor: matlabStatus === 'offline' ? 'pointer' : 'default' }}
                  title={
                    matlabStatus === 'busy'
                      ? 'MATLAB is running code (engine or batch job)'
                      : matlabStatus === 'online'
                        ? 'MATLAB session connected'
                        : matlabStatus === 'offline'
                          ? 'Click to reconnect'
                          : 'Checking MATLAB…'
                  }
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${matlabStatus === 'checking' ? 'animate-pulse' : ''}`}
                    style={{
                      backgroundColor: matlabStatus === 'online' ? 'var(--success)' :
                        matlabStatus === 'offline' ? 'var(--error)' :
                          matlabStatus === 'busy' ? 'var(--warning)' : 'var(--warning)'
                    }}
                  />
                  <span style={{
                    color: matlabStatus === 'offline' ? 'var(--error)' : 'var(--text-muted)'
                  }}>
                    MATLAB {matlabStatus === 'online' ? 'online' : matlabStatus === 'offline' ? 'offline' : matlabStatus === 'busy' ? 'busy' : 'checking…'}
                  </span>
                </button>
                {/* Follow Mode — compact toggle in header */}
                <button
                  onClick={toggleFollowMode}
                  className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-all"
                  title={followMode ? 'Follow Mode: ON — view auto-switches to active tool' : 'Follow Mode: OFF'}
                  style={{
                    backgroundColor: followMode ? 'var(--accent-subtle)' : 'transparent',
                    color: followMode ? 'var(--accent)' : 'var(--text-muted)',
                    border: `1px solid ${followMode ? 'color-mix(in srgb, var(--accent) 35%, transparent)' : 'transparent'}`,
                  }}
                >
                  <Eye className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Follow</span>
                </button>
                {/* Code Doctor — extra MATLAB/LLM fix-up passes (off by default; can feel “stuck”) */}
                <button
                  onClick={toggleDoctorMode}
                  className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-all"
                  title={doctorMode ? 'Code Doctor: ON — auto-debug retries after MATLAB (slower)' : 'Code Doctor: OFF — recommended for faster plots'}
                  style={{
                    backgroundColor: doctorMode ? 'color-mix(in srgb, var(--accent) 12%, transparent)' : 'transparent',
                    color: doctorMode ? 'var(--accent)' : 'var(--text-muted)',
                    border: `1px solid ${doctorMode ? 'color-mix(in srgb, var(--accent) 30%, transparent)' : 'transparent'}`,
                  }}
                >
                  <Stethoscope className="w-3.5 h-3.5" />
                  <span className="hidden sm:inline">Doctor</span>
                </button>
                {/* Sentry Mode — auto-check result quality and retry */}
                <button
                  onClick={toggleSentryMode}
                  className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-all"
                  title={sentryMode ? 'Sentry Mode: ON — auto-checks result quality and retries on failure' : 'Sentry Mode: OFF — click to enable quality checks'}
                  style={{
                    backgroundColor: sentryMode ? 'color-mix(in srgb, var(--success) 15%, transparent)' : 'transparent',
                    color: sentryMode ? 'var(--success)' : 'var(--text-muted)',
                    border: `1px solid ${sentryMode ? 'color-mix(in srgb, var(--success) 35%, transparent)' : 'transparent'}`,
                  }}
                >
                  {sentryMode ? <ShieldCheck className="w-3.5 h-3.5" /> : <Shield className="w-3.5 h-3.5" />}
                  <span className="hidden sm:inline">Sentry</span>
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
            {/* FlowDashboard removed — step status now shown inline in each message */}

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
                  // Derive step phases for the inline strip (for assistant messages only)
                  const stepPhases = m.role === 'assistant'
                    ? derivePhases(m, isStreaming ? pendingRef.current.toolLabel : undefined)
                    : []
                  const hasSteps = stepPhases.length > 0

                  return (
                    <motion.div
                      key={m.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.2 }}
                      className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
                    >
                      <div className={`max-w-2xl flex flex-col gap-1.5 ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
                        {m.skill && m.role === 'assistant' && !isStreaming && <SkillBadge skill={m.skill} />}

                        {/* ── Inline step strip (Option A) — replaces FlowDashboard card ── */}
                        {hasSteps && (
                          <TaskStepStrip phases={stepPhases} isLive={!!isStreaming} />
                        )}

                        {/* ── Agentic steps panel ── */}
                        {m.role === 'assistant' && m.agentSteps && m.agentSteps.length > 0 && (
                          <AgentStepsPanel steps={m.agentSteps} isLive={!!isStreaming} />
                        )}

                        {/* ── CodeDoctor panel ── */}
                        {m.role === 'assistant' && m.doctorLog && m.doctorLog.length > 0 && (
                          <CodeDoctorPanel log={m.doctorLog} isLive={!!isStreaming} />
                        )}

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
                        {/* Sentry status chip */}
                        {m.role === 'assistant' && m.sentryStatus && (
                          <SentryStatusChip status={m.sentryStatus} />
                        )}
                        {/* Code preview card — shown when MATLAB code was executed */}
                        {m.role === 'assistant' && m.code && m.codeFilename && !isStreaming && (
                          <CodePreviewCard
                            code={m.code}
                            filename={m.codeFilename}
                            onOpenInEditor={openCodeInEditor}
                          />
                        )}
                        {!isStreaming && <MetricsRow elapsed_ms={m.elapsed_ms} />}
                        {!isStreaming && (m.executionSummary || m.budgetSummary) && (
                          <ExecutionQualityRow execution={m.executionSummary} budget={m.budgetSummary} />
                        )}
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
              {/* Agentic mode banner — clearly visible above input */}
              {execMode === 'agentic' && (
                <div
                  className="flex items-center gap-2 px-3 py-1.5 rounded-xl mb-1.5 text-xs font-medium"
                  style={{
                    backgroundColor: 'var(--accent-subtle)',
                    border: '1px solid color-mix(in srgb, var(--accent) 35%, transparent)',
                    color: 'var(--accent)',
                  }}
                >
                  <Sparkles className="w-3.5 h-3.5 flex-shrink-0" />
                  <span>Agentic mode — MatClaw will use tools autonomously to complete your request</span>
                  <button
                    className="ml-auto text-[10px] opacity-60 hover:opacity-100 transition-opacity"
                    onClick={() => setExecMode('auto')}
                  >
                    Switch to Auto
                  </button>
                </div>
              )}
              <div
                className="rounded-2xl px-3 py-2 transition-all"
                style={{
                  backgroundColor: 'var(--bg-input)',
                  border: `1px solid ${execMode === 'agentic' ? 'color-mix(in srgb, var(--accent) 50%, var(--border-default))' : 'var(--border-default)'}`,
                  boxShadow: execMode === 'agentic' ? '0 0 0 2px color-mix(in srgb, var(--accent) 10%, transparent)' : 'none',
                }}
              >
                {/* Textarea + send row */}
                <div className="flex items-end gap-2">
                  <textarea
                    ref={textareaRef}
                    rows={1}
                    className="flex-1 bg-transparent resize-none outline-none text-sm leading-relaxed"
                    style={{
                      color: 'var(--text-primary)',
                      fontFamily: 'var(--font-ui)',
                      minHeight: '1.5rem',
                      maxHeight: '200px',
                      overflowY: 'auto',
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
                      className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold transition-all"
                      style={{
                        color: execMode === 'agentic' ? 'var(--accent)' : 'var(--text-secondary)',
                        backgroundColor: execMode === 'agentic' ? 'var(--accent-subtle)' : 'transparent',
                        border: `1px solid ${execMode === 'agentic' ? 'color-mix(in srgb, var(--accent) 40%, transparent)' : 'transparent'}`,
                        boxShadow: execMode === 'agentic' ? '0 0 8px color-mix(in srgb, var(--accent) 25%, transparent)' : 'none',
                      }}
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
                      onClick={() => { setShowModelPicker(p => !p); fetchModels() }}
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
