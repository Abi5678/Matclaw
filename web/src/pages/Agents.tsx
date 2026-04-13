import { useState, useEffect } from 'react'
import { Bot, Plus, Trash2, Wand2, Terminal, Code, BookOpen, Globe, Layout, X, Sparkles } from 'lucide-react'

// Tools available for agents
const AVAILABLE_TOOLS = [
  { id: 'read_file', label: 'Read', description: 'Retrieve and view files', icon: BookOpen },
  { id: 'edit_file', label: 'Edit', description: 'Add, delete and edit files', icon: Code },
  { id: 'run_command', label: 'Terminal', description: 'Run commands in the terminal', icon: Terminal },
  { id: 'preview', label: 'Preview', description: 'Provide a preview entry', icon: Layout },
  { id: 'web_search', label: 'Web search', description: 'Search for web content', icon: Globe },
  { id: 'run_matlab', label: 'MATLAB Bridge', description: 'Execute rigorous scientific functions natively on the local MATLAB engine', icon: Code }
]

import { API } from '../lib/constants'

interface Agent {
  id: string
  name: string
  system_prompt: string
  trigger_condition: string
  callable_by_others: boolean
  allowed_tools: string[]
}

export default function AgentsTab() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isGenerating, setIsGenerating] = useState(false)

  // Form State
  const [formData, setFormData] = useState({
    id: '',
    name: '',
    system_prompt: '',
    trigger_condition: '',
    callable_by_others: true,
    allowed_tools: ['run_matlab', 'read_file', 'edit_file']
  })

  useEffect(() => {
    fetch(`${API}/api/agents`)
      .then(res => res.json())
      .then(data => setAgents(data))
      .catch(err => console.error(err))
  }, [])

  const handleSelect = (a: Agent) => {
    setSelectedId(a.id)
    setFormData(a)
  }

  const handleNew = () => {
    setSelectedId('new')
    setFormData({
      id: '',
      name: '',
      system_prompt: '',
      trigger_condition: '',
      callable_by_others: true,
      allowed_tools: ['read_file', 'edit_file', 'run_matlab']
    })
  }

  const handleSave = async () => {
    try {
      const res = await fetch(`${API}/api/agents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData)
      })
      const saved: Agent = await res.json()
      setAgents([...agents.filter((a: Agent) => a.id !== saved.id), saved])
      setSelectedId(saved.id)
    } catch (e) {
      console.error(e)
    }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this agent?')) return
    await fetch(`${API}/api/agents/${id}`, { method: 'DELETE' })
    setAgents(agents.filter((a: Agent) => a.id !== id))
    if (selectedId === id) handleNew()
  }

  const handleToolToggle = (toolId: string) => {
    if (formData.allowed_tools.includes(toolId)) {
      setFormData({ ...formData, allowed_tools: formData.allowed_tools.filter(t => t !== toolId) })
    } else {
      setFormData({ ...formData, allowed_tools: [...formData.allowed_tools, toolId] })
    }
  }

  const [optimizing, setOptimizing] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const [smartTopic, setSmartTopic] = useState('')

  const handleOptimizePrompt = async () => {
    if (!formData.system_prompt?.trim() || optimizing) return
    setOptimizing(true)
    try {
      const res = await fetch(`${API}/api/agents/optimize-prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: formData.system_prompt }),
      })
      if (!res.ok) throw new Error('Optimization failed')
      const data = await res.json()
      setFormData(f => ({ ...f, system_prompt: data.optimized }))
    } catch (e) {
      console.error('Optimize prompt error:', e)
    } finally {
      setOptimizing(false)
    }
  }

  const executeSmartGenerate = async () => {
    if (!smartTopic) return
    setIsGenerating(true)
    try {
      const res = await fetch(`${API}/api/agents/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic: smartTopic })
      })
      if (!res.ok) throw new Error("Generation failed")
      const generated = await res.json()
      setFormData({
        id: generated.id,
        name: generated.name,
        system_prompt: generated.system_prompt,
        trigger_condition: generated.trigger_condition,
        callable_by_others: true,
        allowed_tools: generated.allowed_tools || []
      })
      setShowModal(false)
      setSmartTopic('')
    } catch (e) {
      console.error("Smart Generate Error:", e)
      alert("Smart Generation failed. " + e)
    } finally {
      setIsGenerating(false)
    }
  }

  return (
    <div
      className="flex-1 w-full h-full overflow-hidden flex"
      style={{ backgroundColor: 'var(--bg-base)', color: 'var(--text-primary)' }}
    >

      {/* Left Sidebar: Agent List */}
      <div
        className="w-64 flex flex-col flex-shrink-0"
        style={{ borderRight: '1px solid var(--border-default)' }}
      >
        <div
          className="p-4 flex justify-between items-center"
          style={{ borderBottom: '1px solid var(--border-default)' }}
        >
          <h2 className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>Agents</h2>
          <button
            onClick={handleNew}
            className="p-1 rounded transition"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
            onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {agents.map((agent: Agent) => (
            <div
              key={agent.id}
              onClick={() => handleSelect(agent)}
              className="flex items-center gap-3 p-2 rounded cursor-pointer transition select-none group"
              style={{
                backgroundColor: selectedId === agent.id ? 'var(--bg-active)' : 'transparent',
              }}
              onMouseEnter={e => {
                if (selectedId !== agent.id)
                  e.currentTarget.style.backgroundColor = 'var(--bg-hover)'
              }}
              onMouseLeave={e => {
                if (selectedId !== agent.id)
                  e.currentTarget.style.backgroundColor = 'transparent'
              }}
            >
              <div
                className="w-8 h-8 rounded flex items-center justify-center flex-shrink-0"
                style={{ backgroundColor: 'var(--accent-subtle)', color: 'var(--accent)' }}
              >
                <Bot className="w-4 h-4" />
              </div>
              <div className="flex-1 truncate text-sm" style={{ color: 'var(--text-primary)' }}>
                {agent.name}
              </div>
              <button
                onClick={e => { e.stopPropagation(); handleDelete(agent.id) }}
                className="opacity-0 group-hover:opacity-100 p-1 rounded transition-all flex-shrink-0"
                title="Delete agent"
                style={{ color: 'var(--text-muted)' }}
                onMouseEnter={e => { e.currentTarget.style.color = 'var(--error, #ef4444)'; e.currentTarget.style.backgroundColor = 'rgba(239,68,68,0.1)' }}
                onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.backgroundColor = 'transparent' }}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Right Canvas: Agent Builder Form */}
      <div className="flex-1 overflow-y-auto p-8 flex justify-center">
        <div className="max-w-2xl w-full">

          <div className="flex items-center justify-between mb-8">
            <h1
              className="text-xl font-semibold flex items-center gap-2"
              style={{ color: 'var(--text-primary)' }}
            >
              Agents
              <span style={{ color: 'var(--text-muted)' }}>/</span>
              <span>{selectedId === 'new' ? 'Create Agent' : 'Edit Agent'}</span>
            </h1>
            <button
              onClick={() => setShowModal(true)}
              className="flex items-center gap-2 px-3 py-1.5 rounded text-sm transition font-medium"
              style={{
                backgroundColor: 'var(--bg-elevated)',
                border: '1px solid var(--border-default)',
                color: 'var(--text-primary)',
              }}
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'var(--bg-elevated)')}
            >
              <Wand2 className="w-4 h-4" style={{ color: 'var(--accent)' }} />
              Smart Generate
            </button>
          </div>

          <div className="space-y-6">

            {/* Name */}
            <div>
              <label
                className="block text-xs font-semibold mb-2 uppercase tracking-wider"
                style={{ color: 'var(--text-secondary)' }}
              >
                Name <span style={{ color: 'var(--error)' }}>*</span>
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={e => setFormData({ ...formData, name: e.target.value })}
                placeholder="Enter agent name"
                className="w-full rounded p-2.5 text-sm outline-none transition"
                style={{
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border-default)',
                  color: 'var(--text-primary)',
                }}
                onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-default)')}
              />
            </div>

            {/* Prompt */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <label
                  className="text-xs font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--text-secondary)' }}
                >
                  Prompt
                </label>
                <button
                  onClick={handleOptimizePrompt}
                  disabled={optimizing || !formData.system_prompt?.trim()}
                  className="flex items-center gap-1.5 px-2 py-1 rounded text-xs font-medium transition disabled:opacity-40"
                  style={{ color: 'var(--accent)' }}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--accent-subtle)')}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  {optimizing ? 'Optimizing...' : 'Optimize Prompt'}
                </button>
              </div>
              <textarea
                value={formData.system_prompt}
                onChange={e => setFormData({ ...formData, system_prompt: e.target.value })}
                placeholder="Enter the agent's role, tone, workflow, tool preferences, and any rules or guidelines. (Optional)"
                className="w-full h-32 rounded p-2.5 text-sm outline-none transition resize-y font-mono"
                style={{
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border-default)',
                  color: 'var(--text-primary)',
                }}
                onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-default)')}
              />
            </div>

            {/* Callable config */}
            <div
              className="p-4 rounded-md"
              style={{
                border: '1px solid var(--border-default)',
                backgroundColor: 'var(--bg-surface)',
              }}
            >
              <div className="flex items-center justify-between mb-4">
                <label className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                  Callable by other agents
                </label>
                <div
                  className="w-10 h-5 rounded-full p-0.5 cursor-pointer transition-colors"
                  style={{ backgroundColor: formData.callable_by_others ? 'var(--accent)' : 'var(--border-default)' }}
                  onClick={() => setFormData({ ...formData, callable_by_others: !formData.callable_by_others })}
                >
                  <div
                    className="w-4 h-4 bg-white rounded-full shadow transform transition-transform"
                    style={{ transform: formData.callable_by_others ? 'translateX(20px)' : 'translateX(0)' }}
                  />
                </div>
              </div>
              <p className="text-xs mb-4" style={{ color: 'var(--text-muted)' }}>
                Currently restricted to DAG Task Router delegation scope.
              </p>

              <div className="space-y-4">
                <div>
                  <label
                    className="block text-xs font-medium mb-1"
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    English Identifier <span style={{ color: 'var(--error)' }}>*</span>
                  </label>
                  <input
                    type="text"
                    value={formData.id}
                    onChange={e => setFormData({ ...formData, id: e.target.value })}
                    placeholder="The unique string identifier, e.g. 'project-analyzer'"
                    className="w-full rounded p-2 text-sm outline-none font-mono transition"
                    style={{
                      backgroundColor: 'var(--bg-elevated)',
                      border: '1px solid var(--border-default)',
                      color: 'var(--accent)',
                    }}
                    onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                    onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-default)')}
                  />
                </div>
                <div>
                  <label
                    className="block text-xs font-medium mb-1"
                    style={{ color: 'var(--text-secondary)' }}
                  >
                    When to Call <span style={{ color: 'var(--error)' }}>*</span>
                  </label>
                  <textarea
                    value={formData.trigger_condition}
                    onChange={e => setFormData({ ...formData, trigger_condition: e.target.value })}
                    placeholder="Please describe the appropriate scenarios and timing for other agents to call this agent..."
                    className="w-full h-24 rounded p-2 text-sm outline-none resize-none italic transition"
                    style={{
                      backgroundColor: 'var(--bg-elevated)',
                      border: '1px solid var(--border-default)',
                      color: 'var(--text-primary)',
                    }}
                    onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                    onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-default)')}
                  />
                </div>
              </div>
            </div>

            {/* Tools Area */}
            <div>
              <label
                className="block text-xs font-semibold mb-3 uppercase tracking-wider"
                style={{ color: 'var(--text-secondary)' }}
              >
                Tools (MCP Servers)
              </label>
              <div
                className="rounded-md overflow-hidden"
                style={{ border: '1px solid var(--border-default)', backgroundColor: 'var(--bg-surface)' }}
              >
                {AVAILABLE_TOOLS.map((tool, index) => {
                  const Icon = tool.icon
                  const isActive = formData.allowed_tools.includes(tool.id)
                  return (
                    <div
                      key={tool.id}
                      className="flex items-center justify-between p-3 cursor-pointer select-none transition"
                      style={{
                        borderBottom: index !== AVAILABLE_TOOLS.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                      }}
                      onClick={() => handleToolToggle(tool.id)}
                      onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                      onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                    >
                      <div className="flex items-center gap-3">
                        <div
                          className="w-4 h-4 rounded-sm border flex items-center justify-center transition-colors"
                          style={{
                            backgroundColor: isActive ? 'var(--accent)' : 'transparent',
                            borderColor: isActive ? 'var(--accent)' : 'var(--text-muted)',
                          }}
                        >
                          {isActive && (
                            <div className="w-2 h-2 bg-white rounded-sm" style={{ clipPath: 'polygon(14% 44%, 0 65%, 50% 100%, 100% 16%, 80% 0%, 43% 62%)' }} />
                          )}
                        </div>
                        <Icon className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
                        <p className="text-sm font-medium" style={{ color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                          {tool.label}
                        </p>
                      </div>
                      <p className="text-xs truncate max-w-xs" style={{ color: 'var(--text-muted)' }}>
                        {tool.description}
                      </p>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Action Bar */}
            <div className="pt-6 flex items-center gap-3">
              <button
                onClick={handleSave}
                disabled={!formData.id || !formData.name}
                className="px-6 py-2 rounded font-semibold text-sm transition disabled:opacity-50 disabled:cursor-not-allowed"
                style={{
                  backgroundColor: 'var(--accent)',
                  color: 'white',
                }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--accent-hover)')}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'var(--accent)')}
              >
                {selectedId === 'new' ? 'Create' : 'Save Changes'}
              </button>
              <button
                onClick={handleNew}
                className="px-6 py-2 rounded font-medium text-sm transition"
                style={{
                  backgroundColor: 'var(--bg-elevated)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-default)',
                }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'var(--bg-elevated)')}
              >
                Cancel
              </button>
              {selectedId && selectedId !== 'new' && (
                <button
                  onClick={() => handleDelete(selectedId)}
                  className="ml-auto p-2 rounded transition"
                  title="Delete Agent"
                  style={{ color: 'var(--error)' }}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(239,68,68,0.1)')}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>

          </div>
        </div>
      </div>

      {/* Smart Generate Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
          <div
            className="rounded-lg shadow-2xl w-full max-w-lg overflow-hidden"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border-default)',
            }}
          >
            <div
              className="p-4 flex items-center justify-between"
              style={{ borderBottom: '1px solid var(--border-default)' }}
            >
              <h2 className="font-semibold flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
                <Wand2 className="w-4 h-4" style={{ color: 'var(--accent)' }} /> Smart Generate Agent
              </h2>
              <button
                onClick={() => setShowModal(false)}
                style={{ color: 'var(--text-muted)' }}
                onMouseEnter={e => (e.currentTarget.style.color = 'var(--text-primary)')}
                onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted)')}
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-6">
              <label className="block text-sm font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
                Agent Instructions
              </label>
              <textarea
                autoFocus
                value={smartTopic}
                onChange={e => setSmartTopic(e.target.value)}
                placeholder="Describe what this agent should do, when it should be used, and its specialized role."
                className="w-full h-32 rounded p-3 text-sm outline-none resize-none transition"
                style={{
                  backgroundColor: 'var(--bg-surface)',
                  border: '1px solid var(--border-default)',
                  color: 'var(--text-primary)',
                }}
                onFocus={e => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onBlur={e => (e.currentTarget.style.borderColor = 'var(--border-default)')}
              />
            </div>
            <div
              className="p-4 flex justify-end gap-3"
              style={{
                borderTop: '1px solid var(--border-default)',
                backgroundColor: 'var(--bg-surface)',
              }}
            >
              <button
                onClick={() => setShowModal(false)}
                className="px-4 py-2 rounded text-sm font-medium transition"
                style={{ color: 'var(--text-secondary)' }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
              >
                Cancel
              </button>
              <button
                onClick={executeSmartGenerate}
                disabled={isGenerating || !smartTopic}
                className="px-4 py-2 rounded text-sm font-semibold transition flex items-center gap-2 disabled:opacity-50"
                style={{
                  backgroundColor: 'var(--accent)',
                  color: 'white',
                }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--accent-hover)')}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'var(--accent)')}
              >
                {isGenerating ? 'Synthesizing...' : 'Generate'}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  )
}
