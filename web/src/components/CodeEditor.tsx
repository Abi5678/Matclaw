import { useState, useCallback, useEffect } from 'react'
import Editor from '@monaco-editor/react'
import { Play, Save, FileCode2, Plus, X } from 'lucide-react'
import type { ProjectFile } from '../lib/sessions'

const API = 'http://localhost:8000'

interface EditorTab {
  file: ProjectFile
  content: string
  dirty: boolean
}

interface CodeEditorProps {
  /** Files pre-loaded from the chat workspace (project-gen output) */
  files?: ProjectFile[]
  /** Called when user runs a MATLAB file */
  onRunMatlab?: (code: string, filename: string) => void
}

export default function CodeEditor({ files = [], onRunMatlab }: CodeEditorProps) {
  const [tabs, setTabs] = useState<EditorTab[]>(() =>
    files.map(f => ({ file: f, content: f.content, dirty: false }))
  )
  const [activeTab, setActiveTab] = useState<string | null>(
    files.length > 0 ? files[0].filename : null
  )
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)

  const currentTab = tabs.find(t => t.file.filename === activeTab) ?? null

  // ── sync new files added dynamically from outside (e.g. "Open in Editor") ─
  useEffect(() => {
    if (!files.length) return
    setTabs(prev => {
      const existing = new Set(prev.map(t => t.file.filename))
      const added = files.filter(f => !existing.has(f.filename))
      if (!added.length) return prev
      return [...prev, ...added.map(f => ({ file: f, content: f.content, dirty: false }))]
    })
    // Auto-focus the last added file
    setActiveTab(files[files.length - 1].filename)
  }, [files])

  // ── close a tab ─────────────────────────────────────────────────────────
  const closeTab = useCallback((filename: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setTabs(prev => {
      const next = prev.filter(t => t.file.filename !== filename)
      if (activeTab === filename) {
        setActiveTab(next.length > 0 ? next[next.length - 1].file.filename : null)
      }
      return next
    })
  }, [activeTab])

  // ── edit handler ─────────────────────────────────────────────────────────
  const handleChange = useCallback((value: string | undefined) => {
    if (value === undefined || !activeTab) return
    setTabs(prev => prev.map(t =>
      t.file.filename === activeTab
        ? { ...t, content: value, dirty: true }
        : t
    ))
  }, [activeTab])

  // ── save to backend ──────────────────────────────────────────────────────
  const handleSave = useCallback(async () => {
    if (!currentTab) return
    setSaving(true)
    try {
      await fetch(`${API}/projects/${currentTab.file.path}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'text/plain' },
        body: currentTab.content,
      })
      setTabs(prev => prev.map(t =>
        t.file.filename === activeTab ? { ...t, dirty: false } : t
      ))
    } catch {
      // Save failed silently — content stays in editor
    } finally {
      setSaving(false)
    }
  }, [currentTab, activeTab])

  // ── run MATLAB ────────────────────────────────────────────────────────────
  const handleRun = useCallback(() => {
    if (!currentTab || !onRunMatlab) return
    setRunning(true)
    onRunMatlab(currentTab.content, currentTab.file.filename)
    setTimeout(() => setRunning(false), 1500)
  }, [currentTab, onRunMatlab])

  const isMatlab = currentTab?.file.language === 'matlab'
  const langMap: Record<string, string> = {
    matlab: 'matlab', python: 'python', typescript: 'typescript',
    javascript: 'javascript', html: 'html', markdown: 'markdown',
  }
  const monacoLang = langMap[currentTab?.file.language ?? ''] ?? 'plaintext'

  // ── new scratch file ──────────────────────────────────────────────────────
  const handleNewFile = useCallback(() => {
    const filename = `scratch_${Date.now()}.m`
    const file: ProjectFile = {
      path: filename, filename, language: 'matlab',
      content: '% New MATLAB script\n', url: '',
    }
    setTabs(prev => [...prev, { file, content: file.content, dirty: true }])
    setActiveTab(filename)
  }, [])

  return (
    <div
      className="flex-1 flex flex-col overflow-hidden"
      style={{ backgroundColor: 'var(--bg-base)' }}
    >
      {/* Tab bar */}
      <div
        className="flex items-center flex-shrink-0 overflow-x-auto"
        style={{
          backgroundColor: 'var(--bg-surface)',
          borderBottom: '1px solid var(--border-subtle)',
          minHeight: '36px',
        }}
      >
        {tabs.map(tab => (
          <button
            key={tab.file.filename}
            onClick={() => setActiveTab(tab.file.filename)}
            className="flex items-center gap-1.5 px-3 h-9 text-xs whitespace-nowrap transition-colors relative flex-shrink-0"
            style={{
              color: activeTab === tab.file.filename ? 'var(--text-primary)' : 'var(--text-muted)',
              backgroundColor: activeTab === tab.file.filename ? 'var(--bg-base)' : 'transparent',
              borderRight: '1px solid var(--border-subtle)',
            }}
          >
            <FileCode2 className="w-3 h-3 flex-shrink-0" style={{ color: 'var(--accent)' }} />
            <span className="max-w-28 truncate">{tab.file.filename}</span>
            {tab.dirty && (
              <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: 'var(--warning)' }} />
            )}
            <span
              onClick={e => closeTab(tab.file.filename, e)}
              className="ml-1 w-4 h-4 flex items-center justify-center rounded hover:bg-white/10 transition-colors"
              style={{ color: 'var(--text-muted)' }}
            >
              <X className="w-2.5 h-2.5" />
            </span>
          </button>
        ))}

        {/* New file button */}
        <button
          onClick={handleNewFile}
          className="flex items-center justify-center w-8 h-9 ml-1 transition-colors flex-shrink-0"
          title="New MATLAB file"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => e.currentTarget.style.color = 'var(--text-secondary)'}
          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
        >
          <Plus className="w-3.5 h-3.5" />
        </button>

        <div className="flex-1" />

        {/* Action buttons */}
        {currentTab && (
          <div className="flex items-center gap-1 px-2">
            <button
              onClick={handleSave}
              disabled={saving || !currentTab.dirty}
              className="flex items-center gap-1 px-2 py-1 rounded text-xs transition-colors disabled:opacity-40"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { if (!saving && currentTab.dirty) e.currentTarget.style.color = 'var(--text-secondary)' }}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
              title="Save (⌘S)"
            >
              <Save className="w-3 h-3" />
              {saving ? 'Saving…' : 'Save'}
            </button>

            {isMatlab && onRunMatlab && (
              <button
                onClick={handleRun}
                disabled={running}
                className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors disabled:opacity-60"
                style={{
                  backgroundColor: 'var(--accent)',
                  color: 'var(--bg-base)',
                  opacity: running ? 0.7 : 1,
                }}
                title="Run in MATLAB"
              >
                <Play className="w-3 h-3" />
                {running ? 'Running…' : 'Run'}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Monaco Editor */}
      {currentTab ? (
        <div className="flex-1 overflow-hidden">
          <Editor
            height="100%"
            language={monacoLang}
            value={currentTab.content}
            onChange={handleChange}
            theme="vs-dark"
            options={{
              fontSize: 13,
              fontFamily: 'Menlo, Monaco, "Courier New", monospace',
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              wordWrap: 'on',
              lineNumbers: 'on',
              renderLineHighlight: 'gutter',
              tabSize: 4,
              insertSpaces: true,
              automaticLayout: true,
              padding: { top: 12, bottom: 12 },
              smoothScrolling: true,
            }}
          />
        </div>
      ) : (
        <div
          className="flex-1 flex flex-col items-center justify-center gap-3"
          style={{ color: 'var(--text-muted)' }}
        >
          <FileCode2 className="w-10 h-10 opacity-20" />
          <p className="text-sm">No file open</p>
          <button
            onClick={handleNewFile}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-md transition-colors"
            style={{ backgroundColor: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
          >
            <Plus className="w-3 h-3" />
            New MATLAB file
          </button>
        </div>
      )}
    </div>
  )
}

export type { EditorTab }
