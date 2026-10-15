"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Building2, Check, LogOut, Moon, OctagonX, PanelLeft, PanelRight, Settings2, Sun, User } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudClock, HudPill } from "@/components/hud/hud-primitives";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown";
import { Hint } from "@/components/ui/tooltip";
import { useAuth } from "@/hooks/use-auth";
import { api } from "@/lib/api";
import { useJarvis } from "@/lib/jarvis-store";
import { toast, useAppStore } from "@/lib/store";
import { cn, timeAgo } from "@/lib/utils";

const APP_VERSION = "v1.0";

function IconButton({ label, onClick, active, children, className }: { label: string; onClick?: () => void; active?: boolean; children: React.ReactNode; className?: string }) {
  return (
    <Hint label={label}>
      <button
        type="button"
        onClick={onClick}
        aria-label={label}
        className={cn("flex h-8 w-8 items-center justify-center border border-border text-muted-foreground transition-colors hover:border-primary hover:text-primary", active && "border-primary/60 bg-hud-ghost text-primary", className)}
      >
        {children}
      </button>
    </Hint>
  );
}

/** HUD header: version badge · panel toggles · J.A.R.V.I.S title · status pills · clock · user. */
export function Topbar() {
  const { auth, logout, switchOrganization } = useAuth();
  const theme = useAppStore((s) => s.theme);
  const setTheme = useAppStore((s) => s.setTheme);
  const connection = useAppStore((s) => s.connection);
  const prefs = useJarvis((s) => s.prefs);
  const consoleOpen = useJarvis((s) => s.consoleOpen);
  const setConsoleOpen = useJarvis((s) => s.setConsoleOpen);
  const monitorOpen = useJarvis((s) => s.monitorOpen);
  const setMonitorOpen = useJarvis((s) => s.setMonitorOpen);
  const queryClient = useQueryClient();
  const notifications = useQuery({ queryKey: ["notifications"], queryFn: () => api.notifications.list(30), enabled: !!auth, refetchInterval: 60_000 });
  const activeTasks = useQuery({ queryKey: ["tasks", "active-count"], queryFn: () => api.tasks.list({ status: "active", limit: 1 }), enabled: !!auth, refetchInterval: 30_000 });
  const [confirmStop, setConfirmStop] = React.useState(false);
  const stopAll = useMutation({
    mutationFn: () => api.tasks.stopAll(),
    onSuccess: (r) => {
      toast({ title: "Emergency stop", description: `${r.stopped} task(s) stopped`, variant: "warning" });
      useJarvis.getState().push({ role: "sys", text: `EMERGENCY STOP — ${r.stopped} task(s) cancelled.`, level: "warning" });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      setConfirmStop(false);
    },
    onError: (e: Error) => toast({ title: "Stop failed", description: e.message, variant: "destructive" }),
  });
  const markAll = useMutation({ mutationFn: () => api.notifications.markAllRead(), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }) });
  const unread = notifications.data?.unread ?? 0;
  const active = activeTasks.data?.total ?? 0;
  const displayName = prefs.assistantName.toUpperCase().split("").join(".").replace(/\.(?=\s|$)/g, "");

  return (
    <header className="relative flex h-14 shrink-0 items-center gap-2 border-b border-border bg-hud-dark px-3">
      <span className="hud-pill text-hud-dim">{APP_VERSION}</span>
      <IconButton label={monitorOpen ? "Hide navigation labels + SYS MONITOR" : "Show navigation + SYS MONITOR"} onClick={() => setMonitorOpen(!monitorOpen)} active={monitorOpen}>
        <PanelLeft className="h-3.5 w-3.5" />
      </IconButton>
      <IconButton label="Customise assistant (name, voice, colour)" onClick={() => window.dispatchEvent(new CustomEvent("ba:customize"))}>
        <Settings2 className="h-3.5 w-3.5" />
      </IconButton>

      <div className="flex min-w-0 flex-1 flex-col items-center justify-center px-3 text-center">
        <div className="hud-glow truncate font-mono text-lg font-bold leading-none tracking-[0.35em] text-primary">{displayName}</div>
        <div className="mt-1 hidden max-w-full truncate font-mono text-[9px] uppercase tracking-[0.3em] text-muted-foreground md:block">Browser Automation Workspace · {auth?.organization.name}</div>
      </div>
      <div className="hidden items-center gap-1.5 lg:flex">
        <HudPill tone={connection === "open" ? "green" : "amber"} pulse={connection !== "open"}>
          {connection === "open" ? "LINK SECURE" : `LINK ${connection}`}
        </HudPill>
        <Link href="/tasks">
          <HudPill tone={active > 0 ? "default" : "dim"} pulse={active > 0}>
            {active} ACTIVE
          </HudPill>
        </Link>
      </div>
      <Hint label="Stop everything: cancels all queued and running tasks">
        <button
          type="button"
          onClick={() => setConfirmStop(true)}
          disabled={auth?.role === "viewer"}
          className="flex h-8 items-center gap-1.5 border border-hud-red/70 bg-hud-red/10 px-2.5 font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-hud-red transition-colors hover:bg-hud-red/25 disabled:opacity-40"
        >
          <OctagonX className="h-3.5 w-3.5" /> <span className="hidden md:inline">STOP EVERYTHING</span>
        </button>
      </Hint>
      <IconButton label={theme === "dark" ? "Day HUD" : "Night HUD"} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
        {theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
      </IconButton>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button type="button" className="relative flex h-8 w-8 items-center justify-center border border-border text-muted-foreground hover:border-primary hover:text-primary" aria-label="Notifications">
            <Bell className="h-3.5 w-3.5" />
            {unread > 0 ? <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center bg-hud-amber px-1 font-mono text-[9px] font-bold text-black">{unread > 99 ? "99+" : unread}</span> : null}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-80">
          <div className="flex items-center justify-between px-2 py-1.5">
            <DropdownMenuLabel className="hud-label p-0">Notifications</DropdownMenuLabel>
            {unread > 0 ? (
              <button className="font-mono text-[10px] uppercase tracking-[0.1em] text-primary hover:underline" onClick={() => markAll.mutate()}>
                Mark all read
              </button>
            ) : null}
          </div>
          <DropdownMenuSeparator />
          <div className="max-h-80 overflow-y-auto">
            {(notifications.data?.items ?? []).length === 0 ? <p className="p-3 text-center font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">No notifications yet</p> : null}
            {(notifications.data?.items ?? []).map((n) => {
              const taskId = (n.data as { task_id?: string })?.task_id;
              return (
                <DropdownMenuItem key={n.id} asChild>
                  <Link href={taskId ? `/tasks/${taskId}` : "/tasks"} className={cn("flex flex-col items-start gap-0.5", !n.read_at && "bg-hud-ghost")} onClick={() => !n.read_at && api.notifications.markRead(n.id).then(() => queryClient.invalidateQueries({ queryKey: ["notifications"] }))}>
                    <span className="flex w-full items-center gap-2">
                      <span className={cn("hud-dot", n.severity === "error" ? "bg-hud-red text-hud-red" : n.severity === "warning" ? "bg-hud-amber text-hud-amber" : n.severity === "success" ? "bg-hud-green text-hud-green" : "bg-primary text-primary")} />
                      <span className="flex-1 truncate text-xs font-bold">{n.title}</span>
                      <span className="text-[9px] text-muted-foreground">{timeAgo(n.created_at)}</span>
                    </span>
                    <span className="line-clamp-2 pl-3.5 text-[11px] text-muted-foreground">{n.body}</span>
                  </Link>
                </DropdownMenuItem>
              );
            })}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button type="button" className="flex h-8 items-center gap-2 border border-border px-2 text-left hover:border-primary">
            <span className="flex h-5 w-5 items-center justify-center bg-primary font-mono text-[10px] font-bold uppercase text-primary-foreground">{(auth?.user.full_name || auth?.user.email || "?").slice(0, 1)}</span>
            <span className="hidden flex-col leading-tight xl:flex">
              <span className="font-mono text-[10px] font-bold uppercase tracking-[0.08em]">{auth?.user.full_name || auth?.user.email}</span>
              <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">{auth?.role}</span>
            </span>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-60">
          <DropdownMenuLabel className="hud-label">Organisations</DropdownMenuLabel>
          {(auth?.organizations ?? []).map((m) => (
            <DropdownMenuItem key={m.organization.id} onSelect={() => m.organization.id !== auth?.organization.id && switchOrganization(m.organization.id)}>
              <Building2 />
              <span className="flex-1 truncate">{m.organization.name}</span>
              <span className="text-[10px] text-muted-foreground">{m.role}</span>
              {m.organization.id === auth?.organization.id ? <Check /> : null}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild>
            <Link href="/settings?tab=account">
              <User /> Account
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => logout()} destructive>
            <LogOut /> Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <IconButton label={consoleOpen ? "Hide JARVIS console" : "Show JARVIS console"} onClick={() => setConsoleOpen(!consoleOpen)} active={consoleOpen}>
        <PanelRight className="h-3.5 w-3.5" />
      </IconButton>
      <HudClock className="ml-1 hidden w-[112px] md:block" />

      <ConfirmBanner
        open={confirmStop}
        title="STOP EVERYTHING"
        detail="All queued and running tasks will be cancelled immediately. Browser sessions stay open so you can inspect them. This cannot be undone."
        confirmLabel="YES — STOP ALL TASKS"
        tone="danger"
        busy={stopAll.isPending}
        onConfirm={() => stopAll.mutate()}
        onCancel={() => setConfirmStop(false)}
      />
    </header>
  );
}
