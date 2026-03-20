import * as React from 'react'

import { cn } from '../../lib/utils'

export function Separator({
  className,
  orientation = 'horizontal',
  decorative = true,
  ...props
}: React.HTMLAttributes<HTMLHRElement> & {
  orientation?: 'horizontal' | 'vertical'
  decorative?: boolean
}) {
  return (
    <hr
      role={decorative ? 'presentation' : undefined}
      className={cn(
        orientation === 'horizontal' ? 'h-px w-full' : 'h-full w-px',
        'border-border/10',
        className,
      )}
      {...props}
    />
  )
}

