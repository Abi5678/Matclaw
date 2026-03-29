import { useState, useEffect, useMemo } from 'react'
import { ThemeContext, type ThemeMode, type ResolvedTheme } from './themeContext'

function getSystemPreference(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'dark'
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function resolveTheme(mode: ThemeMode, systemPref: 'light' | 'dark'): ResolvedTheme {
  if (mode === 'auto') return systemPref
  if (mode === 'light-plus') return 'light-plus'
  if (mode === 'deep-blue') return 'deep-blue'
  return mode as ResolvedTheme
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() => {
    const saved = localStorage.getItem('matclaw-theme') as ThemeMode
    return saved || 'deep-blue'
  })

  const [systemPref, setSystemPref] = useState<'light' | 'dark'>(() => getSystemPreference())

  const resolved = useMemo(() => resolveTheme(mode, systemPref), [mode, systemPref])

  useEffect(() => {
    localStorage.setItem('matclaw-theme', mode)
    document.documentElement.setAttribute('data-theme', resolved)
  }, [mode, resolved])

  // Listen for system changes
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: light)')
    const handler = () => setSystemPref(getSystemPreference())
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  return (
    <ThemeContext.Provider value={{ mode, resolved, setMode }}>
      {children}
    </ThemeContext.Provider>
  )
}
