import { useState } from 'react'
import { FolderOpen, ChevronDown, ChevronRight, FileCode2, Play } from 'lucide-react'
import type { ProjectFile } from '../lib/sessions'

import { API } from '../lib/constants'

const LANG_COLORS: Record<string, string> = {
  matlab: 'text-orange-400',
  python: 'text-blue-400',
  html: 'text-red-400',
  javascript: 'text-yellow-400',
  typescript: 'text-blue-300',
  markdown: 'text-zinc-400',
  text: 'text-zinc-500',
}

interface FileTreeProps {
  files: ProjectFile[]
  onRunMatlab?: (code: string) => void
  onOpenInEditor?: (file: ProjectFile) => void
}

export default function FileTree({ files, onRunMatlab, onOpenInEditor }: FileTreeProps) {
  const [expanded, setExpanded] = useState(true)
  const [openFile, setOpenFile] = useState<string | null>(null)

  if (!files.length) return null

  const projectName = files[0]?.path?.split('/')[0] || 'Project'

  return (
    <div className="mt-2 rounded-lg border border-white/10 bg-black/30 max-w-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 border-b border-white/10 bg-white/5 hover:bg-white/8 transition-colors"
      >
        <FolderOpen className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
        <span className="text-xs font-medium text-zinc-200 flex-1 text-left truncate">{projectName}</span>
        <span className="text-xs text-zinc-500">{files.length} files</span>
        {expanded ? <ChevronDown className="w-3 h-3 text-zinc-500" /> : <ChevronRight className="w-3 h-3 text-zinc-500" />}
      </button>

      {/* File list */}
      {expanded && (
        <div className="divide-y divide-white/5">
          {files.map(f => {
            const isOpen = openFile === f.filename
            const isMatlab = f.language === 'matlab' && f.filename.endsWith('.m')
            const langColor = LANG_COLORS[f.language] || 'text-zinc-400'

            return (
              <div key={f.filename}>
                <div className="flex items-center gap-2 px-3 py-1.5 hover:bg-white/5 transition-colors">
                  <FileCode2 className={`w-3 h-3 flex-shrink-0 ${langColor}`} />
                  <button
                    onClick={() => {
                      if (onOpenInEditor) {
                        onOpenInEditor(f)
                      } else {
                        setOpenFile(isOpen ? null : f.filename)
                      }
                    }}
                    className="flex-1 text-xs text-zinc-300 text-left truncate hover:text-zinc-100 transition-colors"
                    title={onOpenInEditor ? 'Open in Editor' : undefined}
                  >
                    {f.filename}
                  </button>
                  {isMatlab && onRunMatlab && (
                    <button
                      onClick={() => onRunMatlab(f.content)}
                      className="flex items-center gap-1 px-1.5 py-0.5 rounded text-xs text-cyan-400 hover:bg-cyan-400/10 transition-colors"
                      title="Run in MATLAB"
                    >
                      <Play className="w-2.5 h-2.5" />
                      Run
                    </button>
                  )}
                  <a
                    href={`${API}${f.url}`}
                    download={f.filename}
                    className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
                    title="Download"
                  >
                    DL
                  </a>
                </div>
                {isOpen && (
                  <pre className="px-3 py-2 bg-black/40 text-xs text-zinc-300 overflow-x-auto max-h-60 leading-relaxed">
                    {f.content}
                  </pre>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
