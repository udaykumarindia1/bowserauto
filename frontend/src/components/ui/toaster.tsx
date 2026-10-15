"use client";

import { X } from "lucide-react";

import { useAppStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export function Toaster() {
  const toasts = useAppStore((s) => s.toasts);
  const dismiss = useAppStore((s) => s.dismissToast);
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-80 flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cn(
            "pointer-events-auto hud-corners border border-border bg-card p-3 font-mono text-xs shadow-lg animate-in slide-in-from-right-4",
            t.variant === "destructive" && "border-hud-red/60",
            t.variant === "success" && "border-hud-green/60",
            t.variant === "warning" && "border-hud-amber/60",
          )}
        >
          <div className="flex items-start gap-2">
            <div className="flex-1">
              <p className="hud-title text-[10px]">{t.title}</p>
              {t.description ? <p className="mt-0.5 text-xs text-muted-foreground">{t.description}</p> : null}
            </div>
            <button onClick={() => dismiss(t.id)} className="text-muted-foreground hover:text-foreground">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
