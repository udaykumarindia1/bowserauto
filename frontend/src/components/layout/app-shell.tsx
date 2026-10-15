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
    <footer className="flex h-8 shrink-0 items-center gap-3 overflow-hidden border-t border-border/70 bg-card/75 px-3 text-[10px] font-medium text-muted-foreground backdrop-blur-xl sm:gap-4">
      <span className={cn("whitespace-nowrap", state === "OFFLINE" ? "text-hud-red" : "text-hud-green")}>● AI {state.toLowerCase()}</span>
      <span className={cn("hidden whitespace-nowrap sm:inline", connection === "open" ? "text-primary" : "text-hud-amber")}>Live link {connection}</span>
      {data && (
        <>
          <span className="whitespace-nowrap">{data.tasks.running} running</span>
          <span className="hidden whitespace-nowrap sm:inline">{data.tasks.queued} queued</span>
          <span className={cn("hidden whitespace-nowrap md:inline", data.tasks.needs_attention > 0 && "text-hud-amber")}>{data.tasks.needs_attention} attention</span>
          <span className="hidden whitespace-nowrap lg:inline">{data.sessions.ready}/{data.sessions.total} browsers</span>
          <span className="hidden whitespace-nowrap xl:inline">{data.system.environment} · {data.system.bus}</span>
        </>
      )}
      <span className="ml-auto hidden whitespace-nowrap lg:inline">
        <kbd className="hud-kbd">Ctrl+Space</kbd> talk <span className="px-1 text-border">·</span> <kbd className="hud-kbd">Esc</kbd> interrupt <span className="px-1 text-border">·</span> <kbd className="hud-kbd">/</kbd> console
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
  const setConsoleOpen = useJarvis((s) => s.setConsoleOpen);

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
      <div className="flex h-[100dvh] flex-col overflow-hidden bg-background">
        <Topbar />
        <div className="flex min-h-0 flex-1">
          <Sidebar attention={attention.data?.total ?? 0} />
          <main className="hud-grid relative min-h-0 min-w-0 flex-1 overflow-y-auto">{children}</main>
          {consoleOpen ? <button type="button" className="fixed inset-x-0 bottom-8 top-14 z-40 bg-slate-950/35 backdrop-blur-[2px] xl:hidden" aria-label="Close assistant console" onClick={() => setConsoleOpen(false)} /> : null}
          <JarvisConsole className={cn(
            "w-[360px] shrink-0 border-l border-border/70 bg-card/90 transition-[width,transform] xl:w-[380px]",
            !consoleOpen && "hidden",
            consoleOpen && "fixed bottom-8 right-0 top-14 z-50 w-[min(92vw,420px)] shadow-2xl xl:static xl:z-auto xl:shadow-none",
          )} />
        </div>
        <StatusStrip />
      </div>
      <CustomizeOverlay />
    </JarvisProvider>
  );
}
