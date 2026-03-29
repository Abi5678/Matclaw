import type { ThemeMode } from './themeContext'

export const THEME_OPTIONS: { value: ThemeMode; label: string }[] = [
  { value: 'deep-blue', label: 'Deep Blue' },
  { value: 'dark', label: 'Dark' },
  { value: 'light', label: 'Light' },
  { value: 'light-plus', label: 'Light+' },
  { value: 'auto', label: 'Auto' },
]
