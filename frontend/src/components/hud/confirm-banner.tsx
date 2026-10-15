"use client";

import { useEffect } from "react";

import { cn } from "@/lib/utils";

/**
 * "⚠ CONFIRM" gate for irreversible actions (Mark-LV ConfirmBanner). Rendered as a HUD
 * overlay with real YES / NO buttons; Enter confirms, Escape cancels.
 */
export function ConfirmBanner({
  open,
  title,
  detail,
  confirmLabel = "YES — PROCEED",
  cancelLabel = "NO — CANCEL",
  tone = "warning",
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "warning" | "danger";
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
      if (e.key === "Enter" && !busy) onConfirm();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onConfirm, onCancel]);

  if (!open) return null;
  const accent = tone === "danger" ? "text-hud-red border-hud-red/60" : "text-hud-amber border-hud-amber/60";
  return (
    <div className="fixed inset-0 z-[70] flex items-start justify-center bg-background/70 p-4 pt-[12vh] backdrop-blur-[2px]" role="alertdialog" aria-modal="true" aria-label={title}>
      <div className={cn("hud-corners w-full max-w-lg border bg-card p-5 shadow-2xl", accent)}>
        <div className={cn("hud-title flex items-center gap-2", tone === "danger" ? "text-hud-red" : "text-hud-amber")}>
          <span className="text-base leading-none">⚠</span> CONFIRM
        </div>
        <div className="mt-3 font-sans text-sm font-bold uppercase tracking-[0.12em] text-foreground">{title}</div>
        {detail && <p className="mt-2 whitespace-pre-wrap font-sans text-xs leading-relaxed text-muted-foreground">{detail}</p>}
        <div className="mt-5 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onConfirm}
            className={cn(
              "border px-4 py-2 font-sans text-[11px] font-bold uppercase tracking-[0.16em] transition-colors disabled:opacity-50",
              tone === "danger" ? "border-hud-red bg-hud-red/15 text-hud-red hover:bg-hud-red/25" : "border-hud-amber bg-hud-amber/15 text-hud-amber hover:bg-hud-amber/25",
            )}
          >
            {busy ? "WORKING…" : confirmLabel}
          </button>
          <button type="button" onClick={onCancel} className="border border-border bg-background px-4 py-2 font-sans text-[11px] font-bold uppercase tracking-[0.16em] text-muted-foreground hover:border-primary hover:text-primary">
            {cancelLabel}
          </button>
          <span className="ml-auto flex items-center gap-1 text-[9px] text-muted-foreground">
            <kbd className="hud-kbd">Enter</kbd> confirm <kbd className="hud-kbd">Esc</kbd> cancel
          </span>
        </div>
      </div>
    </div>
  );
}
