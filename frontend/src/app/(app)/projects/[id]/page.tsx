"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowLeft, Download, FileVideo, Play, Plus, Trash2, Upload, Users, Wand2 } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudPanel, HudPill, MetricBar } from "@/components/hud/hud-primitives";
import { SceneForm, SceneList } from "@/components/scenes/scene-editor";
import { TaskRow } from "@/components/tasks/task-card";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/misc";
import { StatusBadge } from "@/components/ui/status-badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { canWrite, toast, useAppStore } from "@/lib/store";
import type { Character, ProjectDetail } from "@/lib/types";
import { cn, formatBytes, formatDuration, isActiveTaskStatus, timeAgo, truncate } from "@/lib/utils";

function ScriptTab({ project, editable }: { project: ProjectDetail; editable: boolean }) {
  const queryClient = useQueryClient();
  const [script, setScript] = React.useState(project.script_text);
  const [clip, setClip] = React.useState(project.output_settings.clip_duration_seconds ?? 8);
  const [target, setTarget] = React.useState<number | "">(project.output_settings.total_duration_seconds ?? "");
  const [confirm, setConfirm] = React.useState(false);
  React.useEffect(() => setScript(project.script_text), [project.script_text]);
  const save = useMutation({
    mutationFn: () => api.projects.update(project.id, { script_text: script }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: [`project:${project.id}`] }); toast({ title: "Script saved", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Save failed", description: e.message, variant: "destructive" }),
  });
  const analyze = useMutation({
    mutationFn: () => api.projects.analyze(project.id, { script_text: script, clip_duration_seconds: Number(clip) || null, target_duration_seconds: target === "" ? null : Number(target) }),
    onSuccess: (r) => { queryClient.invalidateQueries({ queryKey: [`project:${project.id}`] }); setConfirm(false); toast({ title: `${r.scenes.length} scenes generated`, description: `${r.method}${r.warnings.length ? ` · ${r.warnings[0]}` : ""}`, variant: "success" }); },
    onError: (e: Error) => { setConfirm(false); toast({ title: "Analysis failed", description: e.message, variant: "destructive" }); },
  });
  return (
    <div className="space-y-3 p-3">
      <ConfirmBanner open={confirm} title="REPLACE SCENES" detail={`Analysing the script will replace the existing ${project.scenes.length} scene(s) and their edits with a fresh breakdown.`} busy={analyze.isPending} onConfirm={() => analyze.mutate()} onCancel={() => setConfirm(false)} />
      <Textarea rows={18} disabled={!editable} value={script} onChange={(e) => setScript(e.target.value)} placeholder="Paste the script here. Use headings like “Scene 1 — Title” or blank lines between scenes." className="font-sans" />
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1"><Label>Clip length (s)</Label><Input type="number" className="w-28" min={1} max={60} value={clip} onChange={(e) => setClip(Number(e.target.value) || 8)} /></div>
        <div className="space-y-1"><Label>Target total (s, optional)</Label><Input type="number" className="w-36" min={1} value={target} onChange={(e) => setTarget(e.target.value === "" ? "" : Number(e.target.value))} /></div>
        <span className="flex-1" />
        {editable ? (
          <>
            <Button variant="outline" size="sm" disabled={script === project.script_text} loading={save.isPending} onClick={() => save.mutate()}>Save script</Button>
            <Button size="sm" disabled={!script.trim()} loading={analyze.isPending} onClick={() => (project.scenes.length ? setConfirm(true) : analyze.mutate())}><Wand2 /> Analyse into scenes</Button>
          </>
        ) : null}
      </div>
      <p className="font-sans text-[10px] text-muted-foreground">Scenes are produced by the configured LLM when available, otherwise by a deterministic heuristic splitter. Every scene stays editable before generation.</p>
    </div>
  );
}

function ReferencesTab({ project, editable }: { project: ProjectDetail; editable: boolean }) {
  const queryClient = useQueryClient();
  const assets = useQuery({ queryKey: [`assets:${project.id}`], queryFn: () => api.projects.assets.list(project.id) });
  const characters = useQuery({ queryKey: [`characters:${project.id}`], queryFn: () => api.projects.characters.list(project.id) });
  const fileRef = React.useRef<HTMLInputElement>(null);
  const [previews, setPreviews] = React.useState<Record<string, string>>({});
  const invalidate = () => { queryClient.invalidateQueries({ queryKey: [`assets:${project.id}`] }); queryClient.invalidateQueries({ queryKey: [`project:${project.id}`] }); };
  const upload = useMutation({
    mutationFn: async (files: File[]) => { for (const f of files) await api.projects.assets.upload(project.id, f, "reference"); },
    onSuccess: () => { invalidate(); toast({ title: "Reference uploaded", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Upload failed", description: e.message, variant: "destructive" }),
  });
  const remove = useMutation({ mutationFn: (id: string) => api.projects.assets.remove(project.id, id), onSuccess: invalidate, onError: (e: Error) => toast({ title: "Delete failed", description: e.message, variant: "destructive" }) });
  const [charForm, setCharForm] = React.useState<Partial<Character> & { name: string }>({ name: "", description: "", voice_style: "", reference_asset_id: null });
  const saveChar = useMutation({
    mutationFn: () => (charForm.id ? api.projects.characters.update(project.id, charForm.id, charForm) : api.projects.characters.create(project.id, charForm)),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: [`characters:${project.id}`] }); setCharForm({ name: "", description: "", voice_style: "", reference_asset_id: null }); },
    onError: (e: Error) => toast({ title: "Character save failed", description: e.message, variant: "destructive" }),
  });
  const removeChar = useMutation({ mutationFn: (id: string) => api.projects.characters.remove(project.id, id), onSuccess: () => queryClient.invalidateQueries({ queryKey: [`characters:${project.id}`] }) });

  React.useEffect(() => {
    const images = (assets.data ?? []).filter((a) => a.mime_type.startsWith("image/") && !previews[a.id]);
    if (!images.length) return;
    let cancelled = false;
    (async () => {
      const next: Record<string, string> = {};
      for (const a of images.slice(0, 24)) {
        try { next[a.id] = (await api.projects.assets.url(project.id, a.id)).url; } catch { /* preview optional */ }
      }
      if (!cancelled) setPreviews((p) => ({ ...p, ...next }));
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assets.data, project.id]);

  return (
    <div className="grid gap-3 p-3 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <p className="hud-label">Reference images & files · {assets.data?.length ?? 0}</p>
          {editable ? (
            <>
              <input ref={fileRef} type="file" multiple accept="image/*,video/*,.pdf,.txt" className="hidden" onChange={(e) => e.target.files && upload.mutate(Array.from(e.target.files))} />
              <Button size="sm" variant="outline" loading={upload.isPending} onClick={() => fileRef.current?.click()}><Upload /> Upload</Button>
            </>
          ) : null}
        </div>
        {assets.isLoading ? <Skeleton className="h-32" /> : (assets.data?.length ?? 0) === 0 ? (
          <EmptyState title="No references yet" description="Upload character sheets, style frames or product shots. Attach them to scenes in the editor; adapters upload them to the platform before prompting." />
        ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
            {assets.data!.map((a) => (
              <div key={a.id} className="group border border-border">
                {previews[a.id] ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={previews[a.id]} alt={a.filename} className="aspect-square w-full bg-black object-cover" />
                ) : (
                  <div className="flex aspect-square items-center justify-center bg-hud-dark font-sans text-[10px] text-muted-foreground">{a.mime_type}</div>
                )}
                <div className="flex items-center gap-1 px-1.5 py-1">
                  <span className="min-w-0 flex-1 truncate font-sans text-[10px]" title={a.filename}>{a.filename}</span>
                  <span className="font-sans text-[9px] text-muted-foreground">{formatBytes(a.size_bytes)}</span>
                  {editable ? <button type="button" onClick={() => remove.mutate(a.id)} className="text-muted-foreground hover:text-hud-red"><Trash2 className="h-3 w-3" /></button> : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="space-y-2">
        <p className="hud-label flex items-center gap-1"><Users className="h-3 w-3" /> Characters · {characters.data?.length ?? 0}</p>
        <ul className="divide-y divide-border border border-border">
          {(characters.data ?? []).map((c) => (
            <li key={c.id} className="flex items-start gap-2 px-2 py-1.5">
              <div className="min-w-0 flex-1">
                <p className="font-sans text-[11px] font-bold">{c.name}</p>
                <p className="line-clamp-2 text-[10px] text-muted-foreground">{c.description}{c.voice_style ? ` · voice: ${c.voice_style}` : ""}</p>
              </div>
              {editable ? (
                <>
                  <button type="button" className="hud-label hover:text-primary" onClick={() => setCharForm({ id: c.id, name: c.name, description: c.description, voice_style: c.voice_style, reference_asset_id: c.reference_asset_id })}>edit</button>
                  <button type="button" className="text-muted-foreground hover:text-hud-red" onClick={() => removeChar.mutate(c.id)}><Trash2 className="h-3 w-3" /></button>
                </>
              ) : null}
            </li>
          ))}
          {(characters.data?.length ?? 0) === 0 ? <li className="px-2 py-3 text-center font-sans text-[10px] text-muted-foreground">Characters keep prompts consistent across scenes.</li> : null}
        </ul>
        {editable ? (
          <div className="space-y-2 border border-border p-2">
            <p className="hud-label">{charForm.id ? "Edit character" : "Add character"}</p>
            <Input placeholder="Name" value={charForm.name} onChange={(e) => setCharForm({ ...charForm, name: e.target.value })} />
            <Textarea rows={2} placeholder="Appearance / description" value={charForm.description ?? ""} onChange={(e) => setCharForm({ ...charForm, description: e.target.value })} />
            <Input placeholder="Voice style (optional)" value={charForm.voice_style ?? ""} onChange={(e) => setCharForm({ ...charForm, voice_style: e.target.value })} />
            <Select value={charForm.reference_asset_id ?? ""} onChange={(e) => setCharForm({ ...charForm, reference_asset_id: e.target.value || null })}>
              <option value="">No reference image</option>
              {(assets.data ?? []).filter((a) => a.mime_type.startsWith("image/")).map((a) => <option key={a.id} value={a.id}>{truncate(a.filename, 30)}</option>)}
            </Select>
            <div className="flex gap-1.5">
              <Button size="sm" disabled={!charForm.name.trim()} loading={saveChar.isPending} onClick={() => saveChar.mutate()}>{charForm.id ? "Save" : "Add"}</Button>
              {charForm.id ? <Button size="sm" variant="ghost" onClick={() => setCharForm({ name: "", description: "", voice_style: "", reference_asset_id: null })}>Cancel</Button> : null}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function FilesTab({ projectId }: { projectId: string }) {
  const files = useQuery({ queryKey: ["files", projectId], queryFn: () => api.files.list({ project_id: projectId, limit: 200 }), refetchInterval: 15000 });
  if (files.isLoading) return <div className="p-3"><Skeleton className="h-32" /></div>;
  const items = files.data?.items ?? [];
  if (!items.length) return <div className="p-3"><EmptyState icon={FileVideo} title="No generated files yet" description="Clips land here as scene_001.mp4, scene_002.mp4 … with their metadata." /></div>;
  return (
    <ul className="divide-y divide-border">
      {items.map((f) => (
        <li key={f.id} className="flex items-center gap-3 px-3 py-2 font-sans text-[11px]">
          <FileVideo className="h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <p className="truncate font-bold">{f.filename}</p>
            <p className="truncate text-[10px] text-muted-foreground">{f.platform_key} · {formatBytes(f.size_bytes)}{f.duration_seconds ? ` · ${formatDuration(f.duration_seconds)}` : ""}{f.width ? ` · ${f.width}×${f.height}` : ""} · {timeAgo(f.created_at)}</p>
          </div>
          <StatusBadge status={f.status} />
          <a href={f.download.url} className="hud-pill text-primary hover:bg-hud-ghost" download><Download className="h-3 w-3" /> download</a>
        </li>
      ))}
    </ul>
  );
}

function SettingsTab({ project, editable }: { project: ProjectDetail; editable: boolean }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const platforms = useQuery({ queryKey: ["platforms"], queryFn: api.platforms.list });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions.list });
  const [form, setForm] = React.useState({ name: project.name, description: project.description, visual_style: project.visual_style, platform_key: project.platform_key, browser_session_id: project.browser_session_id ?? "", output: { resolution: "1080p", aspect_ratio: "16:9", clip_duration_seconds: 8, filename_pattern: "scene_{index:03d}{ext}", output_folder: "generated", total_duration_seconds: null as number | null, parallel_generation: false, ...project.output_settings } });
  const [confirmArchive, setConfirmArchive] = React.useState(false);
  const save = useMutation({
    mutationFn: () => api.projects.update(project.id, { name: form.name, description: form.description, visual_style: form.visual_style, platform_key: form.platform_key, browser_session_id: form.browser_session_id || null, output_settings: form.output }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: [`project:${project.id}`] }); queryClient.invalidateQueries({ queryKey: ["projects"] }); toast({ title: "Project saved", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Save failed", description: e.message, variant: "destructive" }),
  });
  const archive = useMutation({
    mutationFn: () => api.projects.update(project.id, { status: project.status === "archived" ? "active" : "archived" }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["projects"] }); queryClient.invalidateQueries({ queryKey: [`project:${project.id}`] }); setConfirmArchive(false); router.push("/projects"); },
  });
  return (
    <div className="grid gap-3 p-3 md:grid-cols-2">
      <ConfirmBanner open={confirmArchive} title={project.status === "archived" ? "RESTORE PROJECT" : "ARCHIVE PROJECT"} detail="Archived projects are hidden from lists but keep all files." busy={archive.isPending} onConfirm={() => archive.mutate()} onCancel={() => setConfirmArchive(false)} />
      <div className="space-y-1 md:col-span-2"><Label>Name</Label><Input disabled={!editable} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
      <div className="space-y-1 md:col-span-2"><Label>Description</Label><Textarea rows={2} disabled={!editable} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
      <div className="space-y-1"><Label>Visual style</Label><Input disabled={!editable} value={form.visual_style} onChange={(e) => setForm({ ...form, visual_style: e.target.value })} /></div>
      <div className="space-y-1">
        <Label>Default platform</Label>
        <Select disabled={!editable} value={form.platform_key} onChange={(e) => setForm({ ...form, platform_key: e.target.value })}>
          {(platforms.data ?? []).map((p) => <option key={p.key} value={p.key}>{p.name}{p.enabled ? "" : " (disabled)"}</option>)}
        </Select>
      </div>
      <div className="space-y-1 md:col-span-2">
        <Label>Default browser session</Label>
        <Select disabled={!editable} value={form.browser_session_id} onChange={(e) => setForm({ ...form, browser_session_id: e.target.value })}>
          <option value="">None (choose per task)</option>
          {(sessions.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name} · {s.platform_key} · {s.login_status.replace("_", " ")}</option>)}
        </Select>
      </div>
      <div className="space-y-1"><Label>Resolution</Label><Input disabled={!editable} value={form.output.resolution} onChange={(e) => setForm({ ...form, output: { ...form.output, resolution: e.target.value } })} /></div>
      <div className="space-y-1"><Label>Aspect ratio</Label><Input disabled={!editable} value={form.output.aspect_ratio} onChange={(e) => setForm({ ...form, output: { ...form.output, aspect_ratio: e.target.value } })} /></div>
      <div className="space-y-1"><Label>Clip length (s)</Label><Input type="number" min={1} max={60} disabled={!editable} value={form.output.clip_duration_seconds} onChange={(e) => setForm({ ...form, output: { ...form.output, clip_duration_seconds: Number(e.target.value) || 8 } })} /></div>
      <div className="space-y-1"><Label>Filename pattern</Label><Input disabled={!editable} value={form.output.filename_pattern} onChange={(e) => setForm({ ...form, output: { ...form.output, filename_pattern: e.target.value } })} /></div>
      <label className="flex items-center gap-2 font-sans text-[10px] uppercase tracking-[0.12em] md:col-span-2">
        <input type="checkbox" className="accent-primary" disabled={!editable} checked={!!form.output.parallel_generation} onChange={(e) => setForm({ ...form, output: { ...form.output, parallel_generation: e.target.checked } })} />
        Allow parallel scene generation (off = strictly one scene at a time)
      </label>
      <p className="font-sans text-[10px] text-muted-foreground md:col-span-2">Folder: {project.folder_path || `projects/${project.id}`} (script / references / scenes / generated / final / logs)</p>
      {editable ? (
        <div className="flex gap-2 md:col-span-2">
          <Button size="sm" loading={save.isPending} onClick={() => save.mutate()}>Save settings</Button>
          <Button size="sm" variant="outline" onClick={() => setConfirmArchive(true)}><Archive /> {project.status === "archived" ? "Restore" : "Archive"}</Button>
        </div>
      ) : null}
    </div>
  );
}

function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const role = useAppStore((s) => s.auth?.role);
  const project = useQuery({ queryKey: [`project:${id}`], queryFn: () => api.projects.get(id), refetchInterval: 10000 });
  const tasks = useQuery({ queryKey: ["tasks", "project", id], queryFn: () => api.tasks.list({ project_id: id, limit: 20 }), refetchInterval: 10000 });
  const platforms = useQuery({ queryKey: ["platforms"], queryFn: api.platforms.list });
  const assets = useQuery({ queryKey: [`assets:${id}`], queryFn: () => api.projects.assets.list(id) });
  const [selected, setSelected] = React.useState<string | null>(params.get("scene"));
  const [tab, setTab] = React.useState(params.get("tab") ?? "scenes");
  const [confirmGenerate, setConfirmGenerate] = React.useState(false);
  const activeTask = (tasks.data?.items ?? []).find((t) => isActiveTaskStatus(t.status));
  const locked = !!activeTask && activeTask.status !== "paused" && activeTask.status !== "needs_attention" && activeTask.status !== "waiting_for_human";
  const writable = canWrite(role);

  const addScene = useMutation({
    mutationFn: () => api.projects.scenes.add(id, { title: `Scene ${(project.data?.scenes.length ?? 0) + 1}`, duration_seconds: project.data?.output_settings.clip_duration_seconds ?? 8 }, selected ?? undefined),
    onSuccess: (s) => { queryClient.invalidateQueries({ queryKey: [`project:${id}`] }); setSelected(s.id); },
    onError: (e: Error) => toast({ title: "Could not add scene", description: e.message, variant: "destructive" }),
  });
  const generate = useMutation({
    mutationFn: (retryFailedOnly: boolean) => api.tasks.create({ kind: "video_generation", project_id: id, title: `Generate · ${project.data?.name ?? ""}`, platform_key: project.data?.platform_key, browser_session_id: project.data?.browser_session_id ?? undefined, instructions: "", config: { retry_failed_only: retryFailedOnly, mock_options: project.data?.platform_key === "mock" ? { speed: "fast" } : {} }, start: true }),
    onSuccess: (t) => { queryClient.invalidateQueries({ queryKey: ["tasks"] }); setConfirmGenerate(false); toast({ title: "Generation queued", variant: "success" }); router.push(`/tasks/${t.id}`); },
    onError: (e: Error) => { setConfirmGenerate(false); toast({ title: "Could not start", description: e.message, variant: "destructive" }); },
  });

  React.useEffect(() => {
    if (!selected && project.data?.scenes.length) setSelected(project.data.scenes[0].id);
  }, [project.data, selected]);

  if (project.isLoading) return <div className="space-y-3 p-4"><Skeleton className="h-10" /><Skeleton className="h-[500px]" /></div>;
  if (project.isError || !project.data) return <div className="p-6"><ErrorState error={project.error} retry={() => project.refetch()} /></div>;
  const p = project.data;
  const stats = p.stats ?? {};
  const total = p.scenes.length;
  const completed = p.scenes.filter((s) => s.status === "completed").length;
  const pending = p.scenes.filter((s) => s.status !== "completed" && s.status !== "skipped").length;
  const scene = p.scenes.find((s) => s.id === selected) ?? null;

  return (
    <div className="space-y-3 p-3">
      <ConfirmBanner open={confirmGenerate} title="QUEUE GENERATION" detail={`Generate ${pending} pending scene(s) on ${p.platform_key}, one at a time. Completed scenes are kept.`} busy={generate.isPending} onConfirm={() => generate.mutate(true)} onCancel={() => setConfirmGenerate(false)} />
      <div className="flex flex-wrap items-center gap-2">
        <Link href="/projects" className="hud-label flex items-center gap-1 hover:text-primary"><ArrowLeft className="h-3 w-3" /> Projects</Link>
        <span className="text-border">/</span>
        <h1 className="min-w-0 flex-1 truncate font-sans text-sm font-bold uppercase tracking-[0.15em] text-primary">{p.name}</h1>
        <HudPill tone="dim">{p.platform_key}</HudPill>
        <HudPill tone="dim">{p.visual_style}</HudPill>
        <StatusBadge status={activeTask?.status ?? p.status} />
        <a href={api.projects.exportUrl(p.id)} className="hud-pill text-muted-foreground hover:text-primary"><Download className="h-3 w-3" /> export</a>
        {writable ? (
          activeTask ? (
            <Link href={`/tasks/${activeTask.id}`}><Button size="sm" variant="outline">Open running task</Button></Link>
          ) : (
            <Button size="sm" disabled={pending === 0} onClick={() => setConfirmGenerate(true)}><Play /> Generate {pending ? `${pending} scene${pending === 1 ? "" : "s"}` : "— all done"}</Button>
          )
        ) : null}
      </div>
      <div className="grid gap-2 sm:grid-cols-4">
        <div className="hud-panel p-2"><MetricBar label={`Completed ${completed}/${total}`} value={total ? (completed / total) * 100 : 0} /></div>
        <div className="hud-panel flex items-center justify-between p-2 font-sans text-[10px]"><span className="text-muted-foreground">FAILED</span><span className={cn(stats.failed ? "text-hud-red" : "")}>{stats.failed ?? p.scenes.filter((s) => s.status === "failed").length}</span></div>
        <div className="hud-panel flex items-center justify-between p-2 font-sans text-[10px]"><span className="text-muted-foreground">OUTPUT</span><span>{formatDuration(p.scenes.reduce((a, s) => a + (s.duration_seconds || 0), 0))}</span></div>
        <div className="hud-panel flex items-center justify-between p-2 font-sans text-[10px]"><span className="text-muted-foreground">UPDATED</span><span>{timeAgo(p.updated_at)}</span></div>
      </div>

      {activeTask ? <TaskRow task={activeTask} /> : null}

      <HudPanel padded={false}>
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="m-2">
            <TabsTrigger value="scenes">Scenes · {total}</TabsTrigger>
            <TabsTrigger value="script">Script</TabsTrigger>
            <TabsTrigger value="references">References</TabsTrigger>
            <TabsTrigger value="files">Generated files</TabsTrigger>
            <TabsTrigger value="tasks">Tasks</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
          </TabsList>
          <TabsContent value="scenes">
            {total === 0 ? (
              <div className="p-3"><EmptyState title="No scenes yet" description="Paste a script in the Script tab and analyse it, or add scenes manually." action={writable ? <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setTab("script")}>Open script</Button><Button size="sm" onClick={() => addScene.mutate()} loading={addScene.isPending}><Plus /> Add scene</Button></div> : undefined} /></div>
            ) : (
              <div className="grid lg:grid-cols-[320px_minmax(0,1fr)]">
                <div className="border-b border-border lg:border-b-0 lg:border-r">
                  <div className="flex items-center justify-between border-b border-border px-2 py-1.5">
                    <span className="hud-label">Scenes</span>
                    {writable && !locked ? <button type="button" className="hud-label flex items-center gap-1 hover:text-primary" onClick={() => addScene.mutate()}><Plus className="h-3 w-3" /> add after selected</button> : null}
                  </div>
                  <div className="max-h-[640px] overflow-y-auto">
                    <SceneList scenes={p.scenes} selectedId={selected} onSelect={setSelected} projectId={p.id} canWrite={writable} locked={locked} />
                  </div>
                </div>
                <div className="p-3">
                  {scene ? <SceneForm scene={scene} projectId={p.id} platforms={platforms.data ?? []} assets={assets.data ?? []} canWrite={writable} locked={locked} /> : <p className="font-sans text-[11px] text-muted-foreground">Select a scene.</p>}
                </div>
              </div>
            )}
          </TabsContent>
          <TabsContent value="script"><ScriptTab project={p} editable={writable && !locked} /></TabsContent>
          <TabsContent value="references"><ReferencesTab project={p} editable={writable} /></TabsContent>
          <TabsContent value="files"><FilesTab projectId={p.id} /></TabsContent>
          <TabsContent value="tasks">
            <div className="space-y-1.5 p-2">
              {(tasks.data?.items ?? []).map((t) => <TaskRow key={t.id} task={t} />)}
              {(tasks.data?.items.length ?? 0) === 0 ? <EmptyState title="No tasks for this project" description="Use Generate above or create a custom task." action={<Link href={`/new-task?project=${p.id}&template=${p.template_key}`}><Button size="sm">New task</Button></Link>} /> : null}
            </div>
          </TabsContent>
          <TabsContent value="settings"><SettingsTab project={p} editable={writable} /></TabsContent>
        </Tabs>
      </HudPanel>
    </div>
  );
}

export default function ProjectPage() {
  return (
    <React.Suspense fallback={null}>
      <ProjectView />
    </React.Suspense>
  );
}
