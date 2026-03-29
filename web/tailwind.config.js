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
        ui: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        // Semantic theme colors using CSS vars
        'th-base': 'var(--bg-base)',
        'th-surface': 'var(--bg-surface)',
        'th-elevated': 'var(--bg-elevated)',
        'th-hover': 'var(--bg-hover)',
        'th-active': 'var(--bg-active)',
        'th-input': 'var(--bg-input)',

        'th-text': 'var(--text-primary)',
        'th-text-2': 'var(--text-secondary)',
        'th-muted': 'var(--text-muted)',

        'th-border': 'var(--border-default)',
        'th-border-subtle': 'var(--border-subtle)',

        'th-accent': 'var(--accent)',
        'th-accent-hover': 'var(--accent-hover)',
        'th-accent-subtle': 'var(--accent-subtle)',

        'th-sidebar': 'var(--sidebar-bg)',
        'th-header': 'var(--header-bg)',
        'th-card': 'var(--card-bg)',
        'th-card-border': 'var(--card-border)',

        'th-success': 'var(--success)',
        'th-warning': 'var(--warning)',
        'th-error': 'var(--error)',
      },
      boxShadow: {
        'glass': '0 8px 32px rgba(0,0,0,0.08)',
        'glass-dark': '0 8px 32px rgba(0,0,0,0.35)',
        'accent-glow': '0 0 20px var(--accent-glow)',
      },
      borderRadius: {
        xl: '1.25rem',
      },
      keyframes: {
        subtlePulse: {
          '0%, 100%': { boxShadow: '0 0 0 0 var(--accent-glow)' },
          '50%': { boxShadow: '0 0 0 8px transparent' },
        },
      },
      animation: {
        subtlePulse: 'subtlePulse 2.4s ease-in-out infinite',
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}
