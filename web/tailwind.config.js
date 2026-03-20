/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '1rem',
    },
    extend: {
      fontFamily: {
        display: ['Space Grotesk', 'system-ui', 'sans-serif'],
        sans: ['Space Grotesk', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        bg: 'hsl(var(--bg) / <alpha-value>)',
        'bg-2': 'hsl(var(--bg-2) / <alpha-value>)',
        text: 'hsl(var(--text) / <alpha-value>)',
        muted: 'hsl(var(--muted) / <alpha-value>)',
        border: 'hsl(var(--border) / <alpha-value>)',
        accent: 'hsl(var(--accent) / <alpha-value>)',
        'accent-2': 'hsl(var(--accent-2) / <alpha-value>)',
        'dot-red': 'hsl(var(--dot-red) / <alpha-value>)',
        'dot-amber': 'hsl(var(--dot-amber) / <alpha-value>)',
        'dot-green': 'hsl(var(--dot-green) / <alpha-value>)',
      },
      boxShadow: {
        accentGlow:
          '0 0 0 1px hsl(var(--accent) / 0.12), 0 4px 24px hsl(var(--accent) / 0.08), 0 0 40px hsl(var(--accent) / 0.1)',
        glass: '0 8px 32px hsl(0 0% 0% / 0.35)',
      },
      borderRadius: {
        xl: '1.25rem',
      },
      keyframes: {
        marquee: {
          from: { transform: 'translateX(0)' },
          to: { transform: 'translateX(-50%)' },
        },
        marqueeReverse: {
          from: { transform: 'translateX(-50%)' },
          to: { transform: 'translateX(0)' },
        },
        subtlePulse: {
          '0%, 100%': { boxShadow: '0 0 0 0 hsl(var(--accent) / 0.35)' },
          '50%': { boxShadow: '0 0 0 8px hsl(var(--accent) / 0)' },
        },
      },
      animation: {
        marquee: 'marquee 32s linear infinite',
        marqueeReverse: 'marqueeReverse 32s linear infinite',
        subtlePulse: 'subtlePulse 2.4s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}

