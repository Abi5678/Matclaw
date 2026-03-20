import * as React from 'react'

import { cn } from '../../lib/utils'

export type CardProps = React.HTMLAttributes<HTMLDivElement>

export function Card({ className, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-xl border border-border/[0.06] bg-bg-2/45 backdrop-blur-xl shadow-glass',
        className,
      )}
      {...props}
    />
  )
}

