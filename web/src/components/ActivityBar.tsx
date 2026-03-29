import { Plus, MessageSquare, Settings, Image, Bot } from 'lucide-react'

export type ActivityTab = 'chat' | 'agents' | 'settings'

interface ActivityBarProps {
  activeTab: ActivityTab
  onTabChange: (tab: ActivityTab) => void
  onNewSession: () => void
  onNavigateVision: () => void
  taskCount?: number
}

export default function ActivityBar({
  activeTab,
  onTabChange,
  onNewSession,
  onNavigateVision,
  taskCount = 0,
}: ActivityBarProps) {
  return (
    <div
      className="flex flex-col items-center w-12 flex-shrink-0 py-2 gap-1"
      style={{
        backgroundColor: 'var(--sidebar-bg)',
        borderRight: '1px solid var(--border-subtle)',
      }}
    >
      {/* New Task button */}
      <button
        onClick={onNewSession}
        title="New Task (Cmd+N)"
        className="w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-200 mb-2"
        style={{ color: 'var(--accent)' }}
        onMouseEnter={e => e.currentTarget.style.backgroundColor = 'var(--bg-hover)'}
        onMouseLeave={e => e.currentTarget.style.backgroundColor = 'transparent'}
      >
        <Plus className="w-5 h-5" />
      </button>

      {/* Task count */}
      {taskCount > 0 && (
        <div className="text-[10px] font-medium mb-2 px-1.5 py-0.5 rounded" style={{ color: 'var(--text-muted)' }}>
          Task {taskCount}
        </div>
      )}

      <div className="flex-1" />

      {/* Navigation icons */}
      <NavButton
        icon={<MessageSquare className="w-4.5 h-4.5" />}
        active={activeTab === 'chat'}
        onClick={() => onTabChange('chat')}
        title="Chat"
      />
      <NavButton
        icon={<Bot className="w-4.5 h-4.5" />}
        active={activeTab === 'agents'}
        onClick={() => onTabChange('agents')}
        title="Agents"
      />
      <NavButton
        icon={<Image className="w-4.5 h-4.5" />}
        active={false}
        onClick={onNavigateVision}
        title="Vision Gallery"
      />

      <div className="flex-1" />

      {/* Settings at bottom */}
      <NavButton
        icon={<Settings className="w-4.5 h-4.5" />}
        active={activeTab === 'settings'}
        onClick={() => onTabChange('settings')}
        title="Settings"
      />
    </div>
  )
}

function NavButton({ icon, active, onClick, title }: {
  icon: React.ReactNode
  active: boolean
  onClick: () => void
  title: string
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="w-9 h-9 rounded-lg flex items-center justify-center transition-all duration-200"
      style={{
        color: active ? 'var(--accent)' : 'var(--text-muted)',
        backgroundColor: active ? 'var(--accent-subtle)' : 'transparent',
      }}
      onMouseEnter={e => {
        if (!active) e.currentTarget.style.backgroundColor = 'var(--bg-hover)'
      }}
      onMouseLeave={e => {
        if (!active) e.currentTarget.style.backgroundColor = 'transparent'
      }}
    >
      {icon}
    </button>
  )
}
