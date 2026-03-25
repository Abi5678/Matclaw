import {
  Terminal, Brain, SlidersHorizontal, BarChart2, Search,
  FileCode2, FileText, Cpu, Stethoscope, GitBranch, Bot,
} from 'lucide-react'

export interface SkillMeta {
  label: string
  icon: React.ReactNode
  color: string      // tailwind text color
  bgColor: string    // tailwind bg color
}

const cls = 'w-3 h-3 inline'

export const SKILL_META: Record<string, SkillMeta> = {
  run_matlab:        { label: "MATLAB",       icon: <Terminal           className={cls} />, color: "text-orange-400",  bgColor: "bg-orange-400/10" },
  query_memory:      { label: "Memory",       icon: <Brain              className={cls} />, color: "text-purple-400",  bgColor: "bg-purple-400/10" },
  pid_optimizer:     { label: "PID",          icon: <SlidersHorizontal  className={cls} />, color: "text-blue-400",    bgColor: "bg-blue-400/10"   },
  signal_analyzer:   { label: "Signal",       icon: <BarChart2          className={cls} />, color: "text-cyan-400",    bgColor: "bg-cyan-400/10"   },
  workspace_auditor: { label: "Workspace",    icon: <Search             className={cls} />, color: "text-yellow-400",  bgColor: "bg-yellow-400/10" },
  code_reviewer:     { label: "Code Review",  icon: <FileCode2          className={cls} />, color: "text-green-400",   bgColor: "bg-green-400/10"  },
  report_generator:  { label: "Report",       icon: <FileText           className={cls} />, color: "text-indigo-400",  bgColor: "bg-indigo-400/10" },
  simulink_runner:   { label: "Simulink",     icon: <Cpu                className={cls} />, color: "text-teal-400",    bgColor: "bg-teal-400/10"   },
  file_doctor:       { label: "File Doctor",  icon: <Stethoscope        className={cls} />, color: "text-red-400",     bgColor: "bg-red-400/10"    },
  gitlab_reporter:   { label: "GitLab",       icon: <GitBranch          className={cls} />, color: "text-orange-500",  bgColor: "bg-orange-500/10" },
}

export function getSkillMeta(skill: string): SkillMeta {
  return SKILL_META[skill] ?? { label: skill, icon: <Bot className={cls} />, color: "text-zinc-400", bgColor: "bg-zinc-400/10" }
}
