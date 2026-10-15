"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";

import { TaskControls } from "@/components/tasks/task-controls";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";
import type { Task } from "@/lib/types";
import { cn, shortId, timeAgo, titleCase } from "@/lib/utils";

/** Attention block with the recovery options the master prompt requires: Retry / Edit / Skip / Take over. */
export function AttentionBanner({ task, className }: { task: Task; className?: string }) {
  if (task.status !== "needs_attention" && task.status !== "waiting_for_human") return null;
  const att = task.attention ?? {};
  return (
    <div className={cn("hud-corners border border-hud-amber/60 bg-hud-amber/10 p-3", className)}>
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-hud-amber" />
        <div className="min-w-0 flex-1">
          <p className="hud-title text-hud-amber">
            {att.kind === "login" ? "Login required" : att.kind === "verification" ? "Verification required" : att.kind === "blocked" ? "Blocked by the platform" : "Needs attention"}
            {att.scene_code ? ` · ${att.scene_code}` : ""}
          </p>
          <p className="mt-1 text-xs text-foreground">{att.reason || task.error || "The agent paused and is waiting for you."}</p>
          <p className="mt-1 text-[10px] text-muted-foreground">
            CAPTCHA, MFA and logins are never bypassed — take over, complete the step yourself, then return control. {att.since ? `Waiting ${timeAgo(att.since)}.` : ""}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <TaskControls task={task} sceneId={att.scene_id} />
            {task.project_id && att.scene_id ? (
              <Link href={`/projects/${task.project_id}?scene=${att.scene_id}`} className="border border-border px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground hover:border-primary hover:text-primary">
                Edit scene
              </Link>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Compact task row used in lists / command center. */
export function TaskRow({ task, showControls = true, dragHandle, className }: { task: Task; showControls?: boolean; dragHandle?: React.ReactNode; className?: string }) {
  const progress = task.progress ?? {};
  const percent = Math.round(progress.percent ?? 0);
  const attention = task.status === "needs_attention" || task.status === "waiting_for_human";
  return (
    <div className={cn("hud-panel group flex items-center gap-3 px-3 py-2 transition-colors hover:border-primary/50", attention && "border-hud-amber/60", className)}>
      {dragHandle}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Link href={`/tasks/${task.id}`} className="truncate font-mono text-xs font-bold text-foreground hover:text-primary">
            {task.title || `Task ${shortId(task.id)}`}
          </Link>
          <StatusBadge status={task.status} />
          {task.human_control ? <span className="hud-pill text-hud-amber">HUMAN CONTROL</span> : null}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[10px] text-muted-foreground">
          <span>{titleCase(task.kind)}</span>
          <span>{task.platform_key}</span>
          <span>P{task.priority}</span>
          {progress.total ? (
            <span>
              {progress.completed ?? 0}/{progress.total} scenes{progress.current_scene_code ? ` · ${progress.current_scene_code}` : ""}
            </span>
          ) : null}
          {task.current_action ? <span className="truncate text-hud-med">▸ {task.current_action}</span> : null}
          {attention && task.attention?.reason ? <span className="truncate text-hud-amber">⚠ {task.attention.reason}</span> : null}
          <span>{timeAgo(task.updated_at)}</span>
        </div>
        {progress.total ? <Progress value={percent} className="mt-1.5 h-1" /> : null}
      </div>
      {showControls ? <TaskControls task={task} compact /> : null}
    </div>
  );
}
