"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Globe, Monitor, Radio } from "lucide-react";

import { LiveViewer } from "@/components/browser/live-viewer";
import { HudCore } from "@/components/hud/hud-core";
import { HudPanel, HudPill } from "@/components/hud/hud-primitives";
import { AttentionBanner, TaskRow } from "@/components/tasks/task-card";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, Skeleton, Stat } from "@/components/ui/misc";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { useJarvis } from "@/lib/jarvis-store";
import type { BrowserSession, Task } from "@/lib/types";
import { cn, formatBytes, formatTime, timeAgo } from "@/lib/utils";

/** The HUD centrepiece: the reactor core, or the live browser of the selected running task. */
function Centrepiece({ activeTasks, sessions }: { activeTasks: Task[]; sessions: BrowserSession[] }) {
  const hudStyle = useJarvis((s) => s.prefs.hudStyle);
  const [selected, setSelected] = React.useState<string | null>(null);
  const live = activeTasks.filter((t) => t.browser_session_id && (t.status === "running" || t.status === "paused" || t.status === "waiting_for_human" || t.status === "needs_attention"));
  const current = live.find((t) => t.id === selected) ?? null;
  const session = current ? sessions.find((s) => s.id === current.browser_session_id) ?? null : null;
  const { data: fullSession } = useQuery({ queryKey: [`session:${session?.id}`], queryFn: () => api.sessions.get(session!.id), enabled: !!session, refetchInterval: 10000 });

  return (
    <HudPanel
      corners
      padded={false}
      className="min-h-[360px]"
      title={current ? `LIVE BROWSER · ${current.title || current.id.slice(0, 8)}` : "AI CORE"}
      actions={
        <div className="flex items-center gap-1.5">
          {live.map((t) => (
            <button key={t.id} type="button" onClick={() => setSelected(selected === t.id ? null : t.id)} className={cn("hud-pill", selected === t.id ? "border-primary text-primary" : "text-muted-foreground hover:text-primary")}>
              <Monitor className="h-3 w-3" /> {t.progress?.current_scene_code ?? t.title?.slice(0, 14) ?? "task"}
            </button>
          ))}
          {current ? (
            <Link href={`/tasks/${current.id}`} className="hud-pill text-primary hover:bg-hud-ghost">
              OPEN WORKSPACE <ArrowRight className="h-3 w-3" />
            </Link>
          ) : null}
        </div>
      }
    >
      {current && fullSession ? (
        <div className="h-[420px]">
          <LiveViewer session={fullSession} canControl={false} humanControl={current.human_control} compact toolbar={false} />
        </div>
      ) : (
        <div className="relative h-[420px]">
          {hudStyle === "core" ? (
            <HudCore />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3">
              <Radio className="h-8 w-8 text-primary" />
              <p className="hud-label">Minimal HUD · say &ldquo;help&rdquo; in the console</p>
            </div>
          )}
          {live.length > 0 && (
            <div className="pointer-events-none absolute inset-x-0 bottom-2 text-center font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
              {live.length} live browser{live.length === 1 ? "" : "s"} — pick one above to watch
            </div>
          )}
        </div>
      )}
    </HudPanel>
  );
}

export default function CommandCenterPage() {
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 15000 });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions.list, refetchInterval: 20000 });
  const assistant = useJarvis((s) => s.prefs.assistantName);

  if (dashboard.isError) return <div className="p-6"><ErrorState error={dashboard.error} retry={() => dashboard.refetch()} /></div>;
  const d = dashboard.data;
  const attention = (d?.active_tasks ?? []).filter((t) => t.status === "needs_attention" || t.status === "waiting_for_human");

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="hud-label">◈ Command center</p>
          <h1 className="hud-glow font-mono text-lg font-bold uppercase tracking-[0.25em] text-primary">{assistant} · Overview</h1>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {d ? (
            <>
              <HudPill tone={d.tasks.running ? "default" : "dim"} pulse={d.tasks.running > 0}>{d.tasks.running} running</HudPill>
              <HudPill tone="dim">{d.tasks.queued} queued</HudPill>
              <HudPill tone={d.tasks.needs_attention ? "amber" : "dim"} pulse={d.tasks.needs_attention > 0}>{d.tasks.needs_attention} attention</HudPill>
              <HudPill tone={d.system.environment === "production" ? "green" : "orange"}>{d.system.environment}</HudPill>
            </>
          ) : null}
          <Link href="/new-task"><Button size="sm">New task</Button></Link>
        </div>
      </div>

      {attention.length > 0 && (
        <div className="space-y-2">
          {attention.map((t) => (
            <AttentionBanner key={t.id} task={t} />
          ))}
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          <Centrepiece activeTasks={d?.active_tasks ?? []} sessions={sessions.data ?? []} />

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {d ? (
              <>
                <Stat label="Active tasks" value={d.tasks.active} hint={`${d.tasks.total} total · ${d.tasks.completed} completed`} href="/tasks" />
                <Stat label="Needs attention" value={d.tasks.needs_attention} tone={d.tasks.needs_attention ? "warning" : "default"} hint={`${d.tasks.failed} failed`} href="/tasks?status=attention" />
                <Stat label="Browsers" value={`${d.sessions.ready}/${d.sessions.total}`} hint={`${d.sessions.logged_in} logged in`} href="/sessions" />
                <Stat label="Generated" value={d.files.count} hint={`${formatBytes(d.files.bytes)} · ${d.usage_week.clips_generated} this week`} href="/files" />
              </>
            ) : (
              Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20" />)
            )}
          </div>

          <HudPanel title="ACTIVE QUEUE" actions={<Link href="/tasks" className="hud-label hover:text-primary">all tasks →</Link>} padded={false}>
            {dashboard.isLoading ? (
              <div className="space-y-2 p-3"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
            ) : (d?.active_tasks.length ?? 0) === 0 ? (
              <div className="p-3">
                <EmptyState title="Queue empty" description={`Nothing is running. Create a task or tell ${assistant}: "run: open example.com and read the headline".`} action={<Link href="/new-task"><Button size="sm">New task</Button></Link>} />
              </div>
            ) : (
              <div className="space-y-1.5 p-2">{d!.active_tasks.map((t) => <TaskRow key={t.id} task={t} />)}</div>
            )}
          </HudPanel>
        </div>

        <div className="space-y-4">
          <HudPanel title="BROWSER SESSIONS" actions={<Link href="/sessions" className="hud-label hover:text-primary">manage →</Link>} padded={false}>
            {(d?.sessions.items.length ?? 0) === 0 ? (
              <div className="p-3"><EmptyState icon={Globe} title="No sessions" description="Create a persistent browser session and log in once." /></div>
            ) : (
              <ul className="divide-y divide-border">
                {d!.sessions.items.map((s) => (
                  <li key={s.id}>
                    <Link href={`/sessions/${s.id}`} className="flex items-center gap-2 px-3 py-2 hover:bg-hud-ghost">
                      <span className={cn("hud-dot", s.status === "ready" || s.status === "in_use" ? "bg-hud-green text-hud-green" : s.status === "error" || s.status === "expired" ? "bg-hud-red text-hud-red" : "bg-muted-foreground text-muted-foreground")} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-xs font-bold">{s.name}</span>
                        <span className="block truncate font-mono text-[10px] text-muted-foreground">{s.platform_key} · {s.login_status.replace("_", " ")}{s.current_url ? ` · ${s.current_url}` : ""}</span>
                      </span>
                      <StatusBadge status={s.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </HudPanel>

          <HudPanel title="RECENT ACTIVITY" actions={<Link href="/activity" className="hud-label hover:text-primary">full log →</Link>} padded={false}>
            <ul className="max-h-80 divide-y divide-border overflow-y-auto font-mono text-[11px]">
              {(d?.recent_activity ?? []).slice(0, 25).map((a) => (
                <li key={a.id} className="flex gap-2 px-3 py-1.5">
                  <span className="shrink-0 text-[9px] text-muted-foreground">{formatTime(a.created_at)}</span>
                  <span className={cn("min-w-0 flex-1 truncate", a.level === "error" ? "text-hud-red" : a.level === "warning" ? "text-hud-amber" : "text-foreground")} title={a.message}>
                    {a.message}
                  </span>
                </li>
              ))}
              {(d?.recent_activity.length ?? 0) === 0 ? <li className="p-3 text-center text-muted-foreground">No activity yet</li> : null}
            </ul>
          </HudPanel>

          <HudPanel title="RECENT FILES" actions={<Link href="/files" className="hud-label hover:text-primary">all files →</Link>} padded={false}>
            <ul className="divide-y divide-border">
              {(d?.recent_files ?? []).map((f) => (
                <li key={f.id} className="flex items-center gap-2 px-3 py-1.5 font-mono text-[11px]">
                  <span className="min-w-0 flex-1 truncate">{f.filename}</span>
                  <span className="text-muted-foreground">{formatBytes(f.size_bytes)}</span>
                  <span className="text-[9px] text-muted-foreground">{timeAgo(f.created_at)}</span>
                </li>
              ))}
              {(d?.recent_files.length ?? 0) === 0 ? <li className="p-3 text-center font-mono text-[11px] text-muted-foreground">Nothing generated yet</li> : null}
            </ul>
          </HudPanel>
        </div>
      </div>
    </div>
  );
}
