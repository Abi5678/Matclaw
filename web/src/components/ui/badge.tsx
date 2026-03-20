import * as React from 'react'

import { cn } from '../../lib/utils'

export type BadgeProps = React.HTMLAttributes<HTMLSpanElement> & {
  variant?: 'default' | 'secondary'
}

export function Badge({ className, variant = 'default', ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold tracking-wide',
        variant === 'default'
          ? 'border-accent/25 bg-accent/12 text-accent'
          : 'border-border/15 bg-bg-2/35 text-text',
        className,
      )}
      {...props}
    />
  )
}

