import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Eye, ArrowLeft, RefreshCw, Image as ImageIcon, Search, CheckCircle, AlertTriangle, Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

const API = 'http://localhost:8000'

interface PlotEntry {
  name: string
  url: string
  mtime: number
}

export default function VisionGallery() {
  const navigate = useNavigate()
  const [plots, setPlots] = useState<PlotEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<PlotEntry | null>(null)
  const [analysisMap, setAnalysisMap] = useState<Record<string, string>>({})
  const [analyzingSet, setAnalyzingSet] = useState<Set<string>>(new Set())

  const fetchPlots = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/plots`)
      const data = await res.json()
      setPlots(data.plots || [])
    } catch {
      setPlots([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchPlots() }, [])

  const analyzePlot = async (plotUrl: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (analyzingSet.has(plotUrl) || analysisMap[plotUrl]) return
    setAnalyzingSet(prev => new Set(prev).add(plotUrl))
    try {
      const res = await fetch(`${API}/api/vision/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plot_path: plotUrl }),
      })
      const data = await res.json()
      setAnalysisMap(prev => ({ ...prev, [plotUrl]: data.analysis || 'No analysis available' }))
    } catch {
      setAnalysisMap(prev => ({ ...prev, [plotUrl]: 'Analysis failed — server error' }))
    } finally {
      setAnalyzingSet(prev => { const s = new Set(prev); s.delete(plotUrl); return s })
    }
  }

  const fmt = (ts: number) =>
    new Date(ts * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

  return (
    <div className="min-h-screen bg-[#0d0d14] text-zinc-100 flex flex-col">
      {/* header */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-white/8 bg-[#10101a]/80 backdrop-blur">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/')} className="text-zinc-500 hover:text-zinc-200 transition-colors">
            <ArrowLeft className="w-4 h-4" />
          </button>
          <Eye className="w-5 h-5 text-cyan-400" />
          <span className="font-bold tracking-tight text-sm">Vision Gallery</span>
          <span className="text-xs text-zinc-500">{plots.length} plots</span>
        </div>
        <button onClick={fetchPlots} className="flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-200 transition-colors">
          <RefreshCw className="w-3.5 h-3.5" /> Refresh
        </button>
      </header>

      <div className="flex-1 p-6">
        {loading ? (
          <div className="flex items-center justify-center h-64">
            <motion.div
              className="w-6 h-6 border-2 border-cyan-400 border-t-transparent rounded-full"
              animate={{ rotate: 360 }}
              transition={{ repeat: Infinity, duration: 0.8, ease: 'linear' }}
            />
          </div>
        ) : plots.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3 text-zinc-600">
            <ImageIcon className="w-10 h-10 opacity-30" />
            <p className="text-sm">No plots yet — run a MATLAB command to generate some</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
            {plots.map((p, i) => {
              const isAnalyzing = analyzingSet.has(p.url)
              const analysis = analysisMap[p.url]
              return (
                <motion.div
                  key={p.name}
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: i * 0.04 }}
                  className="group cursor-pointer rounded-xl overflow-hidden border border-white/8 bg-white/3 hover:border-cyan-400/30 transition-all"
                  onClick={() => setSelected(p)}
                >
                  <div className="aspect-square bg-black/20 overflow-hidden relative">
                    <img
                      src={`${API}${p.url}`}
                      alt={p.name}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                    />
                  </div>
                  <div className="px-2 py-1.5">
                    <p className="text-xs text-zinc-400 truncate">{p.name}</p>
                    <p className="text-xs text-zinc-600">{fmt(p.mtime)}</p>
                  </div>
                  {/* Analyze button */}
                  <div className="px-2 pb-2" onClick={e => e.stopPropagation()}>
                    {!analysis ? (
                      <button
                        onClick={e => analyzePlot(p.url, e)}
                        disabled={isAnalyzing}
                        className="flex items-center gap-1 w-full justify-center py-1 rounded text-xs transition-colors disabled:opacity-50"
                        style={{ backgroundColor: 'rgba(6,182,212,0.12)', color: '#22d3ee' }}
                      >
                        {isAnalyzing
                          ? <><Loader2 className="w-3 h-3 animate-spin" /> Analyzing…</>
                          : <><Search className="w-3 h-3" /> Analyze</>}
                      </button>
                    ) : (
                      <div
                        className="text-xs rounded p-1.5 leading-relaxed"
                        style={{ backgroundColor: 'rgba(6,182,212,0.08)', color: '#a1a1aa' }}
                      >
                        <div className="flex items-center gap-1 mb-1" style={{ color: '#22d3ee' }}>
                          <CheckCircle className="w-3 h-3 flex-shrink-0" />
                          <span className="font-medium">Analysis</span>
                        </div>
                        <p className="line-clamp-4">{analysis}</p>
                      </div>
                    )}
                  </div>
                  {p.name.endsWith('.gif') && (
                    <div className="absolute top-2 right-2 bg-cyan-500/80 text-white text-xs px-1.5 py-0.5 rounded">▶ GIF</div>
                  )}
                </motion.div>
              )
            })}
          </div>
        )}
      </div>

      {/* lightbox */}
      <AnimatePresence>
        {selected && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 p-8"
            onClick={() => setSelected(null)}
          >
            <motion.div
              initial={{ scale: 0.9 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0.9 }}
              className="bg-[#10101a] border border-white/10 rounded-2xl overflow-hidden max-w-3xl w-full"
              onClick={e => e.stopPropagation()}
            >
              <div className="px-4 py-3 border-b border-white/8 flex items-center justify-between">
                <span className="text-sm text-zinc-300">{selected.name}</span>
                <div className="flex items-center gap-3">
                  {!analysisMap[selected.url] && (
                    <button
                      onClick={e => analyzePlot(selected.url, e)}
                      disabled={analyzingSet.has(selected.url)}
                      className="flex items-center gap-1.5 text-xs px-2 py-1 rounded transition-colors disabled:opacity-50"
                      style={{ backgroundColor: 'rgba(6,182,212,0.15)', color: '#22d3ee' }}
                    >
                      {analyzingSet.has(selected.url)
                        ? <><Loader2 className="w-3 h-3 animate-spin" /> Analyzing…</>
                        : <><Search className="w-3 h-3" /> Analyze with AI</>}
                    </button>
                  )}
                  <span className="text-xs text-zinc-500">{fmt(selected.mtime)}</span>
                </div>
              </div>
              <img src={`${API}${selected.url}`} alt={selected.name} className="w-full object-contain max-h-[60vh]" />
              {analysisMap[selected.url] && (
                <div className="px-4 py-3 border-t border-white/8">
                  <div className="flex items-center gap-1.5 mb-2" style={{ color: '#22d3ee' }}>
                    <AlertTriangle className="w-3.5 h-3.5" />
                    <span className="text-xs font-semibold">AI Analysis</span>
                  </div>
                  <p className="text-xs text-zinc-400 leading-relaxed">{analysisMap[selected.url]}</p>
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
