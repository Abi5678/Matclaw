import { useState, useEffect, useCallback } from 'react'
import { useTheme } from '../hooks/useTheme'
import { type ThemeMode } from '../lib/themeContext'
import { THEME_OPTIONS } from '../lib/constants'
import { Check, Monitor, Moon, Sun, Palette, Plus, Trash2, X, Cpu } from 'lucide-react'

const API = 'http://localhost:8000'

const PROVIDERS = [
  { value: 'nvidia', label: 'NVIDIA' },
  { value: 'google', label: 'Google' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openai-compatible', label: 'OpenAI Compatible' },
]

interface ModelConfig {
  id: string
  provider: string
  model: string
  api_key: string
  base_url?: string | null
  label: string
  active: boolean
}

const THEME_ICONS: Record<ThemeMode, React.ReactNode> = {
  'deep-blue': <Palette className="w-4 h-4" />,
  dark: <Moon className="w-4 h-4" />,
  light: <Sun className="w-4 h-4" />,
  'light-plus': <Sun className="w-4 h-4" />,
  auto: <Monitor className="w-4 h-4" />,
}

export default function SettingsPanel() {
  const { mode, setMode } = useTheme()
  const [models, setModels] = useState<ModelConfig[]>([])
  const [showAddModel, setShowAddModel] = useState(false)
  const [newModel, setNewModel] = useState({
    provider: 'openai-compatible',
    model: '',
    api_key: '',
    base_url: '',
    label: '',
  })

  const fetchModels = useCallback(() => {
    fetch(`${API}/api/settings/models`)
      .then(r => r.json())
      .then((data: ModelConfig[]) => setModels(data))
      .catch(err => console.error('Failed to fetch models:', err))
  }, [])

  useEffect(() => { fetchModels() }, [fetchModels])

  const handleAddModel = async () => {
    if (!newModel.model || !newModel.api_key || !newModel.label) return
    try {
      const body: ModelConfig = {
        id: '',
        provider: newModel.provider,
        model: newModel.model,
        api_key: newModel.api_key,
        label: newModel.label,
        active: false,
      }
      if (newModel.provider === 'openai-compatible' && newModel.base_url) {
        body.base_url = newModel.base_url
      }
      await fetch(`${API}/api/settings/models`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      setShowAddModel(false)
      setNewModel({ provider: 'openai-compatible', model: '', api_key: '', base_url: '', label: '' })
      fetchModels()
    } catch (e) {
      console.error(e)
    }
  }

  const handleActivate = async (id: string) => {
    await fetch(`${API}/api/settings/models/${id}/activate`, { method: 'POST' })
    fetchModels()
  }

  const handleDeleteModel = async (id: string) => {
    await fetch(`${API}/api/settings/models/${id}`, { method: 'DELETE' })
    fetchModels()
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-2xl mx-auto p-6 space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-xl font-semibold" style={{ color: 'var(--text-primary)' }}>Settings</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
            Customize your MatClaw workspace
          </p>
        </div>

        {/* ── Basics ─────────────────────────────────────────────── */}
        <SettingsSection title="Basics">
          <SettingsCard label="Theme" description="Select a theme color">
            <div className="flex flex-wrap gap-2">
              {THEME_OPTIONS.map((opt: { value: ThemeMode; label: string }) => (
                <button
                  key={opt.value}
                  onClick={() => setMode(opt.value)}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-all duration-200"
                  style={{
                    backgroundColor: mode === opt.value ? 'var(--accent-subtle)' : 'var(--bg-hover)',
                    color: mode === opt.value ? 'var(--accent)' : 'var(--text-secondary)',
                    border: mode === opt.value ? '1px solid var(--accent)' : '1px solid var(--border-subtle)',
                  }}
                >
                  {THEME_ICONS[opt.value]}
                  {opt.label}
                  {mode === opt.value && <Check className="w-3 h-3" />}
                </button>
              ))}
            </div>
          </SettingsCard>

          <SettingsCard label="Language" description="Select the language for button labels and other in-app text">
            <div
              className="flex items-center px-3 py-2 rounded-lg text-xs font-medium"
              style={{
                backgroundColor: 'var(--bg-elevated)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-primary)',
              }}
            >
              English
            </div>
          </SettingsCard>
        </SettingsSection>

        {/* ── Models ───────────────────────────────────────────────── */}
        <SettingsSection title="Models">
          <div className="space-y-2">
            {models.map((m: ModelConfig) => (
              <div
                key={m.id}
                className="flex items-center justify-between gap-3 rounded-xl px-4 py-3 backdrop-blur-sm cursor-pointer transition"
                style={{
                  backgroundColor: 'var(--card-bg)',
                  border: m.active ? '1px solid var(--accent)' : '1px solid var(--card-border)',
                }}
                onClick={() => handleActivate(m.id)}
              >
                <div className="flex items-center gap-3 min-w-0">
                  <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                    style={{
                      backgroundColor: m.active ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                      color: m.active ? 'var(--accent)' : 'var(--text-muted)',
                    }}
                  >
                    <Cpu className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                        {m.label}
                      </p>
                      {m.active && (
                        <span
                          className="w-2 h-2 rounded-full flex-shrink-0"
                          style={{ backgroundColor: 'var(--accent)' }}
                        />
                      )}
                    </div>
                    <p className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                      {m.provider} · {m.model}
                    </p>
                  </div>
                </div>
                {!m.active && (
                  <button
                    onClick={e => { e.stopPropagation(); handleDeleteModel(m.id) }}
                    className="p-1.5 rounded transition flex-shrink-0"
                    style={{ color: 'var(--text-muted)' }}
                    onMouseEnter={e => (e.currentTarget.style.color = 'var(--error)')}
                    onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            ))}

            {/* Add Model Button */}
            <button
              onClick={() => setShowAddModel(true)}
              className="w-full flex items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-medium transition"
              style={{
                border: '1px dashed var(--border-default)',
                color: 'var(--text-secondary)',
              }}
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
            >
              <Plus className="w-4 h-4" />
              Add Model
            </button>
          </div>
        </SettingsSection>

        {/* ── Preferences ────────────────────────────────────────── */}
        <SettingsSection title="Preferences">
          <SettingsCard label="Editor Settings" description="Font, word-wrap, window settings, etc., in the Editor">
            <SettingsButton>Go to Settings</SettingsButton>
          </SettingsCard>

          <SettingsCard label="Shortcut Settings" description="Customize shortcut keys for various operations in the IDE">
            <SettingsButton>VS Code keymap</SettingsButton>
          </SettingsCard>

          <SettingsCard label="Default Browser for Local Links" description="Choose how local links opened from the terminal should be handled">
            <SettingsButton>System browser</SettingsButton>
          </SettingsCard>

          <SettingsCard label="Default Open Method for Markdown Files" description="Markdown files will open using this method by default">
            <SettingsButton>Markdown Preview Mode</SettingsButton>
          </SettingsCard>
        </SettingsSection>

        {/* ── About ──────────────────────────────────────────────── */}
        <SettingsSection title="About MatClaw">
          <div
            className="rounded-xl p-4 backdrop-blur-sm"
            style={{
              backgroundColor: 'var(--card-bg)',
              border: '1px solid var(--card-border)',
            }}
          >
            <div className="flex items-center gap-3">
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center text-lg font-bold"
                style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
              >
                M
              </div>
              <div>
                <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>MatClaw v2.0</p>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  The AI that actually does things
                </p>
              </div>
            </div>
          </div>
        </SettingsSection>
      </div>

      {/* ── Add Model Modal ──────────────────────────────────────── */}
      {showAddModel && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
          <div
            className="rounded-lg shadow-2xl w-full max-md-md overflow-hidden"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border-default)',
            }}
          >
            <div
              className="p-4 flex items-center justify-between"
              style={{ borderBottom: '1px solid var(--border-default)' }}
            >
              <h2 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
                Add Model
              </h2>
              <button
                onClick={() => setShowAddModel(false)}
                style={{ color: 'var(--text-muted)' }}
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-5 space-y-4">
              {/* Provider */}
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
                  Provider
                </label>
                <select
                  value={newModel.provider}
                  onChange={e => setNewModel((m: typeof newModel) => ({ ...m, provider: e.target.value }))}
                  className="w-full rounded p-2.5 text-sm outline-none"
                  style={{
                    backgroundColor: 'var(--bg-surface)',
                    border: '1px solid var(--border-default)',
                    color: 'var(--text-primary)',
                  }}
                >
                  {PROVIDERS.map(p => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </div>

              {/* Model Name */}
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
                  Model
                </label>
                <input
                  type="text"
                  value={newModel.model}
                  onChange={e => setNewModel((m: typeof newModel) => ({ ...m, model: e.target.value }))}
                  placeholder="e.g. gpt-4o, claude-sonnet-4-20250514"
                  className="w-full rounded p-2.5 text-sm outline-none"
                  style={{
                    backgroundColor: 'var(--bg-surface)',
                    border: '1px solid var(--border-default)',
                    color: 'var(--text-primary)',
                  }}
                />
              </div>

              {/* API Key */}
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
                  API Key
                </label>
                <input
                  type="password"
                  value={newModel.api_key}
                  onChange={e => setNewModel((m: typeof newModel) => ({ ...m, api_key: e.target.value }))}
                  placeholder="sk-..."
                  className="w-full rounded p-2.5 text-sm outline-none font-mono"
                  style={{
                    backgroundColor: 'var(--bg-surface)',
                    border: '1px solid var(--border-default)',
                    color: 'var(--text-primary)',
                  }}
                />
              </div>

              {/* Base URL (only for openai-compatible) */}
              {newModel.provider === 'openai-compatible' && (
                <div>
                  <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
                    Base URL
                  </label>
                  <input
                    type="text"
                    value={newModel.base_url}
                    onChange={e => setNewModel((m: typeof newModel) => ({ ...m, base_url: e.target.value }))}
                    placeholder="https://api.example.com/v1"
                    className="w-full rounded p-2.5 text-sm outline-none font-mono"
                    style={{
                      backgroundColor: 'var(--bg-surface)',
                      border: '1px solid var(--border-default)',
                      color: 'var(--text-primary)',
                    }}
                  />
                </div>
              )}

              {/* Display Label */}
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
                  Display Name
                </label>
                <input
                  type="text"
                  value={newModel.label}
                  onChange={e => setNewModel((m: typeof newModel) => ({ ...m, label: e.target.value }))}
                  placeholder="e.g. GPT-4o, Claude Sonnet"
                  className="w-full rounded p-2.5 text-sm outline-none"
                  style={{
                    backgroundColor: 'var(--bg-surface)',
                    border: '1px solid var(--border-default)',
                    color: 'var(--text-primary)',
                  }}
                />
              </div>
            </div>

            <div
              className="p-4 flex justify-end gap-3"
              style={{
                borderTop: '1px solid var(--border-default)',
                backgroundColor: 'var(--bg-surface)',
              }}
            >
              <button
                onClick={() => setShowAddModel(false)}
                className="px-4 py-2 rounded text-sm font-medium transition"
                style={{ color: 'var(--text-secondary)' }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
              >
                Cancel
              </button>
              <button
                onClick={handleAddModel}
                disabled={!newModel.model || !newModel.api_key || !newModel.label}
                className="px-4 py-2 rounded text-sm font-semibold transition disabled:opacity-40"
                style={{
                  backgroundColor: 'var(--accent)',
                  color: 'white',
                }}
              >
                Add
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function SettingsSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h2
        className="text-sm font-semibold mb-3 uppercase tracking-wider"
        style={{ color: 'var(--text-muted)' }}
      >
        {title}
      </h2>
      <div className="space-y-2">
        {children}
      </div>
    </div>
  )
}

function SettingsCard({ label, description, children }: {
  label: string
  description: string
  children: React.ReactNode
}) {
  return (
    <div
      className="flex items-center justify-between gap-4 rounded-xl px-4 py-3 backdrop-blur-sm"
      style={{
        backgroundColor: 'var(--card-bg)',
        border: '1px solid var(--card-border)',
      }}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{label}</p>
        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{description}</p>
      </div>
      <div className="flex-shrink-0">
        {children}
      </div>
    </div>
  )
}

function SettingsButton({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="flex items-center px-3 py-2 rounded-lg text-xs font-medium cursor-default"
      style={{
        backgroundColor: 'var(--bg-elevated)',
        border: '1px solid var(--border-subtle)',
        color: 'var(--text-secondary)',
      }}
    >
      {children}
    </div>
  )
}
