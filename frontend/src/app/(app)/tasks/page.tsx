"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, OctagonX, Trash2 } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudPanel } from "@/components/hud/hud-primitives";
import { TaskRow } from "@/components/tasks/task-card";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { api, ApiError } from "@/lib/api";
import { canWrite, toast, useAppStore } from "@/lib/store";
import type { Task } from "@/lib/types";
import { cn } from "@/lib/utils";

const FILTERS: { key: string; label: string }[] = [
  { key: "active", label: "Active" },
  { key: "attention", label: "Needs attention" },
  { key: "queued", label: "Queued" },
  { key: "draft", label: "Drafts" },
  { key: "finished", label: "Finished" },
  { key: "", label: "All" },
];

function TasksView() {
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const role = useAppStore((s) => s.auth?.role);
  const [filter, setFilter] = React.useState(params.get("status") ?? "active");
  const [page, setPage] = React.useState(0);
  const [confirmStopAll, setConfirmStopAll] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState<Task | null>(null);
  const limit = 50;
  const tasks = useQuery({ queryKey: ["tasks", filter, page], queryFn: () => api.tasks.list({ status: filter || undefined, limit, offset: page * limit }), refetchInterval: 15000 });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["tasks"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const reorder = useMutation({
    mutationFn: (ids: string[]) => api.tasks.reorder(ids),
    onSuccess: invalidate,
    onError: (e: Error) => toast({ title: "Reorder failed", description: e.message, variant: "destructive" }),
  });
  const stopAll = useMutation({
    mutationFn: () => api.tasks.stopAll(),
    onSuccess: (r) => { toast({ title: "Emergency stop", description: `${r.stopped} task(s) stopped`, variant: "warning" }); invalidate(); setConfirmStopAll(false); },
    onError: (e: Error) => toast({ title: "Stop failed", description: e.message, variant: "destructive" }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.tasks.remove(id),
    onSuccess: () => { toast({ title: "Task deleted", variant: "success" }); invalidate(); setConfirmDelete(null); },
    onError: (e: Error) => toast({ title: "Delete failed", description: e instanceof ApiError && e.status === 409 ? "Stop the task before deleting it." : e.message, variant: "destructive" }),
  });

  const items = tasks.data?.items ?? [];
  const queued = items.filter((t) => t.status === "queued");
  const move = (task: Task, dir: -1 | 1) => {
    const ids = queued.map((t) => t.id);
    const i = ids.indexOf(task.id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    reorder.mutate(ids);
  };

  return (
    <div className="space-y-4 p-4">
      <PageHeader
        eyebrow="Mission queue"
        title="Tasks"
        description="Persisted queue with priorities. Queued tasks can be reordered; running tasks can be paused, taken over or stopped."
        actions={
          <div className="flex items-center gap-1.5">
            {canWrite(role) ? (
              <Button variant="destructive" size="sm" onClick={() => setConfirmStopAll(true)}>
                <OctagonX /> Stop everything
              </Button>
            ) : null}
            <Link href="/new-task"><Button size="sm">New task</Button></Link>
          </div>
        }
      />
      <ConfirmBanner open={confirmStopAll} title="STOP EVERYTHING" detail="Every running, queued and paused task will be cancelled and all agent browsers released. Downloaded files are kept." tone="danger" busy={stopAll.isPending} onConfirm={() => stopAll.mutate()} onCancel={() => setConfirmStopAll(false)} />
      <ConfirmBanner open={!!confirmDelete} title="DELETE TASK" detail={`Remove “${confirmDelete?.title ?? ""}” and its step history. Generated files stay in the project.`} tone="danger" busy={remove.isPending} onConfirm={() => confirmDelete && remove.mutate(confirmDelete.id)} onCancel={() => setConfirmDelete(null)} />

      <div className="flex flex-wrap items-center gap-1.5">
        {FILTERS.map((f) => (
          <button key={f.key} type="button" onClick={() => { setFilter(f.key); setPage(0); }} className={cn("hud-pill", filter === f.key ? "border-primary text-primary" : "text-muted-foreground hover:text-primary")}>
            {f.label}
          </button>
        ))}
        <span className="ml-auto font-sans text-[10px] text-muted-foreground">{tasks.data ? `${tasks.data.total} task(s)` : ""}</span>
      </div>

      <HudPanel padded={false}>
        {tasks.isLoading ? (
          <div className="space-y-2 p-3"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : tasks.isError ? (
          <div className="p-3"><ErrorState error={tasks.error} retry={() => tasks.refetch()} /></div>
        ) : items.length === 0 ? (
          <div className="p-3"><EmptyState title="No tasks here" description={filter === "active" ? "Nothing is running or waiting." : "No tasks match this filter."} action={<Link href="/new-task"><Button size="sm">New task</Button></Link>} /></div>
        ) : (
          <div className="space-y-1.5 p-2">
            {items.map((t) => (
              <TaskRow
                key={t.id}
                task={t}
                dragHandle={
                  canWrite(role) ? (
                    <div className="flex flex-col items-center gap-0.5">
                      {t.status === "queued" ? (
                        <>
                          <button type="button" className="text-muted-foreground hover:text-primary disabled:opacity-20" disabled={queued.indexOf(t) === 0 || reorder.isPending} onClick={() => move(t, -1)} title="Move up"><ArrowUp className="h-3 w-3" /></button>
                          <button type="button" className="text-muted-foreground hover:text-primary disabled:opacity-20" disabled={queued.indexOf(t) === queued.length - 1 || reorder.isPending} onClick={() => move(t, 1)} title="Move down"><ArrowDown className="h-3 w-3" /></button>
                        </>
                      ) : ["completed", "failed", "cancelled", "draft"].includes(t.status) ? (
                        <button type="button" className="text-muted-foreground hover:text-hud-red" onClick={() => setConfirmDelete(t)} title="Delete"><Trash2 className="h-3 w-3" /></button>
                      ) : (
                        <span className="w-3" />
                      )}
                    </div>
                  ) : undefined
                }
              />
            ))}
          </div>
        )}
      </HudPanel>
      {tasks.data && tasks.data.total > limit ? (
        <div className="flex items-center justify-end gap-2">
          <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</Button>
          <span className="font-sans text-[10px] text-muted-foreground">page {page + 1} / {Math.ceil(tasks.data.total / limit)}</span>
          <Button size="sm" variant="outline" disabled={(page + 1) * limit >= tasks.data.total} onClick={() => setPage((p) => p + 1)}>Next</Button>
        </div>
      ) : null}
    </div>
  );
}

export default function TasksPage() {
  return (
    <React.Suspense fallback={null}>
      <TasksView />
    </React.Suspense>
  );
}
