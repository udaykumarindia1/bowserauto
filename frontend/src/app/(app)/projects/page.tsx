"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, Plus } from "lucide-react";

import { HudPanel } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { canWrite, toast, useAppStore } from "@/lib/store";
import { cn, timeAgo, titleCase } from "@/lib/utils";

function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const templates = useQuery({ queryKey: ["templates"], queryFn: api.templates.list, enabled: open });
  const platforms = useQuery({ queryKey: ["platforms"], queryFn: api.platforms.list, enabled: open });
  const [form, setForm] = React.useState({ name: "", description: "", template_key: "ai_video_generation", platform_key: "mock", visual_style: "cinematic", script_text: "", clip_duration_seconds: 8, analyze: true });
  const create = useMutation({
    mutationFn: async () => {
      const project = await api.projects.create({ name: form.name.trim(), description: form.description, template_key: form.template_key, platform_key: form.platform_key, visual_style: form.visual_style, script_text: form.script_text, output_settings: { clip_duration_seconds: form.clip_duration_seconds } });
      if (form.analyze && form.script_text.trim()) await api.projects.analyze(project.id, { clip_duration_seconds: form.clip_duration_seconds });
      return project;
    },
    onSuccess: (p) => { queryClient.invalidateQueries({ queryKey: ["projects"] }); toast({ title: "Project created", description: p.name, variant: "success" }); onOpenChange(false); router.push(`/projects/${p.id}`); },
    onError: (e: Error) => toast({ title: "Could not create project", description: e.message, variant: "destructive" }),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent wide>
        <DialogTitle>New project</DialogTitle>
        <DialogDescription>A project owns a script, its scenes, reference images and generated files (stored under /projects/&lt;id&gt;/).</DialogDescription>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1 sm:col-span-2"><Label>Name</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} autoFocus /></div>
          <div className="space-y-1">
            <Label>Template</Label>
            <Select value={form.template_key} onChange={(e) => setForm({ ...form, template_key: e.target.value })}>
              {(templates.data ?? []).map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Default platform</Label>
            <Select value={form.platform_key} onChange={(e) => setForm({ ...form, platform_key: e.target.value })}>
              {(platforms.data ?? []).filter((p) => p.enabled).map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
            </Select>
          </div>
          <div className="space-y-1"><Label>Visual style</Label><Input value={form.visual_style} onChange={(e) => setForm({ ...form, visual_style: e.target.value })} /></div>
          <div className="space-y-1"><Label>Clip length (s)</Label><Input type="number" min={1} max={60} value={form.clip_duration_seconds} onChange={(e) => setForm({ ...form, clip_duration_seconds: Number(e.target.value) || 8 })} /></div>
          <div className="space-y-1 sm:col-span-2"><Label>Description</Label><Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
          <div className="space-y-1 sm:col-span-2"><Label>Script (optional)</Label><Textarea rows={7} value={form.script_text} onChange={(e) => setForm({ ...form, script_text: e.target.value })} placeholder="Paste a script; it is split into scenes automatically (LLM when configured, heuristic otherwise)." /></div>
          <label className="flex items-center gap-2 font-sans text-[10px] uppercase tracking-[0.12em] sm:col-span-2">
            <input type="checkbox" className="accent-primary" checked={form.analyze} onChange={(e) => setForm({ ...form, analyze: e.target.checked })} /> Analyse script into scenes now
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!form.name.trim()} loading={create.isPending} onClick={() => create.mutate()}>Create</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ProjectsPage() {
  const role = useAppStore((s) => s.auth?.role);
  const [archived, setArchived] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const projects = useQuery({ queryKey: ["projects", archived], queryFn: () => api.projects.list(archived), refetchInterval: 20000 });
  return (
    <div className="space-y-4 p-4">
      <PageHeader eyebrow="Productions" title="Projects" description="Scripts, scenes, references and generated clips." actions={canWrite(role) ? <Button size="sm" onClick={() => setOpen(true)}><Plus /> New project</Button> : null} />
      <NewProjectDialog open={open} onOpenChange={setOpen} />
      <div className="flex items-center gap-1.5">
        <button type="button" className={cn("hud-pill", !archived ? "border-primary text-primary" : "text-muted-foreground")} onClick={() => setArchived(false)}>Active</button>
        <button type="button" className={cn("hud-pill", archived ? "border-primary text-primary" : "text-muted-foreground")} onClick={() => setArchived(true)}>Including archived</button>
      </div>
      {projects.isLoading ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3"><Skeleton className="h-32" /><Skeleton className="h-32" /><Skeleton className="h-32" /></div>
      ) : projects.isError ? (
        <ErrorState error={projects.error} retry={() => projects.refetch()} />
      ) : (projects.data?.length ?? 0) === 0 ? (
        <HudPanel><EmptyState icon={FolderKanban} title="No projects yet" description="Create a project, paste a script and let the agent generate every scene." action={canWrite(role) ? <Button size="sm" onClick={() => setOpen(true)}>New project</Button> : undefined} /></HudPanel>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {projects.data!.map((p) => {
            const pct = p.scene_count ? Math.round((p.completed_scenes / p.scene_count) * 100) : 0;
            return (
              <Link key={p.id} href={`/projects/${p.id}`} className="hud-panel hud-corners block p-3 transition-colors hover:border-primary/60">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-sans text-xs font-bold uppercase tracking-[0.1em]">{p.name}</p>
                    <p className="hud-label mt-0.5">{titleCase(p.template_key)} · {p.platform_key}</p>
                  </div>
                  <StatusBadge status={p.active_task_status ?? p.status} />
                </div>
                {p.description ? <p className="mt-2 line-clamp-2 text-[11px] text-muted-foreground">{p.description}</p> : null}
                <div className="mt-3 flex items-center justify-between font-sans text-[10px] text-muted-foreground">
                  <span>{p.completed_scenes}/{p.scene_count} scenes</span>
                  <span>{timeAgo(p.updated_at)}</span>
                </div>
                <Progress value={pct} className="mt-1 h-1" />
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
