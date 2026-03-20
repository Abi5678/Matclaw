import { useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion, useScroll, useSpring, useTransform } from 'framer-motion'
import {
  ArrowRight,
  ArrowUpRight,
  ExternalLink,
  Github,
  Menu,
  MessageCircle,
  Sparkles,
  Star,
  Terminal as TerminalIcon,
  X,
  Zap,
  Cpu,
  Workflow,
  ShieldCheck,
} from 'lucide-react'

import { Button } from '../components/ui/button'
import { Card } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Separator } from '../components/ui/separator'
import { cn } from '../lib/utils'

import type { ReactNode } from 'react'

type Feature = {
  id: string
  icon: ReactNode
  title: string
  description: string
}

type Step = {
  id: string
  title: string
  description: string
}

function Starfield({ className }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const parseHsl = (value: string) => {
      const parts = value.trim().split(/\s+/)
      if (parts.length < 3) return null
      const h = Number(parts[0])
      const s = Number(parts[1].replace('%', ''))
      const l = Number(parts[2].replace('%', ''))
      if (Number.isNaN(h) || Number.isNaN(s) || Number.isNaN(l)) return null
      return { h, s: s / 100, l: l / 100 }
    }

    const hslToRgb = (h: number, s: number, l: number) => {
      // h in degrees, s/l in [0..1]
      const c = (1 - Math.abs(2 * l - 1)) * s
      const hp = h / 60
      const x = c * (1 - Math.abs((hp % 2) - 1))
      let r1 = 0
      let g1 = 0
      let b1 = 0
      if (0 <= hp && hp < 1) {
        r1 = c
        g1 = x
      } else if (1 <= hp && hp < 2) {
        r1 = x
        g1 = c
      } else if (2 <= hp && hp < 3) {
        g1 = c
        b1 = x
      } else if (3 <= hp && hp < 4) {
        g1 = x
        b1 = c
      } else if (4 <= hp && hp < 5) {
        r1 = x
        b1 = c
      } else if (5 <= hp && hp < 6) {
        r1 = c
        b1 = x
      }
      const m = l - c / 2
      const r = Math.round((r1 + m) * 255)
      const g = Math.round((g1 + m) * 255)
      const b = Math.round((b1 + m) * 255)
      return { r, g, b }
    }

    const rootStyles = getComputedStyle(document.documentElement)
    const accentHsl = parseHsl(rootStyles.getPropertyValue('--accent'))
    const accent2Hsl = parseHsl(rootStyles.getPropertyValue('--accent-2'))

    const accentRgb = accentHsl ? hslToRgb(accentHsl.h, accentHsl.s, accentHsl.l) : { r: 0, g: 0, b: 0 }
    const accent2Rgb = accent2Hsl ? hslToRgb(accent2Hsl.h, accent2Hsl.s, accent2Hsl.l) : { r: 0, g: 0, b: 0 }

    const stars = Array.from({ length: 140 }).map((_, i) => ({
      id: i,
      x: Math.random(),
      y: Math.random(),
      z: Math.random(),
      r: Math.random() * 1.4 + 0.2,
      s: Math.random() * 0.6 + 0.1,
    }))

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const { width, height } = canvas.getBoundingClientRect()
      canvas.width = Math.floor(width * dpr)
      canvas.height = Math.floor(height * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    resize()
    window.addEventListener('resize', resize)

    let last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05)
      last = now

      const { width, height } = canvas.getBoundingClientRect()
      ctx.clearRect(0, 0, width, height)

      // Subtle film-grain vibe: tiny alpha noise
      ctx.globalCompositeOperation = 'source-over'

      for (const star of stars) {
        star.y -= dt * star.s * (0.18 + star.z)
        if (star.y < -0.05) {
          star.y = 1.05
          star.x = Math.random()
        }

        const px = star.x * width
        const py = star.y * height

        const alpha = 0.15 + star.z * 0.55
        const glow = 0.6 + star.z * 1.2

        ctx.beginPath()
        ctx.fillStyle = `rgba(${accentRgb.r}, ${accentRgb.g}, ${accentRgb.b}, ${alpha})`
        ctx.arc(px, py, star.r * glow, 0, Math.PI * 2)
        ctx.fill()

        ctx.beginPath()
        ctx.fillStyle = `rgba(${accent2Rgb.r}, ${accent2Rgb.g}, ${accent2Rgb.b}, ${alpha * 0.25})`
        ctx.arc(px, py, star.r * 2.2 * glow, 0, Math.PI * 2)
        ctx.fill()
      }

      rafRef.current = window.requestAnimationFrame(tick)
    }

    rafRef.current = window.requestAnimationFrame(tick)

    return () => {
      window.removeEventListener('resize', resize)
      if (rafRef.current) window.cancelAnimationFrame(rafRef.current)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className={cn('pointer-events-none absolute inset-0 -z-10', className)}
      aria-hidden="true"
    />
  )
}

function useTyping(text: string, speedMs: number) {
  const [value, setValue] = useState('')

  useEffect(() => {
    let i = 0
    setValue('')
    const id = window.setInterval(() => {
      i += 1
      setValue(text.slice(0, i))
      if (i >= text.length) window.clearInterval(id)
    }, speedMs)

    return () => window.clearInterval(id)
  }, [text, speedMs])

  return value
}

function TerminalWindow() {
  const tabs = useMemo(
    () => [
      { id: 'one-liner', label: 'Daemon' },
      { id: 'npm', label: 'Web UI' },
      { id: 'hackable', label: 'MCP' },
    ],
    [],
  )

  const [active, setActive] = useState<(typeof tabs)[number]['id']>('one-liner')

  const scriptsByTab: Record<
    (typeof tabs)[number]['id'],
    { title: string; command: string }
  > = useMemo(
    () => ({
      'one-liner': {
        title: 'Daemon',
        command:
          'cd MatClaw && python3.11 -m pip install -e . && python3.11 main.py',
      },
      npm: {
        title: 'Web UI',
        command: 'cd MatClaw/web && npm install && npm run dev',
      },
      hackable: {
        title: 'Hackable',
        command: 'python3.11 -m src.matclaw.mcp.server --http',
      },
    }),
    [],
  )

  const activeCommand = scriptsByTab[active].command
  const typed = useTyping(activeCommand, 14)

  const highlight = (raw: string) => {
    // Tiny “good enough” highlighting: color key tokens.
    const tokens = raw.split(/(\s+)/).filter(Boolean)
    return tokens.map((t, idx) => {
      const clean = t.trim()
      const isSpace = t.length !== clean.length
      if (isSpace) return <span key={idx}>{t}</span>

      const isCmd = /^(npm|python3\.11|python|cd|curl)$/i.test(clean)
      const isFlag = /^--/.test(clean)
      const isRepo = /MatClaw|matclaw|src\.|pip|install|vite|main\.py/i.test(clean)

      const cls = isCmd
        ? 'text-accent'
        : isFlag
          ? 'text-accent/80'
          : isRepo
            ? 'text-accent-2'
            : 'text-muted'

      return (
        <span key={idx} className={cls}>
          {t}
        </span>
      )
    })
  }

  return (
    <Card className="relative overflow-hidden shadow-accentGlow ring-1 ring-accent/10">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(700px_280px_at_20%_0%,hsl(var(--accent)/0.22),transparent_58%)] opacity-70" />
      <div className="pointer-events-none absolute -inset-px rounded-xl bg-gradient-to-b from-accent/15 via-transparent to-transparent opacity-40" />

      <div className="relative p-4 sm:p-5">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-dot-red/80" aria-hidden="true" />
            <span className="h-2.5 w-2.5 rounded-full bg-dot-amber/80" aria-hidden="true" />
            <span className="h-2.5 w-2.5 rounded-full bg-dot-green/70" aria-hidden="true" />
          </div>
          <div className="text-xs text-muted">{scriptsByTab[active].title}</div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setActive(t.id)}
              className={cn(
                'rounded-full border px-3 py-1 text-xs transition',
                t.id === active
                  ? 'border-accent/35 bg-accent/10 text-accent'
                  : 'border-border/10 bg-bg-2/20 text-muted hover:bg-bg-2/30',
              )}
              aria-pressed={t.id === active}
              type="button"
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="mt-4 overflow-hidden rounded-xl border border-border/10 bg-bg-2/30 backdrop-blur">
          <div className="flex items-center gap-2 border-b border-border/10 px-3 py-2">
            <TerminalIcon className="h-4 w-4 text-muted" />
            <span className="text-xs text-muted">install</span>
          </div>
          <div className="px-3 py-4">
            <pre className="whitespace-pre-wrap font-mono text-sm leading-relaxed">
              <code className="block">
                <span className="text-muted">$ </span>
                {highlight(typed)}
                <span className="inline-block animate-pulse text-accent/80">▍</span>
              </code>
            </pre>
            <div className="mt-3 text-xs text-muted">
              This terminal is interactive. It types the command and switches presets instantly.
            </div>
          </div>
        </div>
      </div>
    </Card>
  )
}

function Reveal({
  children,
  delay = 0,
  className,
}: {
  children: ReactNode
  delay?: number
  className?: string
}) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 22 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.18 }}
      transition={{ duration: 0.7, delay }}
    >
      {children}
    </motion.div>
  )
}

export default function Landing() {
  const githubRepo = 'Abi5678/Matclaw'
  const prefersReducedMotion = useReducedMotion()
  const [stars, setStars] = useState<number | null>(null)
  const [contributors, setContributors] = useState<string[]>([])
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    let alive = true
    const run = async () => {
      try {
        const [owner, repo] = githubRepo.split('/')
        const [repoRes, contribRes] = await Promise.all([
          fetch(`https://api.github.com/repos/${owner}/${repo}`),
          fetch(`https://api.github.com/repos/${owner}/${repo}/contributors?per_page=6`),
        ])
        if (!repoRes.ok || !contribRes.ok) throw new Error('GitHub request failed')

        const repoData: { stargazers_count: number } = await repoRes.json()
        const contribData: Array<{ login: string }> = await contribRes.json()

        if (!alive) return
        setStars(repoData.stargazers_count)
        setContributors(contribData.map((c) => c.login))
      } catch {
        if (!alive) return
        setStars(null)
        setContributors([])
      }
    }
    run()
    return () => {
      alive = false
    }
  }, [])

  const realityChecks = useMemo(
    () => [
      { label: 'Open Source Repo', value: 'Public', detail: `github.com/${githubRepo}` },
      { label: 'Stars', value: stars === null ? '—' : stars.toLocaleString(), detail: 'Live from GitHub API' },
      { label: 'MATLAB Engine', value: 'Integrated', detail: 'R2025b tested in local setup' },
      { label: 'Execution Model', value: 'RPI Loop', detail: 'Research → Plan → Implement with logs' },
      { label: 'Safety', value: 'HITL', detail: 'Human approval for long runs' },
      { label: 'Interface', value: 'MCP + Streamlit', detail: 'Agent tooling + control plane' },
    ],
    [githubRepo, stars],
  )

  const features: Feature[] = useMemo(
    () => [
      {
        id: 'f1',
        icon: <Workflow className="h-5 w-5 text-accent" />,
        title: 'Research–Plan–Execute (RPI)',
        description: 'Context in, actions out. The agent loops with self-correction and audit checkpoints.',
      },
      {
        id: 'f2',
        icon: <ShieldCheck className="h-5 w-5 text-accent" />,
        title: 'HITL-gated safety',
        description: 'Long runs pause for your Go/No-Go. No surprises. No chaos.',
      },
      {
        id: 'f3',
        icon: <Cpu className="h-5 w-5 text-accent" />,
        title: 'MCP tool bridge',
        description: 'Run code, query memory, trigger skills, and read status through a stable interface.',
      },
      {
        id: 'f4',
        icon: <Zap className="h-5 w-5 text-accent" />,
        title: 'Sentry + digital twin sync',
        description: 'File-triggered sentry runs and sync pipelines keep your research artifacts fresh.',
      },
      {
        id: 'f5',
        icon: <Sparkles className="h-5 w-5 text-accent" />,
        title: 'Cinematic UI polish',
        description: 'Glass depth, premium micro-interactions, and smooth framer-motion reveals.',
      },
      {
        id: 'f6',
        icon: <TerminalIcon className="h-5 w-5 text-accent" />,
        title: 'Interactive terminal blocks',
        description: 'Type-on-switch install snippets so visitors get it instantly.',
      },
    ],
    [],
  )

  const steps: Step[] = useMemo(
    () => [
      {
        id: 's1',
        title: 'Research',
        description: 'Pull context from memory and recent artifacts. Learn what already worked (and what didn’t).',
      },
      {
        id: 's2',
        title: 'Plan',
        description: 'Select the next action, outline the approach, and set constraints before touching heavy compute.',
      },
      {
        id: 's3',
        title: 'Execute',
        description: 'Run skills against your live kernel. Record everything. Audit results. Self-correct if needed.',
      },
      {
        id: 's4',
        title: 'Report',
        description: 'Generate a crisp lab report with linked artifacts so your team can verify and iterate.',
      },
    ],
    [],
  )

  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  const { scrollY } = useScroll()
  const parallaxY = prefersReducedMotion ? 0 : -65
  const glowLift = useTransform(scrollY, [0, 600], [0, parallaxY])
  const glowOpacity = useSpring(useTransform(scrollY, [0, 420], prefersReducedMotion ? [1, 1] : [1, 0.2]), {
    stiffness: 120,
    damping: 18,
  })

  const heroContainer = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: { staggerChildren: prefersReducedMotion ? 0 : 0.11, delayChildren: prefersReducedMotion ? 0 : 0.06 },
    },
  }
  const heroItem = {
    hidden: { opacity: 0, y: prefersReducedMotion ? 0 : 26 },
    show: {
      opacity: 1,
      y: 0,
      transition: { duration: prefersReducedMotion ? 0 : 0.62, ease: [0.22, 1, 0.36, 1] as const },
    },
  }



  return (
    <div className="min-h-screen bg-transparent">
      <Starfield className="opacity-[0.85]" />

      <header className="sticky top-0 z-50">
        <motion.nav
          initial={false}
          animate={{
            backgroundColor: scrolled ? 'hsl(var(--bg-2) / 0.72)' : 'hsl(var(--bg) / 0)',
            boxShadow: scrolled ? '0 12px 40px hsl(0 0% 0% / 0.25)' : '0 0 0 transparent',
          }}
          transition={{ duration: 0.28 }}
          className={cn(
            'mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-4 py-3',
            'backdrop-blur-xl supports-[backdrop-filter]:bg-bg/35',
          )}
        >
          <a href="#" className="flex items-center gap-3">
            <div className="relative h-10 w-10">
              <motion.div
                animate={
                  prefersReducedMotion ? {} : { rotate: [0, 8, 0] }
                }
                transition={{ duration: 2.2, repeat: Infinity, ease: 'easeInOut' }}
                className="absolute inset-0 rounded-2xl border border-accent/25 bg-bg-2/40 shadow-[0_0_30px_hsl(var(--accent)/0.12)]"
              />
              <div className="absolute inset-0 grid place-items-center">
                <Sparkles className="h-5 w-5 text-accent" aria-hidden />
              </div>
            </div>
            <div className="leading-tight">
              <div className="font-display text-sm font-bold tracking-tight text-text">MatClaw</div>
              <div className="text-xs text-muted">Autonomous agentic OS</div>
            </div>
          </a>

          <div className="hidden items-center justify-center gap-6 md:flex">
            {[
              { label: 'Docs', href: '#features' },
              { label: 'GitHub', href: `https://github.com/${githubRepo}` },
              { label: 'Discord', href: '#' },
              { label: 'Pricing', href: '#open-source' },
            ].map((link) => (
              <motion.a
                key={link.label}
                href={link.href}
                target={link.href.startsWith('http') ? '_blank' : undefined}
                rel={link.href.startsWith('http') ? 'noreferrer' : undefined}
                className="text-sm text-muted hover:text-text transition-colors"
                whileHover={{ y: prefersReducedMotion ? 0 : -1 }}
              >
                {link.label}
              </motion.a>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <Button
              asChild
              variant="ghost"
              className="hidden sm:inline-flex"
            >
              <a href={`https://github.com/${githubRepo}`} target="_blank" rel="noreferrer">
                <Github className="h-4 w-4" />
                GitHub
              </a>
            </Button>
            <Button asChild className="hidden border border-accent/25 sm:inline-flex">
              <a href="#quick-start">
                Get Started <ArrowRight className="h-4 w-4" />
              </a>
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="md:hidden"
              type="button"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              onClick={() => setMenuOpen((o) => !o)}
            >
              {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </Button>
          </div>
        </motion.nav>

        <AnimatePresence>
          {menuOpen ? (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.22 }}
              className="border-b border-border/10 bg-bg-2/90 backdrop-blur-xl md:hidden"
            >
              <div className="mx-auto flex max-w-6xl flex-col gap-1 px-4 py-3">
                {[
                  { label: 'Docs', href: '#features' },
                  { label: 'GitHub', href: `https://github.com/${githubRepo}` },
                  { label: 'Discord', href: '#' },
                  { label: 'Pricing', href: '#open-source' },
                  { label: 'Quick Start', href: '#quick-start' },
                ].map((link) => (
                  <a
                    key={link.label}
                    href={link.href}
                    target={link.href.startsWith('http') ? '_blank' : undefined}
                    rel={link.href.startsWith('http') ? 'noreferrer' : undefined}
                    className="flex items-center justify-between rounded-lg px-3 py-2.5 text-sm text-text hover:bg-bg-2/60"
                    onClick={() => setMenuOpen(false)}
                  >
                    {link.label}
                    {link.href.startsWith('http') ? <ExternalLink className="h-3.5 w-3.5 text-muted" /> : null}
                  </a>
                ))}
                <Button asChild className="mt-2 w-full border border-accent/25">
                  <a href="#quick-start" onClick={() => setMenuOpen(false)}>
                    Get Started <ArrowRight className="h-4 w-4" />
                  </a>
                </Button>
              </div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </header>

      <main>
        {/* HERO — mascot first, staggered cinematic entrance */}
        <section className="relative" aria-labelledby="hero-heading">
          <div className="absolute inset-0 -z-10">
            <motion.div
              style={{ translateY: glowLift, opacity: glowOpacity }}
              className="absolute left-1/2 top-0 h-[560px] w-[820px] -translate-x-1/2 rounded-full bg-[radial-gradient(circle_at_center,hsl(var(--accent)/0.32),transparent_58%)] blur-[14px]"
            />
          </div>

          <div className="mx-auto max-w-6xl px-4 pb-16 pt-12 sm:pt-20 lg:pb-28">
            <motion.div
              className="flex flex-col items-center text-center"
              variants={heroContainer}
              initial="hidden"
              animate="show"
            >
              <motion.div variants={heroItem} className="relative">
                <motion.div
                  className="mx-auto h-[5.5rem] w-[5.5rem] rounded-3xl border border-accent/30 bg-bg-2/50 shadow-accentGlow backdrop-blur-xl"
                  animate={
                    prefersReducedMotion
                      ? {}
                      : { y: [0, -10, 0], rotate: [0, 4, 0] }
                  }
                  transition={{ duration: 2.8, repeat: Infinity, ease: 'easeInOut' }}
                  aria-hidden="true"
                >
                  <div className="flex h-full w-full items-center justify-center">
                    <Sparkles className="h-9 w-9 text-accent drop-shadow-[0_0_12px_hsl(var(--accent)/0.45)]" />
                  </div>
                </motion.div>
                <div className="pointer-events-none absolute inset-0 -z-10 scale-110 blur-2xl opacity-40 bg-[radial-gradient(circle,hsl(var(--accent)/0.35),transparent_65%)]" />
              </motion.div>

              <motion.div variants={heroItem} className="mt-8 max-w-4xl px-2">
                <h1
                  id="hero-heading"
                  className="font-display text-[2.5rem] font-bold leading-[1.05] tracking-[-0.02em] text-accent sm:text-5xl lg:text-6xl"
                >
                  THE AI THAT ACTUALLY DOES THINGS.
                </h1>
                <p className="mx-auto mt-5 max-w-2xl text-base leading-relaxed text-muted sm:text-lg">
                  MatClaw runs persistent <span className="text-text/90">Research → Plan → Implement</span> loops on
                  your MATLAB stack—audited, self-correcting, and tool-driven. Premium SaaS polish; cyberpunk soul.
                </p>
              </motion.div>

              <motion.div variants={heroItem} className="mt-8">
                <motion.div
                  whileHover={prefersReducedMotion ? {} : { scale: 1.02 }}
                  whileTap={prefersReducedMotion ? {} : { scale: 0.99 }}
                  className="relative inline-flex max-w-[90vw] flex-wrap items-center justify-center gap-2 rounded-full border border-border/[0.08] bg-bg-2/45 px-4 py-2.5 shadow-glass backdrop-blur-xl"
                >
                  <Badge className="border-accent/30 bg-accent/15 text-accent shadow-[0_0_16px_hsl(var(--accent)/0.2)]">
                    NEW
                  </Badge>
                  <span className="text-sm text-muted">
                    Hybrid RPI UI · MCP bridge · HITL gates—ship without the theater.
                  </span>
                  <ArrowRight className="h-4 w-4 shrink-0 text-accent" aria-hidden />
                </motion.div>
              </motion.div>

              <motion.div
                variants={heroItem}
                className="mt-10 flex w-full max-w-md flex-col items-stretch gap-3 sm:max-w-none sm:flex-row sm:justify-center"
              >
                <motion.div whileHover={prefersReducedMotion ? {} : { scale: 1.03 }} whileTap={{ scale: 0.98 }}>
                  <Button
                    asChild
                    className="w-full border border-accent/25 shadow-accentGlow motion-safe:animate-subtlePulse sm:w-auto"
                  >
                    <a href="#quick-start">
                      Start the loop <ArrowRight className="h-4 w-4" />
                    </a>
                  </Button>
                </motion.div>
                <motion.div whileHover={prefersReducedMotion ? {} : { scale: 1.02 }} whileTap={{ scale: 0.98 }}>
                  <Button asChild variant="outline" className="w-full sm:w-auto">
                    <a href="#how-it-works">
                      See how it works <ArrowUpRight className="h-4 w-4" />
                    </a>
                  </Button>
                </motion.div>
              </motion.div>

              <motion.div variants={heroItem} className="mt-12 w-full max-w-4xl">
                <div className="grid gap-4 sm:grid-cols-3">
                  {[
                    { label: 'Self-correction', value: 'On' },
                    { label: 'Artifacts + logs', value: 'Linked' },
                    { label: 'Safety gates', value: 'HITL' },
                  ].map((s) => (
                    <motion.div
                      key={s.label}
                      whileHover={prefersReducedMotion ? {} : { y: -3, boxShadow: '0 0 0 1px hsl(var(--accent) / 0.2)' }}
                      className="rounded-2xl border border-border/[0.06] bg-bg-2/35 p-4 text-left shadow-glass backdrop-blur-md transition-shadow"
                    >
                      <div className="text-sm text-muted">{s.label}</div>
                      <div className="mt-2 font-display text-2xl font-bold text-text">{s.value}</div>
                    </motion.div>
                  ))}
                </div>
              </motion.div>
            </motion.div>
          </div>
        </section>

        {/* REALITY CHECK (factual, no fake testimonials) */}
        <section className="relative pb-16">
          <div className="mx-auto max-w-6xl px-4">
            <div className="max-w-2xl">
              <h2 className="text-2xl font-bold text-text sm:text-3xl">What is real today</h2>
              <p className="mt-2 text-muted text-sm sm:text-base">
                No invented quotes. Just concrete signals from the project and runtime setup.
              </p>
            </div>

            <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {realityChecks.map((item, idx) => (
                <Reveal key={item.label} delay={0.04 * idx}>
                  <Card className="h-full p-5">
                    <div className="text-xs uppercase tracking-[0.08em] text-muted">{item.label}</div>
                    <div className="mt-2 text-xl font-bold text-text">{item.value}</div>
                    <p className="mt-2 text-sm text-muted">{item.detail}</p>
                  </Card>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* QUICK START */}
        <section id="quick-start" className="relative pb-16">
          <div className="mx-auto max-w-6xl px-4">
            <div className="flex flex-col items-start justify-between gap-6 md:flex-row md:items-end">
              <Reveal>
                <div>
                  <h2 className="text-2xl font-bold text-text sm:text-3xl">
                    Quick Start (for real humans)
                  </h2>
                  <p className="mt-2 text-muted text-sm sm:text-base">
                    Pick a preset. Watch the terminal type. Start shipping.
                  </p>
                </div>
              </Reveal>
              <Reveal delay={0.1} className="w-full md:w-auto">
                <div className="flex items-center gap-2 rounded-2xl border border-border/10 bg-bg-2/25 px-4 py-3 text-sm text-muted">
                  <TerminalIcon className="h-4 w-4 text-accent" />
                  Works great on macOS.
                </div>
              </Reveal>
            </div>

            <div className="mt-8">
              <Reveal delay={0.1}>
                <TerminalWindow />
              </Reveal>
            </div>
          </div>
        </section>

        {/* FEATURES */}
        <section id="features" className="relative pb-16">
          <div className="mx-auto max-w-6xl px-4">
            <Reveal>
              <div className="max-w-2xl">
                <h2 className="text-2xl font-bold text-text sm:text-3xl">Features that feel expensive</h2>
                <p className="mt-2 text-muted text-sm sm:text-base">
                  Glass depth, premium gradients, and interaction polish. Underneath: a real orchestration engine.
                </p>
              </div>
            </Reveal>

            <div className="mt-10 grid grid-cols-1 gap-4 lg:auto-rows-fr lg:grid-cols-12">
              {features.map((f, idx) => {
                const bento =
                  [
                    'lg:col-span-6 lg:row-span-2',
                    'lg:col-span-6',
                    'lg:col-span-6',
                    'lg:col-span-4',
                    'lg:col-span-4',
                    'lg:col-span-4',
                  ][idx] ?? 'lg:col-span-4'
                return (
                  <Reveal key={f.id} delay={0.05 + idx * 0.04} className={cn(bento, 'h-full')}>
                    <motion.div
                      className="h-full"
                      whileHover={
                        prefersReducedMotion
                          ? {}
                          : {
                              scale: 1.02,
                              rotate: idx % 2 === 0 ? -0.4 : 0.4,
                            }
                      }
                      transition={{ type: 'spring', stiffness: 280, damping: 18 }}
                    >
                      <Card
                        className={cn(
                          'group h-full p-5 transition-shadow duration-300',
                          'hover:border-accent/25 hover:shadow-accentGlow',
                          idx === 0 && 'lg:min-h-[260px]',
                        )}
                      >
                        <div
                          className={cn(
                            'flex h-full gap-4',
                            idx === 0 ? 'flex-col sm:flex-row lg:flex-col lg:justify-between' : 'items-start',
                          )}
                        >
                          <div className="mt-1 flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-accent/20 bg-accent/10 shadow-[0_0_20px_hsl(var(--accent)/0.12)] transition group-hover:border-accent/35">
                            {f.icon}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="font-display text-lg font-bold text-text">{f.title}</div>
                            <p className="mt-2 text-sm leading-relaxed text-muted">{f.description}</p>
                          </div>
                        </div>
                      </Card>
                    </motion.div>
                  </Reveal>
                )
              })}
            </div>
          </div>
        </section>

        {/* HOW IT WORKS */}
        <section id="how-it-works" className="relative pb-16">
          <div className="mx-auto max-w-6xl px-4">
            <Reveal>
              <div className="max-w-2xl">
                <h2 className="text-2xl font-bold text-text sm:text-3xl">How it works</h2>
                <p className="mt-2 text-muted text-sm sm:text-base">
                  Simple steps. Serious outcomes. Self-correction when reality disagrees.
                </p>
              </div>
            </Reveal>

            <div className="mt-10 grid gap-6 lg:grid-cols-2 lg:gap-8">
              <div className="relative">
                <div className="absolute left-6 top-0 bottom-0 w-px bg-border/10" aria-hidden="true" />
                <div className="space-y-6">
                  {steps.map((s, idx) => (
                    <Reveal key={s.id} delay={idx * 0.05}>
                      <div className="relative">
                        <div className="absolute left-3 top-1.5 h-10 w-10 rounded-2xl border border-accent/20 bg-accent/10 grid place-items-center">
                          <div className="font-mono text-sm text-accent">{idx + 1}</div>
                        </div>
                        <div className="ml-16 rounded-2xl border border-border/10 bg-bg-2/25 p-5 backdrop-blur">
                          <div className="text-lg font-bold text-text">{s.title}</div>
                          <p className="mt-2 text-sm leading-relaxed text-muted">{s.description}</p>
                        </div>
                      </div>
                    </Reveal>
                  ))}
                </div>
              </div>

              <Reveal delay={0.1}>
                <Card className="h-full p-6">
                  <div className="flex items-center gap-3">
                    <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-accent/20 bg-accent/10">
                      <ShieldCheck className="h-5 w-5 text-accent" />
                    </div>
                    <div>
                      <div className="text-lg font-bold text-text">Built to verify</div>
                      <div className="text-sm text-muted">Audit checkpoints before heavy compute.</div>
                    </div>
                  </div>
                  <Separator className="my-5" />
                  <div className="space-y-3 text-sm text-muted">
                    <div className="flex items-start gap-3">
                      <div className="mt-1 h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
                      HITL gates for long runs so humans stay in control.
                    </div>
                    <div className="flex items-start gap-3">
                      <div className="mt-1 h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
                      Lab journal as a single source of truth with linked artifacts.
                    </div>
                    <div className="flex items-start gap-3">
                      <div className="mt-1 h-2 w-2 rounded-full bg-accent" aria-hidden="true" />
                      MCP tool bridge keeps execution consistent across clients.
                    </div>
                  </div>
                  <div className="mt-6">
                    <Button asChild className="w-full border border-accent/25">
                      <a href="https://github.com/Abi5678/Matclaw" target="_blank" rel="noreferrer">
                        Explore the code <ArrowUpRight className="h-4 w-4" />
                      </a>
                    </Button>
                  </div>
                </Card>
              </Reveal>
            </div>
          </div>
        </section>

        {/* OPEN SOURCE CTA */}
        <section id="open-source" className="relative pb-20">
          <div className="mx-auto max-w-6xl px-4">
            <Reveal>
              <div className="flex flex-col items-start justify-between gap-6 md:flex-row md:items-end">
                <div>
                  <h2 className="text-2xl font-bold text-text sm:text-3xl">Open source, open receipts</h2>
                  <p className="mt-2 text-muted text-sm sm:text-base">
                    Star it if you want this kind of engineering energy in your stack.
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <div className="rounded-2xl border border-border/10 bg-bg-2/25 px-4 py-3 text-sm text-muted">
                    <span className="text-accent font-semibold">
                      {stars === null ? '...' : stars.toLocaleString()}
                    </span>{' '}
                    stars
                  </div>
                  <div className="hidden sm:block">
                    <div className="flex items-center">
                      {(contributors.length ? contributors : ['matclaw', 'labops', 'mcpfan', 'shipmore']).slice(0, 5).map((login, i) => (
                        <div
                          key={`${login}-${i}`}
                          className="relative -ml-2 h-10 w-10 overflow-hidden rounded-full border border-bg-2/60 bg-accent/10 grid place-items-center"
                          title={login}
                          aria-hidden="true"
                        >
                          <span className="font-mono text-xs text-accent">
                            {login.slice(0, 2).toUpperCase()}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </Reveal>

            <div className="mt-10">
              <Reveal delay={0.1}>
                <Card className="p-6 md:p-8">
                  <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
                    <div className="max-w-2xl">
                      <div className="flex items-center gap-3">
                        <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-accent/20 bg-accent/10">
                          <Star className="h-5 w-5 text-accent" />
                        </div>
                        <div>
                          <div className="text-lg font-bold text-text">MatClaw is built in the open</div>
                          <div className="text-sm text-muted">Help shape the agent OS for MATLAB engineers.</div>
                        </div>
                      </div>
                      <p className="mt-4 text-sm leading-relaxed text-muted">
                        Your star funds experiments. Your contributions make the loop sharper.
                      </p>
                    </div>

                    <div className="flex w-full flex-col gap-3 sm:w-auto sm:flex-row">
                      <Button asChild className="w-full border border-accent/25 sm:w-auto">
                        <a
                          href={`https://github.com/${githubRepo}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Star on GitHub <Star className="h-4 w-4" />
                        </a>
                      </Button>
                      <Button
                        variant="outline"
                        className="w-full sm:w-auto"
                        onClick={() => {
                          const el = document.getElementById('quick-start')
                          el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                        }}
                      >
                        Try the install
                      </Button>
                    </div>
                  </div>
                </Card>
              </Reveal>
            </div>
          </div>
        </section>

        {/* FOOTER */}
        <footer className="border-t border-border/10 bg-bg/30 backdrop-blur">
          <div className="mx-auto max-w-6xl px-4 py-12">
            <div className="grid gap-10 md:grid-cols-3">
              <div className="md:col-span-1">
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-2xl border border-accent/25 bg-bg-2/40 grid place-items-center">
                    <Sparkles className="h-5 w-5 text-accent" />
                  </div>
                  <div>
                    <div className="text-sm font-bold text-text">MatClaw</div>
                    <div className="text-xs text-muted">Cyberpunk-premium agent OS</div>
                  </div>
                </div>
                <p className="mt-4 text-sm leading-relaxed text-muted">
                  Research. Plan. Execute. Audit. Report. Repeat.
                </p>
              </div>

              <div className="grid gap-6 sm:grid-cols-2 md:col-span-2">
                <div>
                  <div className="text-sm font-semibold text-text">Product</div>
                  <ul className="mt-3 space-y-2 text-sm">
                    <li>
                      <a className="text-muted hover:text-text transition-colors" href="#features">
                        Features
                      </a>
                    </li>
                    <li>
                      <a className="text-muted hover:text-text transition-colors" href="#how-it-works">
                        How it works
                      </a>
                    </li>
                    <li>
                      <a className="text-muted hover:text-text transition-colors" href="#quick-start">
                        Quick start
                      </a>
                    </li>
                  </ul>
                </div>

                <div>
                  <div className="text-sm font-semibold text-text">Community</div>
                  <ul className="mt-3 space-y-2 text-sm">
                    <li>
                      <a
                        className="text-muted hover:text-text transition-colors"
                        href="https://github.com/Abi5678/Matclaw"
                        target="_blank"
                        rel="noreferrer"
                      >
                        GitHub
                      </a>
                    </li>
                    <li>
                      <a className="text-muted hover:text-text transition-colors" href="#">
                        Discord
                      </a>
                    </li>
                    <li>
                      <a className="text-muted hover:text-text transition-colors" href="#">
                        Pricing
                      </a>
                    </li>
                  </ul>
                </div>

                <div>
                  <div className="text-sm font-semibold text-text">Legal</div>
                  <ul className="mt-3 space-y-2 text-sm">
                    <li>
                      <a className="text-muted hover:text-text transition-colors" href="#">
                        Terms
                      </a>
                    </li>
                    <li>
                      <a className="text-muted hover:text-text transition-colors" href="#">
                        Privacy
                      </a>
                    </li>
                    <li>
                      <a className="text-muted hover:text-text transition-colors" href="#">
                        Security
                      </a>
                    </li>
                  </ul>
                </div>
              </div>
            </div>

            <Separator className="my-8" />

            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div className="text-sm text-muted">
                © {new Date().getFullYear()} MatClaw. All rights reserved.
              </div>
              <div className="flex items-center gap-3">
                <Button variant="ghost" asChild>
                  <a href="https://github.com/Abi5678/Matclaw" target="_blank" rel="noreferrer">
                    <Github className="h-4 w-4" />
                    GitHub
                  </a>
                </Button>
                <Button variant="ghost" asChild>
                  <a href="#" onClick={(e) => e.preventDefault()}>
                    <MessageCircle className="h-4 w-4" />
                    Community
                  </a>
                </Button>
              </div>
            </div>
          </div>
        </footer>
      </main>
    </div>
  )
}

