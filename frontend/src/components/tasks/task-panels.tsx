"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Camera, CheckCircle2, ChevronDown, ChevronRight, Cpu, XCircle } from "lucide-react";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import type { ActivityEntry, AgentRun, Scene, Screenshot, TaskStep } from "@/lib/types";
import { cn, formatDuration, formatTime, timeAgo, truncate } from "@/lib/utils";

const LEVEL_COLOR: Record<string, string> = { error: "text-hud-red", warning: "text-hud-amber", success: "text-hud-green", debug: "text-muted-foreground", info: "text-foreground" };

/** Structured activity log for one task (timestamp · level · event · message · duration). */
export function ActivityLog({ taskId, className }: { taskId: string; className?: string }) {
  const activity = useQuery({ queryKey: [`activity:${taskId}`], queryFn: () => api.tasks.activity(taskId), refetchInterval: 5000 });
  const ref = React.useRef<HTMLDivElement>(null);
  const [follow, setFollow] = React.useState(true);
  const items = React.useMemo(() => [...(activity.data ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at)), [activity.data]);
  React.useEffect(() => {
    if (follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [items.length, follow]);
  if (activity.isLoading) return <Skeleton className="h-40" />;
  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div ref={ref} onScroll={(e) => { const el = e.currentTarget; setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 40); }} className="min-h-0 flex-1 overflow-y-auto font-sans text-[11px]">
        {items.length === 0 ? <p className="p-3 text-muted-foreground">No activity yet.</p> : null}
        {items.map((a: ActivityEntry) => (
          <div key={a.id} className="flex gap-2 border-b border-border/50 px-2 py-1 hover:bg-hud-ghost">
            <span className="shrink-0 text-[9px] text-muted-foreground">{formatTime(a.created_at)}</span>
            <span className={cn("shrink-0 w-14 truncate text-[9px] uppercase", LEVEL_COLOR[a.level] ?? "text-foreground")}>{a.event_type.replace(/^task\.|^scene\.|^session\./, "")}</span>
            <span className={cn("min-w-0 flex-1 break-words", LEVEL_COLOR[a.level] ?? "text-foreground")}>{a.message}</span>
            {a.duration_ms != null ? <span className="shrink-0 text-[9px] text-muted-foreground">{formatDuration(a.duration_ms / 1000)}</span> : null}
          </div>
        ))}
      </div>
      {!follow ? (
        <button type="button" className="hud-label border-t border-border py-1 text-center hover:text-primary" onClick={() => { setFollow(true); if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }}>
          ↓ follow live
        </button>
      ) : null}
    </div>
  );
}

/** Agent decision panel: goal → tool calls with concise reasoning (never raw chain-of-thought). */
export function AgentDecisions({ taskId, className }: { taskId: string; className?: string }) {
  const runs = useQuery({ queryKey: [`agent-runs:${taskId}`], queryFn: () => api.tasks.agentRuns(taskId), refetchInterval: 5000 });
  const [open, setOpen] = React.useState<Record<string, boolean>>({});
  if (runs.isLoading) return <Skeleton className="h-40" />;
  const list = runs.data ?? [];
  if (!list.length) return <EmptyState icon={Cpu} title="No agent decisions yet" description="Tool calls appear here once the agent starts working on the page." className="py-6" />;
  return (
    <div className={cn("space-y-2 overflow-y-auto p-2", className)}>
      {list.map((run: AgentRun) => {
        const expanded = open[run.id] ?? run.status === "running";
        return (
          <div key={run.id} className="border border-border">
            <button type="button" onClick={() => setOpen({ ...open, [run.id]: !expanded })} className="flex w-full items-start gap-2 px-2 py-1.5 text-left hover:bg-hud-ghost">
              {expanded ? <ChevronDown className="mt-0.5 h-3 w-3 shrink-0 text-primary" /> : <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />}
              <div className="min-w-0 flex-1">
                <p className="truncate font-sans text-[11px] font-bold">{run.goal || "Agent run"}</p>
                <p className="font-sans text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                  {run.mode} · {run.model || "heuristic"} · {run.actions.length} action(s) {run.started_at ? `· ${timeAgo(run.started_at)}` : ""}
                </p>
              </div>
              <StatusBadge status={run.status} />
            </button>
            {expanded ? (
              <ol className="border-t border-border">
                {run.actions.map((a) => (
                  <li key={a.id} className="flex gap-2 border-b border-border/40 px-2 py-1.5 last:border-b-0">
                    {a.success ? <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-hud-green" /> : <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-hud-red" />}
                    <div className="min-w-0 flex-1 font-sans text-[11px]">
                      <p>
                        <span className="text-primary">{a.tool}</span>
                        <span className="text-muted-foreground"> {truncate(JSON.stringify(a.arguments ?? {}), 120)}</span>
                      </p>
                      {a.reasoning ? <p className="text-foreground/80">{a.reasoning}</p> : null}
                      {a.error ? <p className="text-hud-red">{a.error}</p> : null}
                    </div>
                    <span className="shrink-0 text-[9px] text-muted-foreground">{a.duration_ms ? `${a.duration_ms} ms` : ""}</span>
                  </li>
                ))}
                {run.summary ? <li className="px-2 py-1.5 font-sans text-[11px] text-hud-green">{run.summary}</li> : null}
                {run.error ? <li className="px-2 py-1.5 font-sans text-[11px] text-hud-red">{run.error}</li> : null}
              </ol>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** Screenshot history with a lightbox. Images are fetched through signed URLs. */
export function ScreenshotHistory({ taskId, className }: { taskId: string; className?: string }) {
  const shots = useQuery({ queryKey: [`screenshots:${taskId}`], queryFn: () => api.tasks.screenshots(taskId), refetchInterval: 10000 });
  const [active, setActive] = React.useState<Screenshot | null>(null);
  if (shots.isLoading) return <Skeleton className="h-40" />;
  const list = shots.data ?? [];
  if (!list.length) return <EmptyState icon={Camera} title="No screenshots yet" description="The worker captures a screenshot at every important stage." className="py-6" />;
  return (
    <div className={cn("grid grid-cols-2 gap-2 overflow-y-auto p-2 md:grid-cols-3", className)}>
      {list.map((s) => (
        <button key={s.id} type="button" onClick={() => setActive(s)} className="group border border-border text-left hover:border-primary">
          <img src={s.image.url} alt={s.stage} loading="lazy" className="aspect-video w-full bg-black object-cover object-top" />
          <div className="px-1.5 py-1 font-sans text-[9px] uppercase tracking-[0.1em] text-muted-foreground group-hover:text-primary">
            <p className="truncate">{s.stage}</p>
            <p className="truncate">{formatTime(s.created_at)} · {truncate(s.title || s.url, 24)}</p>
          </div>
        </button>
      ))}
      <Dialog open={!!active} onOpenChange={(o) => !o && setActive(null)}>
        <DialogContent wide>
          <DialogTitle>{active?.stage} · {active ? formatTime(active.created_at) : ""}</DialogTitle>
          {active ? (
            <>
              <img src={active.image.url} alt={active.stage} className="max-h-[70vh] w-full border border-border bg-black object-contain" />
              <p className="truncate font-sans text-[10px] text-muted-foreground">{active.title} · {active.url}</p>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Scene progress list with states + per-scene retry/skip hooks. */
export function SceneProgress({ scenes, steps, currentSceneId, projectId, className, renderActions }: { scenes: Scene[]; steps: TaskStep[]; currentSceneId?: string | null; projectId: string | null; className?: string; renderActions?: (scene: Scene) => React.ReactNode }) {
  if (!scenes.length) return null;
  const stepsByScene = new Map<string, TaskStep[]>();
  for (const st of steps) {
    if (!st.scene_id) continue;
    stepsByScene.set(st.scene_id, [...(stepsByScene.get(st.scene_id) ?? []), st]);
  }
  return (
    <ol className={cn("divide-y divide-border", className)}>
      {scenes.map((scene) => {
        const current = scene.id === currentSceneId;
        const sceneSteps = stepsByScene.get(scene.id) ?? [];
        const last = sceneSteps[sceneSteps.length - 1];
        return (
          <li key={scene.id} className={cn("px-2 py-1.5", current && "bg-hud-ghost")}>
            <div className="flex items-center gap-2">
              <span className={cn("font-sans text-[10px] font-bold", current ? "text-primary" : "text-muted-foreground")}>{scene.code}</span>
              <span className="min-w-0 flex-1 truncate font-sans text-[11px]">{scene.title || truncate(scene.prompt, 60)}</span>
              {scene.retry_count ? <span className="hud-pill text-hud-amber">retry {scene.retry_count}</span> : null}
              <StatusBadge status={scene.status} />
            </div>
            {(current || scene.last_error) && (
              <p className={cn("mt-0.5 truncate font-sans text-[10px]", scene.last_error ? "text-hud-red" : "text-muted-foreground")}>
                {scene.last_error || (last ? `${last.name} · ${last.status}` : "")}
              </p>
            )}
            {renderActions ? <div className="mt-1">{renderActions(scene)}</div> : null}
            {projectId && current ? (
              <Link href={`/projects/${projectId}?scene=${scene.id}`} className="hud-label mt-0.5 inline-block hover:text-primary">edit scene →</Link>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

/** Step timeline (adapter steps like open_platform, verify_session, upload_refs, generate, download…). */
export function StepTimeline({ steps, className }: { steps: TaskStep[]; className?: string }) {
  if (!steps.length) return <p className="p-3 font-sans text-[11px] text-muted-foreground">No steps recorded yet.</p>;
  return (
    <ol className={cn("divide-y divide-border font-sans text-[11px]", className)}>
      {steps.map((st) => (
        <li key={st.id} className="flex items-center gap-2 px-2 py-1">
          <span className="w-5 text-[9px] text-muted-foreground">{st.index}</span>
          <span className="min-w-0 flex-1 truncate">{st.name}{st.attempt > 1 ? ` (attempt ${st.attempt})` : ""}</span>
          {st.error ? <span className="max-w-[40%] truncate text-hud-red" title={st.error}>{st.error}</span> : null}
          <span className="text-[9px] text-muted-foreground">{st.duration_ms != null ? formatDuration(st.duration_ms / 1000) : ""}</span>
          <StatusBadge status={st.status} />
        </li>
      ))}
    </ol>
  );
}
