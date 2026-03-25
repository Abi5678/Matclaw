import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Eye, ArrowLeft, RefreshCw, Image as ImageIcon } from 'lucide-react'
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
            {plots.map((p, i) => (
              <motion.div
                key={p.name}
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: i * 0.04 }}
                className="group cursor-pointer rounded-xl overflow-hidden border border-white/8 bg-white/3 hover:border-cyan-400/30 transition-all"
                onClick={() => setSelected(p)}
              >
                <div className="aspect-square bg-black/20 overflow-hidden">
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
                {p.name.endsWith('.gif') && (
                  <div className="absolute top-2 right-2 bg-cyan-500/80 text-white text-xs px-1.5 py-0.5 rounded">▶ GIF</div>
                )}
              </motion.div>
            ))}
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
                <span className="text-xs text-zinc-500">{fmt(selected.mtime)}</span>
              </div>
              <img src={`${API}${selected.url}`} alt={selected.name} className="w-full object-contain max-h-[70vh]" />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
