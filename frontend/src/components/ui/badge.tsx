import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 border px-1.5 py-0.5 font-sans text-[9.5px] font-bold uppercase leading-4 tracking-[0.12em] transition-colors", {
  variants: {
    variant: {
      default: "border-primary/60 bg-hud-ghost text-primary",
      secondary: "border-border bg-secondary text-secondary-foreground",
      outline: "border-border text-foreground",
      success: "border-hud-green/50 bg-hud-green/10 text-hud-green",
      warning: "border-hud-amber/50 bg-hud-amber/10 text-hud-amber",
      destructive: "border-hud-red/50 bg-hud-red/10 text-hud-red",
      info: "border-primary/50 bg-hud-ghost text-primary",
      muted: "border-border bg-muted text-muted-foreground",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
