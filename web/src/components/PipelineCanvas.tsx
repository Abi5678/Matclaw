import { useState, useEffect, useCallback, useRef } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  BackgroundVariant,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Bot, Play, Square, Save, Plus, Trash2, GitFork, ChevronDown, AlertTriangle, CheckCircle, Loader, XCircle } from 'lucide-react'
import {
  listPipelines, getPipeline, savePipeline, deletePipeline, runPipeline, validatePipeline,
  type Pipeline, type PipelineSummary, type PipelineNodeConfig,
} from '../lib/pipeline'
import { usePipelineStream } from '../hooks/usePipelineStream'

const API = 'http://localhost:8000'

// ── Agent types ─────────────────────────────────────────────────────────────
interface Agent { id: string; name: string; system_prompt: string; allowed_tools: string[] }

const TOOL_OPTIONS = [
  { value: '', label: 'LLM Only (text output)' },
  { value: 'run_matlab', label: 'MATLAB Execution' },
  { value: 'read_file', label: 'Read File' },
  { value: 'edit_file', label: 'Edit File' },
  { value: 'run_command', label: 'Terminal Command' },
]

// ── Custom AgentNode ─────────────────────────────────────────────────────────
interface AgentNodeData {
  agentId?: string; label?: string; tool?: string; taskDescription?: string
  status?: string; error?: string; output?: string; onDelete?: (id: string) => void; nodeId?: string
}
function AgentNode({ data: rawData, selected }: NodeProps) {
  const data = rawData as AgentNodeData
  const status = data.status || 'pending'
  const statusColors: Record<string, string> = {
    running:   'var(--accent)',
    completed: '#22c55e',
    failed:    '#ef4444',
    pending:   'var(--border-default)',
  }
  const borderColor = selected ? 'var(--accent)' : statusColors[status]

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-elevated)',
        border: `1px solid ${borderColor}`,
        borderRadius: 12,
        padding: '12px 16px',
        minWidth: 180,
        maxWidth: 240,
        boxShadow: status === 'running' ? `0 0 12px rgba(0,212,170,0.3)` :
                   selected ? `0 0 0 2px rgba(0,212,170,0.2)` : 'none',
        transition: 'border-color 0.2s, box-shadow 0.2s',
        position: 'relative',
      }}
      className="group/node"
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: 'var(--accent)', width: 10, height: 10, border: '2px solid var(--bg-base)' }}
      />

      {/* Delete button — visible on hover */}
      <button
        onClick={(e) => { e.stopPropagation(); data.onDelete?.(data.nodeId ?? '') }}
        className="nodrag"
        style={{
          position: 'absolute', top: 6, right: 6,
          width: 18, height: 18, borderRadius: 4,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.3)',
          color: '#ef4444', cursor: 'pointer', opacity: 0, transition: 'opacity 0.15s',
        }}
        onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
        onMouseLeave={e => (e.currentTarget.style.opacity = '0')}
        onMouseOver={e => (e.currentTarget.style.opacity = '1')}
        title="Remove node"
      >
        <XCircle style={{ width: 11, height: 11 }} />
      </button>

      {/* Status indicator */}
      <div style={{ position: 'absolute', top: 8, right: 30 }}>
        {status === 'running' && <Loader style={{ width: 12, height: 12, color: 'var(--accent)', animation: 'spin 1s linear infinite' }} />}
        {status === 'completed' && <CheckCircle style={{ width: 12, height: 12, color: '#22c55e' }} />}
        {status === 'failed' && <XCircle style={{ width: 12, height: 12, color: '#ef4444' }} />}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <div style={{
          width: 24, height: 24, borderRadius: 6,
          backgroundColor: 'var(--accent-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Bot style={{ width: 13, height: 13, color: 'var(--accent)' }} />
        </div>
        <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
          {data.agentId || 'Agent'}
        </span>
      </div>

      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4, lineHeight: 1.3 }}>
        {data.label || 'Untitled Node'}
      </div>

      {data.tool && (
        <div style={{
          display: 'inline-block', fontSize: 10, fontWeight: 600, padding: '2px 7px',
          borderRadius: 999, backgroundColor: 'rgba(0,212,170,0.1)', color: 'var(--accent)',
          border: '1px solid rgba(0,212,170,0.2)',
        }}>
          {data.tool}
        </div>
      )}

      {status === 'failed' && data.error && (
        <div style={{ fontSize: 11, color: '#ef4444', marginTop: 6, lineHeight: 1.4 }}>
          {data.error.slice(0, 80)}
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        style={{ background: 'var(--accent)', width: 10, height: 10, border: '2px solid var(--bg-base)' }}
      />
    </div>
  )
}

const nodeTypes = { agentNode: AgentNode }

// ── Node Inspector ───────────────────────────────────────────────────────────
function NodeInspector({
  node,
  agents,
  status,
  onChange,
  onDelete,
}: {
  node: Node | null
  agents: Agent[]
  status?: { output?: string; error?: string; plots?: string[] }
  onChange: (id: string, data: Partial<PipelineNodeConfig & { label: string; agent_id: string }>) => void
  onDelete: (id: string) => void
}) {
  if (!node) {
    return (
      <div style={{
        width: 240, flexShrink: 0, borderLeft: '1px solid var(--border-default)',
        backgroundColor: 'var(--bg-surface)', padding: 16, display: 'flex',
        flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8,
      }}>
        <GitFork style={{ width: 24, height: 24, color: 'var(--text-muted)' }} />
        <p style={{ fontSize: 13, color: 'var(--text-muted)', textAlign: 'center' }}>
          Select a node to configure it
        </p>
      </div>
    )
  }

  const d = node.data as Record<string, unknown>
  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '7px 10px', borderRadius: 8, fontSize: 12,
    backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-default)',
    color: 'var(--text-primary)', outline: 'none',
  }
  const labelStyle: React.CSSProperties = {
    fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em',
    color: 'var(--text-muted)', marginBottom: 4, display: 'block',
  }

  return (
    <div style={{
      width: 240, flexShrink: 0, borderLeft: '1px solid var(--border-default)',
      backgroundColor: 'var(--bg-surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border-default)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>Node Config</span>
        <button
          onClick={() => onDelete(node.id)}
          style={{ color: 'var(--error, #ef4444)', padding: 4, borderRadius: 6, cursor: 'pointer', background: 'none', border: 'none' }}
          title="Delete node"
        >
          <Trash2 style={{ width: 14, height: 14 }} />
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
        {/* Label */}
        <div>
          <label style={labelStyle}>Label</label>
          <input
            style={inputStyle}
            value={(d.label as string) || ''}
            onChange={e => onChange(node.id, { label: e.target.value })}
            placeholder="Node label"
          />
        </div>

        {/* Agent */}
        <div>
          <label style={labelStyle}>Agent</label>
          <select
            style={{ ...inputStyle, cursor: 'pointer' }}
            value={(d.agentId as string) || ''}
            onChange={e => onChange(node.id, { agent_id: e.target.value })}
          >
            <option value="">— No agent —</option>
            {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>

        {/* Tool */}
        <div>
          <label style={labelStyle}>Tool</label>
          <select
            style={{ ...inputStyle, cursor: 'pointer' }}
            value={(d.tool as string) || ''}
            onChange={e => onChange(node.id, { tool: e.target.value })}
          >
            {TOOL_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        {/* Task description */}
        <div>
          <label style={labelStyle}>Task Description</label>
          <textarea
            style={{ ...inputStyle, resize: 'vertical', minHeight: 80, fontFamily: 'inherit', lineHeight: 1.5 }}
            value={(d.taskDescription as string) || ''}
            onChange={e => onChange(node.id, { task_description: e.target.value })}
            placeholder="What should this node do?"
          />
        </div>

        {/* MATLAB code (only when tool === run_matlab) */}
        {(d.tool as string) === 'run_matlab' && (
          <div>
            <label style={labelStyle}>MATLAB Code (optional)</label>
            <textarea
              style={{ ...inputStyle, resize: 'vertical', minHeight: 100, fontFamily: 'var(--font-mono, monospace)', fontSize: 11, lineHeight: 1.5 }}
              value={(d.code as string) || ''}
              onChange={e => onChange(node.id, { code: e.target.value })}
              placeholder="% Leave blank to have LLM generate it"
            />
          </div>
        )}

        {/* Live output (during/after run) */}
        {(status?.output || status?.error) && (
          <div>
            <label style={labelStyle}>Output</label>
            <div style={{
              backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
              borderRadius: 8, padding: 10, fontSize: 11, fontFamily: 'monospace',
              color: status?.error ? '#ef4444' : '#a0ffd0', maxHeight: 160, overflowY: 'auto',
              whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {status?.error || status?.output}
            </div>
          </div>
        )}

        {/* Plots */}
        {status?.plots && status.plots.length > 0 && (
          <div>
            <label style={labelStyle}>Plots</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {status.plots.map((plotPath, i) => (
                <a
                  key={i}
                  href={`http://localhost:8000${plotPath}`}
                  target="_blank"
                  rel="noreferrer"
                  title="Click to open full size"
                >
                  <img
                    src={`http://localhost:8000${plotPath}`}
                    alt={`Plot ${i + 1}`}
                    style={{
                      width: '100%', borderRadius: 8,
                      border: '1px solid var(--border-subtle)',
                      display: 'block', cursor: 'pointer',
                    }}
                    onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                  />
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Agent Palette ────────────────────────────────────────────────────────────
function AgentPalette({ agents }: { agents: Agent[] }) {
  const onDragStart = (e: React.DragEvent, agent: Agent) => {
    e.dataTransfer.setData('application/matclaw-agent', JSON.stringify(agent))
    e.dataTransfer.effectAllowed = 'move'
  }

  return (
    <div style={{
      width: 200, flexShrink: 0, borderRight: '1px solid var(--border-default)',
      backgroundColor: 'var(--bg-surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border-default)' }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>Agents</span>
        <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 3 }}>Drag onto canvas</p>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {agents.length === 0 && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)', padding: 8, textAlign: 'center' }}>
            No agents yet. Create some in the Agents tab.
          </p>
        )}
        {agents.map(agent => (
          <div
            key={agent.id}
            draggable
            onDragStart={e => onDragStart(e, agent)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 10px', borderRadius: 10, cursor: 'grab',
              backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
              transition: 'border-color 0.15s, transform 0.15s',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--accent-border, rgba(0,212,170,0.25))'; (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-1px)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border-subtle)'; (e.currentTarget as HTMLDivElement).style.transform = 'none' }}
          >
            <div style={{ width: 28, height: 28, borderRadius: 7, backgroundColor: 'var(--accent-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <Bot style={{ width: 14, height: 14, color: 'var(--accent)' }} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{agent.name}</div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{agent.id}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main PipelineCanvas ──────────────────────────────────────────────────────
let nodeIdCounter = 0
const newNodeId = () => `node_${++nodeIdCounter}_${Date.now()}`

export default function PipelineCanvas() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([])
  const [activePipelineId, setActivePipelineId] = useState<string | null>(null)
  const [pipelineName, setPipelineName] = useState('Untitled Pipeline')
  const [selectedNode, setSelectedNode] = useState<Node | null>(null)
  const [showPipelinePicker, setShowPipelinePicker] = useState(false)
  const [cycleError, setCycleError] = useState<string | null>(null)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle')
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const validateTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Use a ref so AgentNode's delete button always calls the latest version
  const deleteNodeRef = useRef<(id: string) => void>(() => {})

  const stream = usePipelineStream()

  // Fetch agents
  useEffect(() => {
    fetch(`${API}/api/agents`)
      .then(r => r.json())
      .then(setAgents)
      .catch(() => {})
  }, [])

  // Fetch pipeline list
  useEffect(() => {
    listPipelines().then(setPipelines).catch(() => {})
  }, [])

  // Update node statuses from stream
  useEffect(() => {
    setNodes(nds => nds.map(n => ({
      ...n,
      data: {
        ...n.data,
        status: stream.nodeStatuses[n.id]?.status || (stream.isRunning ? 'pending' : n.data.status),
        error: stream.nodeStatuses[n.id]?.error,
      },
    })))
  }, [stream.nodeStatuses, stream.isRunning, setNodes])

  // Validate pipeline on change (debounced)
  useEffect(() => {
    if (validateTimer.current) clearTimeout(validateTimer.current)
    validateTimer.current = setTimeout(() => {
      const pipeline = buildPipelinePayload()
      if (pipeline.nodes.length > 1) {
        validatePipeline(pipeline).then(r => setCycleError(r.valid ? null : (r.error || 'Invalid pipeline')))
      } else {
        setCycleError(null)
      }
    }, 400)
    return () => { if (validateTimer.current) clearTimeout(validateTimer.current) }
  }, [nodes, edges])

  const buildPipelinePayload = (): Pipeline => ({
    id: activePipelineId || undefined,
    name: pipelineName,
    nodes: nodes.map(n => ({
      id: n.id,
      agent_id: (n.data.agentId as string) || '',
      label: (n.data.label as string) || '',
      position: n.position,
      config: {
        task_description: (n.data.taskDescription as string) || '',
        tool: (n.data.tool as string) || '',
        code: (n.data.code as string) || '',
      },
    })),
    edges: edges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      source_handle: e.sourceHandle || 'data',
      target_handle: e.targetHandle || 'data',
    })),
  })

  const onConnect = useCallback((conn: Connection) => {
    setEdges(eds => addEdge({ ...conn, type: 'smoothstep', animated: false, style: { stroke: 'var(--accent)', strokeWidth: 2 } }, eds))
  }, [setEdges])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const raw = e.dataTransfer.getData('application/matclaw-agent')
    if (!raw) return
    const agent: Agent = JSON.parse(raw)
    const rect = reactFlowWrapper.current?.getBoundingClientRect()
    if (!rect) return
    const position = { x: e.clientX - rect.left - 90, y: e.clientY - rect.top - 40 }
    const id = newNodeId()
    setNodes(nds => [...nds, {
      id,
      type: 'agentNode',
      position,
      data: {
        nodeId: id,
        onDelete: (id: string) => deleteNodeRef.current(id),
        label: agent.name,
        agentId: agent.id,
        tool: agent.allowed_tools?.[0] || '',
        taskDescription: '',
        code: '',
        status: 'pending',
      },
    }])
  }, [setNodes])

  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => setSelectedNode(node), [])
  const onPaneClick = useCallback(() => setSelectedNode(null), [])

  const handleNodeChange = useCallback((nodeId: string, updates: Record<string, unknown>) => {
    setNodes(nds => nds.map(n => {
      if (n.id !== nodeId) return n
      const newData = { ...n.data }
      if ('label' in updates) newData.label = updates.label
      if ('agent_id' in updates) newData.agentId = updates.agent_id
      if ('tool' in updates) newData.tool = updates.tool
      if ('task_description' in updates) newData.taskDescription = updates.task_description
      if ('code' in updates) newData.code = updates.code
      return { ...n, data: newData }
    }))
    setSelectedNode(prev => prev?.id === nodeId ? { ...prev, data: { ...prev.data, ...Object.fromEntries(Object.entries(updates).map(([k, v]) => [k === 'agent_id' ? 'agentId' : k === 'task_description' ? 'taskDescription' : k, v])) } } : prev)
  }, [setNodes])

  const handleDeleteNode = useCallback((nodeId: string) => {
    setNodes(nds => nds.filter(n => n.id !== nodeId))
    setEdges(eds => eds.filter(e => e.source !== nodeId && e.target !== nodeId))
    setSelectedNode(null)
  }, [setNodes, setEdges])

  // Keep the ref up to date so AgentNode always calls the latest function
  deleteNodeRef.current = handleDeleteNode

  const handleSave = async () => {
    setSaveStatus('saving')
    try {
      const saved = await savePipeline(buildPipelinePayload())
      setActivePipelineId(saved.id || null)
      setPipelines(await listPipelines())
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
    } catch {
      setSaveStatus('idle')
    }
  }

  const handleRun = async () => {
    if (cycleError || stream.isRunning) return
    stream.reset()
    // Save first, then run
    let pipelineId = activePipelineId
    try {
      const saved = await savePipeline(buildPipelinePayload())
      pipelineId = saved.id || null
      setActivePipelineId(pipelineId)
    } catch { /* if save fails, try to run existing */ }
    if (!pipelineId) return
    // Reset node statuses to pending
    setNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, status: 'pending', error: undefined } })))
    try {
      const { run_id } = await runPipeline(pipelineId)
      stream.startStream(run_id)
    } catch (err) {
      console.error('Pipeline run failed:', err)
      stream.reset()
    }
  }

  const handleLoadPipeline = async (id: string) => {
    const p = await getPipeline(id)
    setActivePipelineId(p.id || null)
    setPipelineName(p.name)
    setNodes(p.nodes.map(n => ({
      id: n.id,
      type: 'agentNode',
      position: n.position,
      data: {
        nodeId: n.id,
        onDelete: (id: string) => deleteNodeRef.current(id),
        label: n.label,
        agentId: n.agent_id,
        tool: n.config.tool,
        taskDescription: n.config.task_description,
        code: n.config.code || '',
        status: 'pending',
      },
    })))
    setEdges(p.edges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.source_handle,
      targetHandle: e.target_handle,
      type: 'smoothstep',
      animated: false,
      style: { stroke: 'var(--accent)', strokeWidth: 2 },
    })))
    setShowPipelinePicker(false)
  }

  const handleNew = () => {
    setNodes([])
    setEdges([])
    setActivePipelineId(null)
    setPipelineName('Untitled Pipeline')
    setSelectedNode(null)
    stream.reset()
  }

  const selectedStatus = selectedNode ? stream.nodeStatuses[selectedNode.id] : undefined

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', backgroundColor: 'var(--bg-base)' }}>

      {/* ── Toolbar ── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px',
        borderBottom: '1px solid var(--border-default)', backgroundColor: 'var(--bg-surface)',
        flexShrink: 0, flexWrap: 'wrap',
      }}>
        {/* Pipeline selector */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setShowPipelinePicker(p => !p)}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 8,
              fontSize: 13, fontWeight: 600, backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border-default)', color: 'var(--text-primary)', cursor: 'pointer',
              maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}
          >
            <GitFork style={{ width: 14, height: 14, color: 'var(--accent)', flexShrink: 0 }} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pipelineName}</span>
            <ChevronDown style={{ width: 12, height: 12, flexShrink: 0 }} />
          </button>
          {showPipelinePicker && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, zIndex: 50, marginTop: 4,
              backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-default)',
              borderRadius: 10, padding: 6, minWidth: 220, boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            }}>
              <button
                onClick={handleNew}
                style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', borderRadius: 7, fontSize: 13, color: 'var(--accent)', cursor: 'pointer', background: 'none', border: 'none' }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
              >
                <Plus style={{ width: 14, height: 14 }} /> New Pipeline
              </button>
              {pipelines.length > 0 && <div style={{ height: 1, backgroundColor: 'var(--border-subtle)', margin: '4px 0' }} />}
              {pipelines.map(p => (
                <button
                  key={p.id}
                  onClick={() => handleLoadPipeline(p.id)}
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', padding: '8px 10px', borderRadius: 7, fontSize: 13, color: 'var(--text-primary)', cursor: 'pointer', background: 'none', border: 'none', textAlign: 'left' }}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-hover)')}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{p.name}</span>
                  <button
                    onClick={async e => { e.stopPropagation(); await deletePipeline(p.id); setPipelines(await listPipelines()) }}
                    style={{ color: 'var(--error, #ef4444)', padding: 3, borderRadius: 4, cursor: 'pointer', background: 'none', border: 'none', flexShrink: 0 }}
                  >
                    <Trash2 style={{ width: 12, height: 12 }} />
                  </button>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Pipeline name input */}
        <input
          value={pipelineName}
          onChange={e => setPipelineName(e.target.value)}
          style={{
            padding: '6px 10px', borderRadius: 8, fontSize: 13, fontWeight: 500,
            backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
            color: 'var(--text-primary)', outline: 'none', width: 180,
          }}
          placeholder="Pipeline name"
        />

        {/* Cycle warning */}
        {cycleError && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#f59e0b' }}>
            <AlertTriangle style={{ width: 13, height: 13 }} />
            {cycleError}
          </div>
        )}

        {/* Stream error */}
        {stream.error && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#ef4444' }}>
            <AlertTriangle style={{ width: 13, height: 13 }} />
            {stream.error}
          </div>
        )}

        {/* Progress */}
        {stream.isRunning && (
          <div style={{ fontSize: 12, color: 'var(--accent)' }}>
            {stream.completedNodes}/{stream.totalNodes} nodes
          </div>
        )}

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {/* Save */}
          <button
            onClick={handleSave}
            disabled={saveStatus === 'saving'}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 8,
              fontSize: 13, fontWeight: 600, cursor: 'pointer',
              backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border-default)',
              color: saveStatus === 'saved' ? '#22c55e' : 'var(--text-primary)',
            }}
          >
            <Save style={{ width: 14, height: 14 }} />
            {saveStatus === 'saving' ? 'Saving…' : saveStatus === 'saved' ? 'Saved!' : 'Save'}
          </button>

          {/* Run / Stop */}
          {stream.isRunning ? (
            <button
              onClick={stream.cancel}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 8,
                fontSize: 13, fontWeight: 700, cursor: 'pointer',
                backgroundColor: '#ef4444', border: '1px solid #ef4444', color: 'white',
              }}
            >
              <Square style={{ width: 14, height: 14 }} fill="white" /> Stop
            </button>
          ) : (
            <button
              onClick={handleRun}
              disabled={!!cycleError || nodes.length === 0}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 8,
                fontSize: 13, fontWeight: 700, cursor: (cycleError || nodes.length === 0) ? 'not-allowed' : 'pointer',
                backgroundColor: 'var(--accent)', border: 'none', color: '#0a0a0f',
                opacity: (cycleError || nodes.length === 0) ? 0.4 : 1,
              }}
            >
              <Play style={{ width: 14, height: 14 }} fill="#0a0a0f" /> Run
            </button>
          )}
        </div>
      </div>

      {/* ── Body: Palette + Canvas + Inspector ── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        <AgentPalette agents={agents} />

        <div ref={reactFlowWrapper} style={{ flex: 1, position: 'relative' }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            deleteKeyCode="Delete"
            style={{ backgroundColor: 'var(--bg-base)' }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
            <Controls showInteractive={false} />
            <MiniMap nodeColor={() => 'var(--bg-elevated)'} maskColor="rgba(0,0,0,0.5)" />
          </ReactFlow>

          {/* Empty state hint */}
          {nodes.length === 0 && (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', pointerEvents: 'none',
            }}>
              <GitFork style={{ width: 40, height: 40, color: 'var(--border-default)', marginBottom: 12 }} />
              <p style={{ fontSize: 14, color: 'var(--text-muted)', marginBottom: 6 }}>No nodes yet</p>
              <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>Drag agents from the left panel onto the canvas</p>
            </div>
          )}
        </div>

        <NodeInspector
          node={selectedNode}
          agents={agents}
          status={selectedStatus}
          onChange={handleNodeChange as (id: string, data: Partial<PipelineNodeConfig & { label: string; agent_id: string }>) => void}
          onDelete={handleDeleteNode}
        />
      </div>
    </div>
  )
}
