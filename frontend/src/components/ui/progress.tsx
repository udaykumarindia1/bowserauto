import * as React from "react";

import { cn } from "@/lib/utils";

export function Progress({ value, className, indicatorClassName }: { value: number; className?: string; indicatorClassName?: string }) {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div className={cn("relative h-2 w-full overflow-hidden border border-border bg-hud-bar", className)} role="progressbar" aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={100}>
      <div className={cn("h-full bg-primary shadow-[0_0_8px_hsl(var(--hud-pri))] transition-all duration-500", indicatorClassName)} style={{ width: `${clamped}%` }} />
    </div>
  );
}
