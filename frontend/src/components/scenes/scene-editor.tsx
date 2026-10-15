"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Copy, Image as ImageIcon, RefreshCw, Save, Trash2, Wand2 } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { toast } from "@/lib/store";
import type { Asset, Platform, Scene } from "@/lib/types";
import { cn, formatDateTime, truncate } from "@/lib/utils";

interface SceneListProps {
  scenes: Scene[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  projectId: string;
  canWrite: boolean;
  locked: boolean;
}

/** Ordered scene list with reorder / duplicate / delete controls. */
export function SceneList({ scenes, selectedId, onSelect, projectId, canWrite, locked }: SceneListProps) {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: [`project:${projectId}`] });
  const reorder = useMutation({ mutationFn: (ids: string[]) => api.projects.scenes.reorder(projectId, ids), onSuccess: invalidate, onError: (e: Error) => toast({ title: "Reorder failed", description: e.message, variant: "destructive" }) });
  const duplicate = useMutation({ mutationFn: (id: string) => api.projects.scenes.duplicate(projectId, id), onSuccess: (s) => { invalidate(); onSelect(s.id); }, onError: (e: Error) => toast({ title: "Duplicate failed", description: e.message, variant: "destructive" }) });
  const remove = useMutation({ mutationFn: (id: string) => api.projects.scenes.remove(projectId, id), onSuccess: () => { invalidate(); setConfirm(null); }, onError: (e: Error) => toast({ title: "Delete failed", description: e.message, variant: "destructive" }) });
  const [confirm, setConfirm] = React.useState<Scene | null>(null);
  const move = (i: number, dir: -1 | 1) => {
    const ids = scenes.map((s) => s.id);
    const j = i + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    reorder.mutate(ids);
  };
  const editable = canWrite && !locked;
  return (
    <div>
      <ConfirmBanner open={!!confirm} title="DELETE SCENE" detail={`Remove ${confirm?.code ?? ""} “${confirm?.title ?? ""}”. Generated files for it remain in the project folder.`} tone="danger" busy={remove.isPending} onConfirm={() => confirm && remove.mutate(confirm.id)} onCancel={() => setConfirm(null)} />
      <ol className="divide-y divide-border">
        {scenes.map((s, i) => (
          <li key={s.id} className={cn("group flex items-center gap-1 px-2 py-1.5", selectedId === s.id ? "bg-hud-ghost" : "hover:bg-hud-ghost/50")}>
            {editable ? (
              <div className="flex flex-col opacity-0 transition-opacity group-hover:opacity-100">
                <button type="button" disabled={i === 0 || reorder.isPending} onClick={() => move(i, -1)} className="text-muted-foreground hover:text-primary disabled:opacity-20"><ArrowUp className="h-3 w-3" /></button>
                <button type="button" disabled={i === scenes.length - 1 || reorder.isPending} onClick={() => move(i, 1)} className="text-muted-foreground hover:text-primary disabled:opacity-20"><ArrowDown className="h-3 w-3" /></button>
              </div>
            ) : null}
            <button type="button" onClick={() => onSelect(s.id)} className="min-w-0 flex-1 text-left">
              <div className="flex items-center gap-2">
                <span className={cn("font-sans text-[10px] font-bold", selectedId === s.id ? "text-primary" : "text-muted-foreground")}>{s.code}</span>
                <span className="min-w-0 flex-1 truncate font-sans text-[11px]">{s.title || truncate(s.prompt, 50) || "Untitled scene"}</span>
                <StatusBadge status={s.status} />
              </div>
              <p className="truncate font-sans text-[9px] text-muted-foreground">{s.duration_seconds}s · {s.platform_key ?? "project default"}{s.reference_asset_ids.length ? ` · ${s.reference_asset_ids.length} ref` : ""}{s.last_error ? ` · ⚠ ${s.last_error}` : ""}</p>
            </button>
            {editable ? (
              <div className="flex opacity-0 transition-opacity group-hover:opacity-100">
                <button type="button" title="Duplicate" onClick={() => duplicate.mutate(s.id)} className="p-1 text-muted-foreground hover:text-primary"><Copy className="h-3 w-3" /></button>
                <button type="button" title="Delete" onClick={() => setConfirm(s)} className="p-1 text-muted-foreground hover:text-hud-red"><Trash2 className="h-3 w-3" /></button>
              </div>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

interface SceneFormProps {
  scene: Scene;
  projectId: string;
  platforms: Platform[];
  assets: Asset[];
  canWrite: boolean;
  locked: boolean;
}

/** Full scene editor: text fields, platform override, reference images, regenerate prompt, reset status. */
export function SceneForm({ scene, projectId, platforms, assets, canWrite, locked }: SceneFormProps) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = React.useState(scene);
  React.useEffect(() => setDraft(scene), [scene]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(scene);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: [`project:${projectId}`] });
  const save = useMutation({
    mutationFn: () => api.projects.scenes.update(projectId, scene.id, {
      title: draft.title, duration_seconds: draft.duration_seconds, characters: draft.characters, location: draft.location, visual: draft.visual, camera: draft.camera,
      dialogue: draft.dialogue, narration: draft.narration, prompt: draft.prompt, negative_prompt: draft.negative_prompt, platform_key: draft.platform_key, reference_asset_ids: draft.reference_asset_ids,
    }),
    onSuccess: () => { invalidate(); toast({ title: `${scene.code} saved`, variant: "success" }); },
    onError: (e: Error) => toast({ title: "Save failed", description: e.message, variant: "destructive" }),
  });
  const regenerate = useMutation({
    mutationFn: () => api.projects.scenes.regeneratePrompt(projectId, scene.id),
    onSuccess: (s) => { invalidate(); setDraft(s); toast({ title: "Prompt regenerated", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Regenerate failed", description: e.message, variant: "destructive" }),
  });
  const setStatus = useMutation({
    mutationFn: (status: "draft" | "queued" | "skipped") => api.projects.scenes.update(projectId, scene.id, { status }),
    onSuccess: invalidate,
    onError: (e: Error) => toast({ title: "Status change failed", description: e.message, variant: "destructive" }),
  });
  const editable = canWrite && !locked;
  const images = assets.filter((a) => a.mime_type.startsWith("image/"));
  const field = (key: keyof Scene, label: string, rows = 2, placeholder = "") => (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Textarea rows={rows} disabled={!editable} value={(draft[key] as string) ?? ""} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} placeholder={placeholder} />
    </div>
  );
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-sans text-sm font-bold text-primary">{scene.code}</span>
        <StatusBadge status={scene.status} />
        {scene.retry_count ? <span className="hud-pill text-hud-amber">retries {scene.retry_count}</span> : null}
        <span className="flex-1" />
        {editable ? (
          <>
            {scene.status !== "draft" && scene.status !== "queued" ? <Button size="sm" variant="outline" onClick={() => setStatus.mutate("draft")} loading={setStatus.isPending}><RefreshCw /> Reset to draft</Button> : null}
            {scene.status !== "skipped" ? <Button size="sm" variant="ghost" onClick={() => setStatus.mutate("skipped")} loading={setStatus.isPending}>Skip scene</Button> : null}
            <Button size="sm" variant="outline" onClick={() => regenerate.mutate()} loading={regenerate.isPending}><Wand2 /> Regenerate prompt</Button>
            <Button size="sm" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}><Save /> Save</Button>
          </>
        ) : null}
      </div>
      {locked ? <p className="border border-hud-amber/50 bg-hud-amber/10 px-2 py-1 font-sans text-[10px] text-hud-amber">A task is running on this project — scenes are read-only until it pauses or finishes.</p> : null}
      {scene.last_error ? <p className="border border-hud-red/50 bg-hud-red/10 px-2 py-1 font-sans text-[10px] text-hud-red">{scene.last_error}</p> : null}

      <div className="grid gap-3 md:grid-cols-[1fr_120px_200px]">
        <div className="space-y-1"><Label>Title</Label><Input disabled={!editable} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></div>
        <div className="space-y-1"><Label>Duration (s)</Label><Input type="number" min={1} max={600} disabled={!editable} value={draft.duration_seconds} onChange={(e) => setDraft({ ...draft, duration_seconds: Number(e.target.value) || 1 })} /></div>
        <div className="space-y-1">
          <Label>Platform</Label>
          <Select disabled={!editable} value={draft.platform_key ?? ""} onChange={(e) => setDraft({ ...draft, platform_key: e.target.value || null })}>
            <option value="">Project default</option>
            {platforms.filter((p) => p.enabled).map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
          </Select>
        </div>
      </div>
      {field("prompt", "Generation prompt (sent to the platform)", 5)}
      <div className="grid gap-3 md:grid-cols-2">
        {field("visual", "Visual description", 3)}
        {field("camera", "Camera", 2)}
        {field("dialogue", "Dialogue", 2)}
        {field("narration", "Narration", 2)}
        <div className="space-y-1"><Label>Location</Label><Input disabled={!editable} value={draft.location} onChange={(e) => setDraft({ ...draft, location: e.target.value })} /></div>
        <div className="space-y-1"><Label>Characters (comma separated)</Label><Input disabled={!editable} value={draft.characters.join(", ")} onChange={(e) => setDraft({ ...draft, characters: e.target.value.split(",").map((c) => c.trim()).filter(Boolean) })} /></div>
      </div>
      {field("negative_prompt", "Negative prompt", 2)}

      <div className="space-y-1">
        <Label>Reference images</Label>
        {images.length === 0 ? (
          <p className="font-sans text-[10px] text-muted-foreground">Upload reference images in the References tab to attach them here.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {images.map((a) => {
              const on = draft.reference_asset_ids.includes(a.id);
              return (
                <button key={a.id} type="button" disabled={!editable} onClick={() => setDraft({ ...draft, reference_asset_ids: on ? draft.reference_asset_ids.filter((x) => x !== a.id) : [...draft.reference_asset_ids, a.id] })} className={cn("flex items-center gap-1.5 border px-2 py-1 font-sans text-[10px]", on ? "border-primary bg-hud-ghost text-primary" : "border-border text-muted-foreground hover:border-primary/50")}>
                  <ImageIcon className="h-3 w-3" /> {truncate(a.filename, 24)}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {Object.keys(scene.generation_metadata ?? {}).length ? (
        <details className="border border-border">
          <summary className="hud-label cursor-pointer px-2 py-1">Generation metadata · {scene.completed_at ? formatDateTime(scene.completed_at) : "in progress"}</summary>
          <pre className="max-h-48 overflow-auto p-2 font-mono text-[10px] text-muted-foreground">{JSON.stringify(scene.generation_metadata, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}
