"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Pencil, Trash2 } from "lucide-react";

import { LiveViewer } from "@/components/browser/live-viewer";
import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudPanel, HudPill, MetricBar } from "@/components/hud/hud-primitives";
import { AttentionBanner } from "@/components/tasks/task-card";
import { TaskControls, useTaskAction } from "@/components/tasks/task-controls";
import { ActivityLog, AgentDecisions, SceneProgress, ScreenshotHistory, StepTimeline } from "@/components/tasks/task-panels";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label, Textarea } from "@/components/ui/input";
import { ErrorState, Skeleton } from "@/components/ui/misc";
import { StatusBadge } from "@/components/ui/status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { useJarvis } from "@/lib/jarvis-store";
import { canWrite, toast, useAppStore } from "@/lib/store";
import type { TaskDetail } from "@/lib/types";
import { formatDateTime, formatDuration, isActiveTaskStatus, titleCase } from "@/lib/utils";

function EditTaskDialog({ task, open, onOpenChange }: { task: TaskDetail; open: boolean; onOpenChange: (o: boolean) => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = React.useState(task.title);
  const [priority, setPriority] = React.useState(task.priority);
  const [instructions, setInstructions] = React.useState(task.instructions);
  const [maxRetries, setMaxRetries] = React.useState(task.config.max_retries ?? 3);
  const save = useMutation({
    mutationFn: () => api.tasks.update(task.id, { title, priority, instructions, config: { ...task.config, max_retries: maxRetries } }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: [`task:${task.id}`] }); queryClient.invalidateQueries({ queryKey: ["tasks"] }); toast({ title: "Task updated", variant: "success" }); onOpenChange(false); },
    onError: (e: Error) => toast({ title: "Update failed", description: e.message, variant: "destructive" }),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Edit task</DialogTitle>
        <DialogDescription>Changes to instructions apply from the next agent step; priority re-sorts the queue.</DialogDescription>
        <div className="space-y-3">
          <div className="space-y-1"><Label>Title</Label><Input value={title} onChange={(e) => setTitle(e.target.value)} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1"><Label>Priority</Label><Input type="number" min={1} max={10} value={priority} onChange={(e) => setPriority(Number(e.target.value) || 5)} /></div>
            <div className="space-y-1"><Label>Max retries</Label><Input type="number" min={0} max={10} value={maxRetries} onChange={(e) => setMaxRetries(Number(e.target.value) || 0)} /></div>
          </div>
          <div className="space-y-1"><Label>Instructions</Label><Textarea rows={5} value={instructions} onChange={(e) => setInstructions(e.target.value)} /></div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function TaskWorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const role = useAppStore((s) => s.auth?.role);
  const assistant = useJarvis((s) => s.prefs.assistantName);
  const task = useQuery({ queryKey: [`task:${id}`], queryFn: () => api.tasks.get(id), refetchInterval: (q) => (q.state.data && isActiveTaskStatus(q.state.data.status) ? 4000 : 20000) });
  const sessionId = task.data?.browser_session_id ?? null;
  const session = useQuery({ queryKey: [`session:${sessionId}`], queryFn: () => api.sessions.get(sessionId!), enabled: !!sessionId, refetchInterval: 10000 });
  const action = useTaskAction(id);
  const [editOpen, setEditOpen] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const remove = useMutation({
    mutationFn: () => api.tasks.remove(id),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["tasks"] }); toast({ title: "Task deleted", variant: "success" }); router.push("/tasks"); },
    onError: (e: Error) => toast({ title: "Delete failed", description: e.message, variant: "destructive" }),
  });

  if (task.isLoading) return <div className="space-y-3 p-4"><Skeleton className="h-10" /><Skeleton className="h-[480px]" /></div>;
  if (task.isError || !task.data) return <div className="p-6"><ErrorState error={task.error} retry={() => task.refetch()} /></div>;
  const t = task.data;
  const progress = t.progress ?? {};
  const active = isActiveTaskStatus(t.status);
  const writable = canWrite(role);
  const elapsed = t.started_at ? (new Date(t.finished_at ?? Date.now()).getTime() - new Date(t.started_at).getTime()) / 1000 : null;

  return (
    <div className="flex min-h-full flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Link href="/tasks" className="hud-label flex items-center gap-1 hover:text-primary"><ArrowLeft className="h-3 w-3" /> Tasks</Link>
        <span className="text-border">/</span>
        <h1 className="min-w-0 flex-1 truncate font-mono text-sm font-bold uppercase tracking-[0.15em] text-primary">{t.title || `Task ${t.id.slice(0, 8)}`}</h1>
        <StatusBadge status={t.status} />
        {t.human_control ? <HudPill tone="amber" pulse>Human control</HudPill> : null}
        {t.worker_id ? <HudPill tone="dim">worker {t.worker_id.slice(0, 8)}</HudPill> : null}
        <TaskControls task={t} sceneId={t.attention?.scene_id} />
        {writable ? (
          <>
            <Button size="icon-sm" variant="ghost" onClick={() => setEditOpen(true)} title="Edit task"><Pencil /></Button>
            {!active ? <Button size="icon-sm" variant="ghost" onClick={() => setConfirmDelete(true)} title="Delete task"><Trash2 /></Button> : null}
          </>
        ) : null}
      </div>
      <ConfirmBanner open={confirmDelete} title="DELETE TASK" detail="Remove this task and its step history. Generated files stay in the project." tone="danger" busy={remove.isPending} onConfirm={() => remove.mutate()} onCancel={() => setConfirmDelete(false)} />
      {editOpen ? <EditTaskDialog task={t} open={editOpen} onOpenChange={setEditOpen} /> : null}
      <AttentionBanner task={t} />

      <div className="grid min-h-0 flex-1 gap-3 xl:grid-cols-[300px_minmax(0,1fr)]">
        {/* Task execution panel */}
        <div className="space-y-3">
          <HudPanel title="EXECUTION" corners>
            <div className="space-y-2">
              <MetricBar label={progress.total ? `Scenes ${progress.completed ?? 0}/${progress.total}` : "Progress"} value={Math.round(progress.percent ?? (t.status === "completed" ? 100 : 0))} />
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px]">
                <dt className="text-muted-foreground">Kind</dt><dd>{titleCase(t.kind)}</dd>
                <dt className="text-muted-foreground">Platform</dt><dd>{t.platform_key}</dd>
                <dt className="text-muted-foreground">Agent mode</dt><dd>{t.config.agent_mode ?? "auto"}</dd>
                <dt className="text-muted-foreground">Priority</dt><dd>P{t.priority}</dd>
                <dt className="text-muted-foreground">Retries</dt><dd>{t.retry_count}/{t.config.max_retries ?? 3}</dd>
                <dt className="text-muted-foreground">Elapsed</dt><dd>{elapsed != null ? formatDuration(elapsed) : "—"}</dd>
                <dt className="text-muted-foreground">Project</dt><dd className="truncate">{t.project_id ? <Link href={`/projects/${t.project_id}`} className="text-primary hover:underline">{t.project_name ?? "open"}</Link> : "—"}</dd>
                <dt className="text-muted-foreground">Browser</dt><dd className="truncate">{sessionId ? <Link href={`/sessions/${sessionId}`} className="text-primary hover:underline">{t.session_name ?? "session"}</Link> : "ephemeral"}</dd>
                <dt className="text-muted-foreground">Queued</dt><dd>{formatDateTime(t.queued_at)}</dd>
                <dt className="text-muted-foreground">Started</dt><dd>{formatDateTime(t.started_at)}</dd>
                {t.finished_at ? <><dt className="text-muted-foreground">Finished</dt><dd>{formatDateTime(t.finished_at)}</dd></> : null}
              </dl>
              {t.current_action ? <p className="border-l-2 border-primary pl-2 font-mono text-[11px] text-foreground">▸ {t.current_action}</p> : null}
              {t.error ? <p className="border-l-2 border-hud-red pl-2 font-mono text-[11px] text-hud-red">{t.error}</p> : null}
              {t.status === "completed" && Object.keys(t.result ?? {}).length ? (
                <pre className="max-h-40 overflow-auto border border-border bg-hud-dark p-2 font-mono text-[10px] text-hud-green">{JSON.stringify(t.result, null, 2)}</pre>
              ) : null}
            </div>
          </HudPanel>

          {t.instructions ? (
            <HudPanel title="INSTRUCTIONS">
              <p className="whitespace-pre-wrap font-mono text-[11px] text-foreground/90">{t.instructions}</p>
            </HudPanel>
          ) : null}

          {t.scenes.length ? (
            <HudPanel title={`SCENES · ${t.scenes.length}`} padded={false} bodyClassName="max-h-[420px] overflow-y-auto">
              <SceneProgress
                scenes={t.scenes}
                steps={t.steps}
                currentSceneId={progress.current_scene_id}
                projectId={t.project_id}
                renderActions={
                  writable && (t.status === "needs_attention" || t.status === "waiting_for_human")
                    ? (scene) => (scene.id === t.attention?.scene_id ? (
                      <div className="flex gap-1">
                        <Button size="sm" variant="outline" loading={action.isPending && action.variables?.action === "retry"} onClick={() => action.mutate({ action: "retry", body: { scene_id: scene.id } })}>Retry scene</Button>
                        <Button size="sm" variant="ghost" loading={action.isPending && action.variables?.action === "skip"} onClick={() => action.mutate({ action: "skip", body: { scene_id: scene.id } })}>Skip scene</Button>
                      </div>
                    ) : null)
                    : undefined
                }
              />
            </HudPanel>
          ) : null}
        </div>

        {/* Live browser + intelligence tabs */}
        <div className="flex min-h-0 flex-col gap-3">
          <div className="h-[520px] min-h-[360px]">
            <LiveViewer
              session={session.data ?? null}
              className="h-full"
              canControl={writable && active}
              humanControl={t.human_control}
              takeoverPending={action.isPending && (action.variables?.action === "takeover" || action.variables?.action === "release")}
              onTakeover={() => action.mutateAsync({ action: "takeover" })}
              onRelease={() => action.mutateAsync({ action: "release" })}
              toolbar={<HudPill tone={t.human_control ? "amber" : active ? "default" : "dim"} pulse={t.status === "running"}>{t.human_control ? "HUMAN" : t.status === "running" ? `${assistant} DRIVING` : t.status.replace(/_/g, " ")}</HudPill>}
            />
          </div>
          <HudPanel padded={false} className="min-h-[300px]" bodyClassName="flex min-h-0 flex-col">
            <Tabs defaultValue="agent" className="flex min-h-0 flex-1 flex-col">
              <TabsList className="m-2 self-start">
                <TabsTrigger value="agent">Agent decisions</TabsTrigger>
                <TabsTrigger value="activity">Activity log</TabsTrigger>
                <TabsTrigger value="steps">Steps · {t.steps.length}</TabsTrigger>
                <TabsTrigger value="shots">Screenshots</TabsTrigger>
              </TabsList>
              <TabsContent value="agent" className="min-h-0 flex-1"><AgentDecisions taskId={t.id} className="max-h-[360px]" /></TabsContent>
              <TabsContent value="activity" className="min-h-0 flex-1"><ActivityLog taskId={t.id} className="max-h-[360px]" /></TabsContent>
              <TabsContent value="steps" className="min-h-0 flex-1"><StepTimeline steps={t.steps} className="max-h-[360px] overflow-y-auto" /></TabsContent>
              <TabsContent value="shots" className="min-h-0 flex-1"><ScreenshotHistory taskId={t.id} className="max-h-[360px]" /></TabsContent>
            </Tabs>
          </HudPanel>
        </div>
      </div>
    </div>
  );
}
