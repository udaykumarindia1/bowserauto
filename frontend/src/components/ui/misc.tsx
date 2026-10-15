import * as React from "react";
import { AlertTriangle, Inbox } from "lucide-react";

import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse bg-muted", className)} />;
}

export function EmptyState({ icon: Icon = Inbox, title, description, action, className }: { icon?: React.ComponentType<{ className?: string }>; title: string; description?: string; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-card/45 p-8 text-center", className)}>
      <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10"><Icon className="h-5 w-5 text-primary" /></span>
      <p className="text-sm font-semibold text-foreground">{title}</p>
      {description ? <p className="mt-1 max-w-sm text-sm leading-relaxed text-muted-foreground">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-center gap-3 rounded-xl border border-hud-red/30 bg-hud-red/5 p-4 text-sm text-hud-red">
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
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4 border-b border-border/70 pb-4">
      <div>
        {eyebrow ? <p className="hud-label mb-1.5">{eyebrow}</p> : null}
        <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">{title}</h1>
        {description ? <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-muted-foreground">{description}</p> : null}
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
      <p className={cn("hud-glow mt-1 font-sans text-2xl font-bold tabular-nums", color)}>{value}</p>
      {hint ? <p className="mt-1 font-sans text-[10px] text-muted-foreground">{hint}</p> : null}
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
