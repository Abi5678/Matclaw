import type { ThemeMode } from './themeContext'

// API base URL — configurable via VITE_API_URL env var for non-localhost deployments.
// Falls back to same-origin (empty string) in production, localhost:8000 in dev.
export const API = import.meta.env.VITE_API_URL
  ?? (import.meta.env.DEV ? 'http://localhost:8000' : '')

// WebSocket base for terminal — derives from API automatically
export const API_WS = API.replace(/^http/, 'ws') + '/api/terminal'

export const THEME_OPTIONS: { value: ThemeMode; label: string }[] = [
  { value: 'deep-blue', label: 'Deep Blue' },
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'light-plus', label: 'Light+' },
  { value: 'auto', label: 'Auto' },
]
