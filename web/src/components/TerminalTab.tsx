import { useEffect, useRef } from 'react'
import { Terminal } from 'xterm'
import { FitAddon } from 'xterm-addon-fit'
import 'xterm/css/xterm.css'

import { API_WS } from '../lib/constants'

export default function TerminalTab() {
  const terminalRef = useRef<HTMLDivElement>(null)
  const term = useRef<Terminal | null>(null)
  const fitAddon = useRef<FitAddon | null>(null)
  const ws = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!terminalRef.current) return

    term.current = new Terminal({
      cursorBlink: true,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      fontSize: 14,
      theme: {
        background: 'transparent',
        foreground: 'var(--text-primary)',
        cursor: 'var(--accent)',
      }
    })

    fitAddon.current = new FitAddon()
    term.current.loadAddon(fitAddon.current)
    term.current.open(terminalRef.current)
    fitAddon.current.fit()

    ws.current = new WebSocket(API_WS)

    ws.current.onopen = () => {
      // Guard: Ensure fitAddon is ready before proposing dimensions
      if (fitAddon.current) {
        try {
          const dims = fitAddon.current.proposeDimensions()
          if (dims) {
            ws.current?.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }))
          }
        } catch (e) {
          console.warn('Failed to propose terminal dimensions in onopen:', e)
        }
      }
    }

    ws.current.onmessage = (event) => {
      term.current?.write(event.data)
    }

    term.current.onData((data) => {
      if (ws.current?.readyState === WebSocket.OPEN) {
        ws.current.send(data)
      }
    })

    const handleResize = () => {
      if (!term.current || !fitAddon.current) return
      try {
        fitAddon.current.fit()
        const dims = fitAddon.current.proposeDimensions()
        if (dims && ws.current?.readyState === WebSocket.OPEN) {
          ws.current.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }))
        }
      } catch (e) {
        console.warn('Terminal resize failed:', e)
      }
    }

    // Delay the initial fit to ensure the DOM has finished layout
    const initialFit = setTimeout(() => {
      if (fitAddon.current) {
        try {
          fitAddon.current.fit()
        } catch (e) {
          console.warn('Initial terminal fit failed:', e)
        }
      }
    }, 100)

    window.addEventListener('resize', handleResize)

    return () => {
      clearTimeout(initialFit)
      window.removeEventListener('resize', handleResize)
      ws.current?.close()
      term.current?.dispose()
    }
  }, [])

  return (
    <div className="flex-1 w-full h-full overflow-hidden p-4" style={{ backgroundColor: 'var(--bg-elevated)' }}>
      <div 
        className="w-full h-full rounded-md shadow-inner p-3" 
        style={{ backgroundColor: 'var(--bg-canvas)', border: '1px solid var(--border-subtle)' }}
      >
        <div ref={terminalRef} className="w-full h-full" />
      </div>
    </div>
  )
}
