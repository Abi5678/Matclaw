const API = 'http://localhost:8000'

// ── Types ──────────────────────────────────────────────────────────────────

export interface PipelineNodeConfig {
  task_description: string
  tool: string  // 'run_matlab' | 'read_file' | 'edit_file' | 'run_command' | '' (LLM-only)
  code?: string
  timeout_seconds?: number
}

export interface PipelineNode {
  id: string
  agent_id: string
  label: string
  position: { x: number; y: number }
  config: PipelineNodeConfig
}

export interface PipelineEdge {
  id: string
  source: string
  target: string
  source_handle: string
  target_handle: string
}

export interface Pipeline {
  id?: string
  name: string
  description?: string
  nodes: PipelineNode[]
  edges: PipelineEdge[]
  created_at?: number
  updated_at?: number
}

export interface PipelineSummary {
  id: string
  name: string
  description: string
  created_at: number
  updated_at: number
}

export type NodeRunStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface NodeStatus {
  status: NodeRunStatus
  output?: string
  plots?: string[]
  error?: string
  startedAt?: number
  finishedAt?: number
}

export interface RunResult {
  run_id: string
  status: 'running' | 'completed' | 'failed' | 'cancelled'
}

// ── API Client ─────────────────────────────────────────────────────────────

export async function listPipelines(): Promise<PipelineSummary[]> {
  const res = await fetch(`${API}/api/pipelines`)
  if (!res.ok) throw new Error('Failed to list pipelines')
  return res.json()
}

export async function getPipeline(id: string): Promise<Pipeline> {
  const res = await fetch(`${API}/api/pipelines/${id}`)
  if (!res.ok) throw new Error('Pipeline not found')
  return res.json()
}

export async function savePipeline(pipeline: Pipeline): Promise<Pipeline> {
  const res = await fetch(`${API}/api/pipelines`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pipeline),
  })
  if (!res.ok) throw new Error('Failed to save pipeline')
  return res.json()
}

export async function deletePipeline(id: string): Promise<void> {
  await fetch(`${API}/api/pipelines/${id}`, { method: 'DELETE' })
}

export async function validatePipeline(pipeline: Partial<Pipeline>): Promise<{ valid: boolean; error: string | null }> {
  const res = await fetch(`${API}/api/pipelines/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pipeline),
  })
  if (!res.ok) return { valid: false, error: 'Validation request failed' }
  return res.json()
}

export async function runPipeline(id: string): Promise<{ run_id: string }> {
  const res = await fetch(`${API}/api/pipelines/${id}/run`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to start pipeline run')
  return res.json()
}

export async function cancelRun(runId: string): Promise<void> {
  await fetch(`${API}/api/pipelines/runs/${runId}`, { method: 'DELETE' })
}
