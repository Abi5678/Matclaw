import { createContext } from 'react'

export type ThemeMode = 'deep-blue' | 'dark' | 'light' | 'light-plus' | 'auto'
export type ResolvedTheme = 'deep-blue' | 'dark' | 'light' | 'light-plus'

export interface ThemeContextType {
  mode: ThemeMode
  resolved: ResolvedTheme
  setMode: (mode: ThemeMode) => void
}

export const ThemeContext = createContext<ThemeContextType | undefined>(undefined)
