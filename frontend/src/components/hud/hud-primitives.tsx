"use client";

import { useEffect, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Framed HUD panel with optional "▸ TITLE" header and corner brackets. */
export function HudPanel({
  title,
  actions,
  children,
  className,
  bodyClassName,
  corners = false,
  padded = true,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  corners?: boolean;
  padded?: boolean;
}) {
  return (
    <section className={cn("hud-panel flex min-h-0 flex-col", corners && "hud-corners", className)}>
      {(title || actions) && (
        <header className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2">
          {title ? <h2 className="hud-section border-0 pb-0">{title}</h2> : <span />}
          {actions && <div className="flex items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cn("min-h-0 flex-1", padded && "p-3", bodyClassName)}>{children}</div>
    </section>
  );
}

export function HudSectionTitle({ children, className, right }: { children: ReactNode; className?: string; right?: ReactNode }) {
  return (
    <div className={cn("flex items-center justify-between gap-2", className)}>
      <h3 className="hud-section flex-1">{children}</h3>
      {right}
    </div>
  );
}

/** Blinking-dot status pill (Mark-LV dashboard style). */
export function HudPill({ children, tone = "default", pulse = false, className }: { children: ReactNode; tone?: "default" | "green" | "amber" | "red" | "orange" | "dim"; pulse?: boolean; className?: string }) {
  const color = {
    default: "text-primary",
    green: "text-hud-green",
    amber: "text-hud-amber",
    red: "text-hud-red",
    orange: "text-hud-orange",
    dim: "text-muted-foreground",
  }[tone];
  return (
    <span className={cn("hud-pill", color, className)}>
      <span className={cn("hud-dot bg-current", pulse && "animate-blink")} />
      {children}
    </span>
  );
}

/** Horizontal telemetry bar (CPU / MEM / NET …) ported from Mark-LV's MetricBar. */
export function MetricBar({ label, value, display, tone = "primary", max = 100 }: { label: string; value: number | null; display?: string; tone?: "primary" | "amber" | "green" | "orange" | "red"; max?: number }) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
  const colour = {
    primary: "bg-primary",
    amber: "bg-hud-amber",
    green: "bg-hud-green",
    orange: "bg-hud-orange",
    red: "bg-hud-red",
  }[tone];
  const text = {
    primary: "text-primary",
    amber: "text-hud-amber",
    green: "text-hud-green",
    orange: "text-hud-orange",
    red: "text-hud-red",
  }[tone];
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between font-sans text-[10px] font-bold tracking-[0.12em]">
        <span className="text-muted-foreground">{label}</span>
        <span className={cn(value == null ? "text-muted-foreground/60" : text)}>{display ?? (value == null ? "--" : `${Math.round(value)}%`)}</span>
      </div>
      <div className="relative h-1.5 w-full overflow-hidden border border-border bg-hud-bar">
        <div className={cn("h-full transition-[width] duration-700 ease-out", colour)} style={{ width: `${pct}%`, boxShadow: "0 0 8px currentColor" }} />
      </div>
    </div>
  );
}

/** Live clock + date in the header, HH:MM:SS 24h like the desktop HUD. */
export function HudClock({ className }: { className?: string }) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  if (!now) return <div className={cn("text-right font-sans", className)}>--:--:--</div>;
  const time = now.toLocaleTimeString(undefined, { hour12: false });
  const date = now.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short", year: "numeric" }).toUpperCase();
  return (
    <div className={cn("text-right font-sans", className)}>
      <div className="hud-glow text-base font-bold leading-none tracking-[0.12em] text-primary">{time}</div>
      <div className="mt-1 text-[9px] tracking-[0.2em] text-muted-foreground">{date}</div>
    </div>
  );
}

/** Small uppercase badge used in the sys monitor footer ("AI CORE / ACTIVE"). */
export function HudBadge({ top, bottom, tone = "primary", className }: { top: string; bottom: string; tone?: "primary" | "green" | "dim" | "amber" | "red"; className?: string }) {
  const colour = { primary: "text-primary", green: "text-hud-green", dim: "text-muted-foreground", amber: "text-hud-amber", red: "text-hud-red" }[tone];
  return (
    <div className={cn("border border-hud-a bg-hud-panel2 px-2 py-1.5 text-center font-sans text-[9px] font-bold leading-tight tracking-[0.14em]", colour, className)}>
      <div>{top}</div>
      <div>{bottom}</div>
    </div>
  );
}

/** Typewriter text — reveals characters progressively (Mark-LV LogWidget behaviour). */
export function Typewriter({ text, enabled = true, speed = 14, onDone, className }: { text: string; enabled?: boolean; speed?: number; onDone?: () => void; className?: string }) {
  const [shown, setShown] = useState(enabled ? 0 : text.length);
  useEffect(() => {
    if (!enabled) {
      setShown(text.length);
      return;
    }
    setShown(0);
    let i = 0;
    const step = Math.max(1, Math.round(text.length / 160));
    const id = window.setInterval(() => {
      i = Math.min(text.length, i + step);
      setShown(i);
      if (i >= text.length) {
        window.clearInterval(id);
        onDone?.();
      }
    }, speed);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, enabled]);
  const done = shown >= text.length;
  return (
    <span className={cn(!done && "animate-typing-caret", className)}>
      {text.slice(0, shown)}
    </span>
  );
}
