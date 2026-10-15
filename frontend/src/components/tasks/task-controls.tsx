"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Hand, Pause, Play, RotateCcw, SkipForward, Square, Undo2 } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { useJarvis } from "@/lib/jarvis-store";
import { canWrite, toast, useAppStore } from "@/lib/store";
import type { Task } from "@/lib/types";
import { cn } from "@/lib/utils";

type Action = "start" | "pause" | "resume" | "stop" | "takeover" | "release" | "retry" | "skip";

/** Which controls make sense for a task in a given state. */
export function availableActions(task: Pick<Task, "status" | "human_control">): Action[] {
  const s = task.status;
  const actions: Action[] = [];
  if (s === "draft") actions.push("start");
  if (s === "running") actions.push("pause", "takeover", "stop");
  if (s === "paused" && !task.human_control) actions.push("resume", "takeover", "stop");
  if (s === "paused" && task.human_control) actions.push("release", "stop");
  if (s === "queued") actions.push("stop");
  if (s === "waiting_for_human" || s === "needs_attention") {
    actions.push(task.human_control ? "release" : "takeover", "retry", "skip", "stop");
  }
  if (s === "failed" || s === "cancelled") actions.push("retry");
  return actions;
}

const LABELS: Record<Action, { label: string; icon: React.ComponentType<{ className?: string }>; variant: "default" | "destructive" | "outline" | "success" | "warning" }> = {
  start: { label: "Start", icon: Play, variant: "default" },
  pause: { label: "Pause", icon: Pause, variant: "outline" },
  resume: { label: "Resume", icon: Play, variant: "success" },
  stop: { label: "Stop", icon: Square, variant: "destructive" },
  takeover: { label: "Take over browser", icon: Hand, variant: "warning" },
  release: { label: "Return control to AI", icon: Undo2, variant: "success" },
  retry: { label: "Retry", icon: RotateCcw, variant: "default" },
  skip: { label: "Skip", icon: SkipForward, variant: "outline" },
};

export function useTaskAction(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ action, body }: { action: Action; body?: Record<string, unknown> }) => api.tasks.action(taskId, action, body),
    onSuccess: (_, { action }) => {
      queryClient.invalidateQueries({ queryKey: [`task:${taskId}`] });
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      useJarvis.getState().push({ role: "sys", text: `${LABELS[action].label.toUpperCase()} requested.` });
    },
    onError: (e: Error) => toast({ title: "Action failed", description: e instanceof ApiError ? e.message : "Unexpected error", variant: "destructive" }),
  });
}

/** Row of real task controls (Pause / Resume / Take over / Return control / Stop / Retry / Skip). */
export function TaskControls({ task, size = "sm", compact = false, sceneId, className }: { task: Task; size?: "sm" | "default"; compact?: boolean; sceneId?: string | null; className?: string }) {
  const role = useAppStore((s) => s.auth?.role);
  const mutation = useTaskAction(task.id);
  const [confirmStop, setConfirmStop] = React.useState(false);
  if (!canWrite(role)) return null;
  const actions = availableActions(task);
  if (!actions.length) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {actions.map((action) => {
        const meta = LABELS[action];
        const Icon = meta.icon;
        return (
          <Button
            key={action}
            size={size}
            variant={meta.variant}
            loading={mutation.isPending && mutation.variables?.action === action}
            onClick={() => {
              if (action === "stop") return setConfirmStop(true);
              const body = (action === "retry" || action === "skip") && sceneId ? { scene_id: sceneId } : undefined;
              mutation.mutate({ action, body });
            }}
            title={meta.label}
          >
            <Icon /> {compact ? null : meta.label}
          </Button>
        );
      })}
      <ConfirmBanner open={confirmStop} title="STOP TASK" detail={`Cancel “${task.title || task.id.slice(0, 8)}”. Completed scenes and downloaded files are kept.`} tone="danger" busy={mutation.isPending} onConfirm={() => mutation.mutate({ action: "stop" }, { onSettled: () => setConfirmStop(false) })} onCancel={() => setConfirmStop(false)} />
    </div>
  );
}
