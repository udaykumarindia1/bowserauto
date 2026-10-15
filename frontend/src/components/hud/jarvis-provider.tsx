"use client";

import { createContext, useContext, type ReactNode } from "react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { useJarvisController, type JarvisController } from "@/hooks/use-jarvis";
import { useJarvis } from "@/lib/jarvis-store";

const JarvisContext = createContext<JarvisController | null>(null);

export function useJarvisConsole(): JarvisController {
  const ctx = useContext(JarvisContext);
  if (!ctx) throw new Error("useJarvisConsole must be used inside JarvisProvider");
  return ctx;
}

/** Mounts the single JARVIS controller and the global confirmation gate. */
export function JarvisProvider({ enabled, children }: { enabled: boolean; children: ReactNode }) {
  const controller = useJarvisController(enabled);
  const pending = useJarvis((s) => s.pendingConfirm);
  const danger = !!pending && /stop|clear|delete|cancel/i.test(pending.title);
  return (
    <JarvisContext.Provider value={controller}>
      {children}
      <ConfirmBanner
        open={!!pending}
        title={pending?.title ?? ""}
        detail={pending?.detail}
        tone={danger ? "danger" : "warning"}
        onConfirm={() => void controller.confirmPending()}
        onCancel={controller.cancelPending}
      />
    </JarvisContext.Provider>
  );
}
