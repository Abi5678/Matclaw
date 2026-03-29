import { Workflow, Terminal, Code2, Globe, Bot, Settings } from 'lucide-react'

export type WorkspaceTab = 'flow' | 'terminal' | 'editor' | 'browser' | 'agents' | 'settings'

interface WorkspaceTabsProps {
  activeTab: WorkspaceTab
  onTabChange: (tab: WorkspaceTab) => void
}

const TABS: { id: WorkspaceTab; label: string; icon: React.ReactNode }[] = [
  { id: 'flow', label: 'Flow', icon: <Workflow className="w-3.5 h-3.5" /> },
  { id: 'terminal', label: 'Terminal', icon: <Terminal className="w-3.5 h-3.5" /> },
  { id: 'editor', label: 'Editor', icon: <Code2 className="w-3.5 h-3.5" /> },
  { id: 'browser', label: 'Browser', icon: <Globe className="w-3.5 h-3.5" /> },
  { id: 'agents', label: 'Agents', icon: <Bot className="w-3.5 h-3.5" /> },
  { id: 'settings', label: 'Settings', icon: <Settings className="w-3.5 h-3.5" /> },
]

export default function WorkspaceTabs({ activeTab, onTabChange }: WorkspaceTabsProps) {
  return (
    <div
      className="flex items-center h-9 flex-shrink-0 overflow-x-auto"
      style={{
        backgroundColor: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      {TABS.map(tab => {
        const isActive = activeTab === tab.id
        return (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className="flex items-center gap-1.5 px-4 h-full text-xs font-medium whitespace-nowrap transition-colors duration-200 relative"
            style={{
              color: isActive ? 'var(--text-primary)' : 'var(--text-muted)',
              backgroundColor: isActive ? 'var(--bg-base)' : 'transparent',
            }}
            onMouseEnter={e => {
              if (!isActive) e.currentTarget.style.color = 'var(--text-secondary)'
            }}
            onMouseLeave={e => {
              if (!isActive) e.currentTarget.style.color = 'var(--text-muted)'
            }}
          >
            <span style={{ color: isActive ? 'var(--accent)' : undefined }}>{tab.icon}</span>
            {tab.label}
            {isActive && (
              <span
                className="absolute bottom-0 left-0 right-0 h-[2px]"
                style={{ backgroundColor: 'var(--accent)' }}
              />
            )}
          </button>
        )
      })}
      {/* Filler for remaining space */}
      <div className="flex-1" style={{ borderBottom: '1px solid var(--border-subtle)' }} />
    </div>
  )
}
