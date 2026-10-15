"use client";

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { JarvisConsole } from "@/components/hud/console";
import { CustomizeOverlay } from "@/components/hud/customize-overlay";
import { HudCore } from "@/components/hud/hud-core";
import { JarvisProvider } from "@/components/hud/jarvis-provider";
import { useAuth } from "@/hooks/use-auth";
import { useRealtimeSync } from "@/hooks/use-realtime";
import { api } from "@/lib/api";
import { useJarvis } from "@/lib/jarvis-store";
import { useAppStore } from "@/lib/store";
import { cn } from "@/lib/utils";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

/** Footer status strip: environment · bus · worker/task counts · shortcuts. */
function StatusStrip() {
  const connection = useAppStore((s) => s.connection);
  const state = useJarvis((s) => s.state);
  const { data } = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 15000 });
  return (
    <footer className="flex h-7 shrink-0 items-center gap-4 overflow-hidden border-t border-border bg-hud-dark px-3 font-mono text-[9px] uppercase tracking-[0.18em] text-muted-foreground">
      <span className={cn(state === "OFFLINE" ? "text-hud-red" : "text-hud-green")}>● AI CORE {state}</span>
      <span className={cn(connection === "open" ? "text-primary" : "text-hud-amber")}>WS {connection}</span>
      {data && (
        <>
          <span>{data.tasks.running} RUNNING</span>
          <span>{data.tasks.queued} QUEUED</span>
          <span className={cn(data.tasks.needs_attention > 0 && "text-hud-amber")}>{data.tasks.needs_attention} ATTENTION</span>
          <span>{data.sessions.ready}/{data.sessions.total} BROWSERS</span>
          <span>ENV {data.system.environment}</span>
          <span>BUS {data.system.bus}</span>
        </>
      )}
      <span className="ml-auto hidden md:inline">
        PROTOCOL BOWSER-1 · <kbd className="hud-kbd">Ctrl+Space</kbd> talk · <kbd className="hud-kbd">Esc</kbd> interrupt · <kbd className="hud-kbd">/</kbd> console
      </span>
    </footer>
  );
}

/** Authenticated application frame: redirects to /login when the session is missing. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { auth, authChecked, refresh, apply } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const consoleOpen = useJarvis((s) => s.consoleOpen);

  React.useEffect(() => {
    if (!authChecked) void refresh();
  }, [authChecked, refresh]);

  React.useEffect(() => {
    const onUnauthorized = () => {
      apply(null);
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    };
    window.addEventListener("ba:unauthorized", onUnauthorized);
    return () => window.removeEventListener("ba:unauthorized", onUnauthorized);
  }, [apply, router, pathname]);

  React.useEffect(() => {
    if (authChecked && !auth) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [authChecked, auth, router, pathname]);

  useRealtimeSync(!!auth);
  const attention = useQuery({ queryKey: ["tasks", "attention-count"], queryFn: () => api.tasks.list({ status: "attention", limit: 1 }), enabled: !!auth, refetchInterval: 30_000 });

  if (!auth) {
    return (
      <div className="hud-grid flex h-screen flex-col items-center justify-center gap-4 bg-background">
        <div className="h-56 w-56">
          <HudCore state="INITIALISING" showWave={false} compact />
        </div>
        <p className="hud-label animate-blink">Initialising workspace…</p>
      </div>
    );
  }
  return (
    <JarvisProvider enabled={!!auth}>
      <div className="flex h-screen flex-col overflow-hidden bg-background">
        <Topbar />
        <div className="flex min-h-0 flex-1">
          <Sidebar attention={attention.data?.total ?? 0} />
          <main className="hud-grid relative min-h-0 min-w-0 flex-1 overflow-y-auto">{children}</main>
          <JarvisConsole className={cn("w-[340px] shrink-0 border-l border-border transition-[width]", !consoleOpen && "hidden")} />
        </div>
        <StatusStrip />
      </div>
      <CustomizeOverlay />
    </JarvisProvider>
  );
}
