import * as React from "react";
import { AlertTriangle, Inbox } from "lucide-react";

import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse bg-muted", className)} />;
}

export function EmptyState({ icon: Icon = Inbox, title, description, action, className }: { icon?: React.ComponentType<{ className?: string }>; title: string; description?: string; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center border border-dashed border-border p-8 text-center", className)}>
      <Icon className="mb-3 h-8 w-8 text-muted-foreground/60" />
      <p className="hud-title">{title}</p>
      {description ? <p className="mt-1 max-w-sm text-xs text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-center gap-3 border border-hud-red/40 bg-hud-red/10 p-3 font-mono text-xs text-hud-red">
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span className="flex-1">{message}</span>
      {retry ? (
        <button onClick={retry} className="text-xs underline">
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function PageHeader({ title, description, actions, eyebrow }: { title: string; description?: string; actions?: React.ReactNode; eyebrow?: string }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3 border-b border-border pb-3">
      <div>
        {eyebrow ? <p className="hud-label mb-1">{eyebrow}</p> : null}
        <h1 className="hud-glow font-mono text-lg font-bold uppercase tracking-[0.25em] text-primary">◈ {title}</h1>
        {description ? <p className="mt-1 max-w-3xl text-xs text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Stat({ label, value, hint, tone, href }: { label: string; value: React.ReactNode; hint?: string; tone?: "default" | "success" | "warning" | "destructive"; href?: string }) {
  const color = tone === "success" ? "text-hud-green" : tone === "warning" ? "text-hud-amber" : tone === "destructive" ? "text-hud-red" : "text-primary";
  const body = (
    <div className={cn("hud-panel hud-corners p-3", href && "transition-colors hover:border-primary/60")}>
      <p className="hud-label">{label}</p>
      <p className={cn("hud-glow mt-1 font-mono text-2xl font-bold tabular-nums", color)}>{value}</p>
      {hint ? <p className="mt-1 font-mono text-[10px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
  return href ? <a href={href}>{body}</a> : body;
}

export function KeyValue({ items, className }: { items: [string, React.ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs", className)}>
      {items.map(([k, v]) => (
        <React.Fragment key={k}>
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="truncate font-medium">{v}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}
