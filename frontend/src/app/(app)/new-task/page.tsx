"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bug, Clapperboard, Image as ImageIcon, PenLine, Search, Table2, Workflow, type LucideIcon } from "lucide-react";

import { HudPanel } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/misc";
import { api, ApiError } from "@/lib/api";
import { useJarvis } from "@/lib/jarvis-store";
import { canWrite, toast, useAppStore } from "@/lib/store";
import type { Template } from "@/lib/types";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = { clapperboard: Clapperboard, image: ImageIcon, search: Search, table: Table2, bug: Bug, pen: PenLine, workflow: Workflow };

type Kind = "video_generation" | "browser_workflow" | "image_generation" | "research" | "data_entry" | "website_testing" | "content_creation";

function NewTaskForm() {
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const role = useAppStore((s) => s.auth?.role);
  const assistant = useJarvis((s) => s.prefs.assistantName);
  const templates = useQuery({ queryKey: ["templates"], queryFn: api.templates.list });
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api.projects.list() });
  const platforms = useQuery({ queryKey: ["platforms"], queryFn: api.platforms.list });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions.list });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings.get });

  const [templateKey, setTemplateKey] = React.useState(params.get("template") || "browser_workflow");
  const [projectMode, setProjectMode] = React.useState<"none" | "existing" | "new">(params.get("project") ? "existing" : "none");
  const [projectId, setProjectId] = React.useState(params.get("project") || "");
  const [newProject, setNewProject] = React.useState({ name: "", script_text: "", visual_style: "cinematic", clip_duration_seconds: 8 });
  const [title, setTitle] = React.useState("");
  const [instructions, setInstructions] = React.useState(params.get("instructions") || "");
  const [platformKey, setPlatformKey] = React.useState(params.get("platform") || "");
  const [sessionId, setSessionId] = React.useState(params.get("session") || "");
  const [priority, setPriority] = React.useState(5);
  const [approvalMode, setApprovalMode] = React.useState<"confirm_writes" | "confirm_all" | "never">("confirm_writes");
  const [allowedDomains, setAllowedDomains] = React.useState("");
  const [config, setConfig] = React.useState({ max_retries: 3, generation_timeout_seconds: 900, action_timeout_seconds: 30, agent_mode: "auto" as "auto" | "adapter" | "generic", headless: "" as "" | "true" | "false", retry_failed_only: false });
  const [mock, setMock] = React.useState({ speed: "fast", fail_rate: 0, login_required: false, human_check_attempt: 0 });
  const [startNow, setStartNow] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const template: Template | undefined = templates.data?.find((t) => t.key === templateKey);
  const kind = (template?.kind ?? "browser_workflow") as Kind;
  const isVideo = kind === "video_generation";

  React.useEffect(() => {
    if (!template) return;
    const cfg = template.config as { platform_key?: string; instructions?: string };
    if (!platformKey && cfg.platform_key) setPlatformKey(cfg.platform_key);
    if (!instructions && cfg.instructions) setInstructions(cfg.instructions);
    if (kind === "video_generation" && projectMode === "none") setProjectMode(projects.data?.length ? "existing" : "new");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template?.key]);

  React.useEffect(() => {
    if (isVideo && projectMode === "existing" && !projectId && projects.data?.length) setProjectId(projects.data[0].id);
  }, [isVideo, projectMode, projectId, projects.data]);

  const selectedProject = projects.data?.find((p) => p.id === projectId);
  const effectivePlatform = platformKey || selectedProject?.platform_key || "generic";
  const platform = platforms.data?.find((p) => p.key === effectivePlatform);
  const matchingSessions = (sessions.data ?? []).filter((s) => s.platform_key === effectivePlatform || s.platform_key === "generic" || effectivePlatform === "generic");
  const limits = settings.data?.effective_limits ?? {};
  const estimate = useQuery({ queryKey: [`estimate:${projectId}`], queryFn: () => api.projects.estimate(projectId), enabled: isVideo && projectMode === "existing" && !!projectId });

  const submit = useMutation({
    mutationFn: async () => {
      setError(null);
      let project_id: string | undefined = projectMode === "existing" ? projectId || undefined : undefined;
      if (projectMode === "new") {
        if (!newProject.name.trim()) throw new ApiError(422, "validation", "Project name is required");
        const created = await api.projects.create({
          name: newProject.name.trim(),
          template_key: templateKey,
          script_text: newProject.script_text,
          visual_style: newProject.visual_style,
          platform_key: effectivePlatform,
          browser_session_id: sessionId || null,
          output_settings: { clip_duration_seconds: newProject.clip_duration_seconds },
        });
        project_id = created.id;
        if (isVideo && newProject.script_text.trim()) {
          await api.projects.analyze(created.id, { script_text: newProject.script_text, clip_duration_seconds: newProject.clip_duration_seconds });
        }
        queryClient.invalidateQueries({ queryKey: ["projects"] });
      }
      if (!isVideo && !instructions.trim()) throw new ApiError(422, "validation", "Describe what the agent should do");
      const body = {
        title: title.trim() || undefined,
        kind,
        project_id,
        instructions,
        platform_key: effectivePlatform,
        browser_session_id: sessionId || undefined,
        priority,
        start: startNow,
        config: {
          max_retries: config.max_retries,
          generation_timeout_seconds: config.generation_timeout_seconds,
          action_timeout_seconds: config.action_timeout_seconds,
          agent_mode: config.agent_mode,
          approval_mode: approvalMode,
          allowed_domains: allowedDomains.split(/\r?\n/).map((domain) => domain.trim()).filter(Boolean),
          headless: config.headless === "" ? null : config.headless === "true",
          retry_failed_only: config.retry_failed_only,
          mock_options: effectivePlatform === "mock" ? { speed: mock.speed, fail_rate: mock.fail_rate, login_required: mock.login_required, human_check_attempt: mock.human_check_attempt || undefined } : {},
        },
      };
      return api.tasks.create(body);
    },
    onSuccess: (task) => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      useJarvis.getState().push({ role: "sys", text: `Task ${task.title || task.id.slice(0, 8)} ${startNow ? "queued" : "saved as draft"}.`, href: `/tasks/${task.id}` });
      toast({ title: startNow ? "Task queued" : "Draft saved", description: task.title, variant: "success" });
      router.push(`/tasks/${task.id}`);
    },
    onError: (e: Error) => setError(e instanceof ApiError ? e.message : "Could not create task"),
  });

  if (!canWrite(role)) {
    return <div className="p-6"><HudPanel title="READ ONLY"><p className="text-xs text-muted-foreground">Your role is viewer. Ask an organisation admin for member access to create tasks.</p></HudPanel></div>;
  }

  return (
    <div className="space-y-4 p-4">
      <PageHeader eyebrow="Mission planning" title="New task" description={`Pick a workflow, point ${assistant} at a browser session and queue the job. Everything here is persisted — tasks survive restarts.`} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-4">
          <HudPanel title="1 · WORKFLOW">
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {(templates.data ?? []).map((t) => {
                const Icon = ICONS[t.icon] ?? Workflow;
                const active = t.key === templateKey;
                return (
                  <button key={t.id} type="button" onClick={() => setTemplateKey(t.key)} className={cn("hud-corners flex flex-col gap-1 border p-3 text-left transition-colors", active ? "border-primary bg-hud-ghost" : "border-border hover:border-primary/50")}>
                    <Icon className={cn("h-4 w-4", active ? "text-primary" : "text-muted-foreground")} />
                    <span className="font-sans text-[11px] font-bold uppercase tracking-[0.12em]">{t.name}</span>
                    <span className="line-clamp-2 text-[10px] text-muted-foreground">{t.description}</span>
                  </button>
                );
              })}
            </div>
          </HudPanel>

          <HudPanel title={isVideo ? "2 · PROJECT & SCRIPT" : "2 · INSTRUCTIONS"}>
            <div className="space-y-3">
              {isVideo || projectMode !== "none" ? (
                <div className="flex flex-wrap gap-1.5">
                  {(isVideo ? (["existing", "new"] as const) : (["none", "existing", "new"] as const)).map((mode) => (
                    <button key={mode} type="button" onClick={() => setProjectMode(mode)} className={cn("hud-pill", projectMode === mode ? "border-primary text-primary" : "text-muted-foreground")}>
                      {mode === "none" ? "No project" : mode === "existing" ? "Existing project" : "New project"}
                    </button>
                  ))}
                </div>
              ) : (
                <button type="button" className="hud-label hover:text-primary" onClick={() => setProjectMode("existing")}>+ attach to a project (files are saved in its folder)</button>
              )}

              {projectMode === "existing" ? (
                <div className="space-y-1">
                  <Label>Project</Label>
                  <Select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                    <option value="">— select —</option>
                    {(projects.data ?? []).map((p) => (
                      <option key={p.id} value={p.id}>{p.name} · {p.scene_count} scenes · {p.platform_key}</option>
                    ))}
                  </Select>
                  {isVideo && selectedProject && selectedProject.scene_count === 0 ? <p className="text-[10px] text-hud-amber">This project has no scenes yet — open it and analyse the script first.</p> : null}
                  {isVideo && selectedProject?.active_task_id ? <p className="text-[10px] text-hud-amber">A task is already active on this project; the new one will be rejected until it finishes.</p> : null}
                </div>
              ) : null}

              {projectMode === "new" ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1 sm:col-span-2">
                    <Label>Project name</Label>
                    <Input value={newProject.name} onChange={(e) => setNewProject({ ...newProject, name: e.target.value })} placeholder="e.g. Product launch teaser" />
                  </div>
                  {isVideo ? (
                    <>
                      <div className="space-y-1 sm:col-span-2">
                        <Label>Script</Label>
                        <Textarea rows={8} value={newProject.script_text} onChange={(e) => setNewProject({ ...newProject, script_text: e.target.value })} placeholder={"Paste the script. Scenes are detected from headings like “Scene 1 — Rooftop at dusk” or blank-line separated paragraphs."} />
                      </div>
                      <div className="space-y-1">
                        <Label>Visual style</Label>
                        <Input value={newProject.visual_style} onChange={(e) => setNewProject({ ...newProject, visual_style: e.target.value })} />
                      </div>
                      <div className="space-y-1">
                        <Label>Clip length (s)</Label>
                        <Input type="number" min={1} max={60} value={newProject.clip_duration_seconds} onChange={(e) => setNewProject({ ...newProject, clip_duration_seconds: Number(e.target.value) || 8 })} />
                      </div>
                    </>
                  ) : null}
                </div>
              ) : null}

              <div className="space-y-1">
                <Label>{isVideo ? "Extra instructions for the agent (optional)" : "What should the agent do?"}</Label>
                <Textarea rows={isVideo ? 3 : 6} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder={isVideo ? "e.g. Prefer 16:9, keep the same character across scenes." : "e.g. Open https://news.ycombinator.com, read the top 5 titles and save them to summary.md"} />
              </div>
              <div className="space-y-1">
                <Label>Task title (optional)</Label>
                <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Shown in the queue" />
              </div>
            </div>
          </HudPanel>

          <HudPanel title="3 · PLATFORM & BROWSER">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label>Platform</Label>
                <Select value={platformKey} onChange={(e) => { setPlatformKey(e.target.value); setSessionId(""); }}>
                  <option value="">{selectedProject ? `Project default (${selectedProject.platform_key})` : "— select —"}</option>
                  {(platforms.data ?? []).filter((p) => p.enabled).map((p) => (
                    <option key={p.key} value={p.key}>{p.name}{p.adapter_available ? "" : " (generic agent)"}</option>
                  ))}
                </Select>
                {platform ? <p className="text-[10px] text-muted-foreground">{platform.description}{platform.requires_login ? " · Requires a logged-in session." : ""}</p> : null}
              </div>
              <div className="space-y-1">
                <Label>Browser session</Label>
                <Select value={sessionId} onChange={(e) => setSessionId(e.target.value)}>
                  <option value="">Ephemeral session (closed when the task ends)</option>
                  {matchingSessions.map((s) => (
                    <option key={s.id} value={s.id}>{s.name} · {s.status} · {s.login_status.replace("_", " ")}</option>
                  ))}
                </Select>
                {platform?.requires_login && !sessionId ? <p className="text-[10px] text-hud-amber">Pick a persistent session you have already logged into, or the agent will pause for login.</p> : null}
              </div>
              {effectivePlatform === "mock" ? (
                <div className="hud-corners grid gap-2 border border-hud-orange/50 bg-hud-orange/5 p-3 sm:col-span-2 sm:grid-cols-4">
                  <p className="hud-label text-hud-orange sm:col-span-4">Mock video generator · dev mode simulation</p>
                  <div className="space-y-1">
                    <Label>Speed</Label>
                    <Select value={mock.speed} onChange={(e) => setMock({ ...mock, speed: e.target.value })}>
                      <option value="instant">instant</option><option value="fast">fast</option><option value="normal">normal</option><option value="slow">slow</option>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <Label>Fail rate</Label>
                    <Input type="number" min={0} max={1} step={0.1} value={mock.fail_rate} onChange={(e) => setMock({ ...mock, fail_rate: Number(e.target.value) || 0 })} />
                  </div>
                  <div className="space-y-1">
                    <Label>Human check at attempt</Label>
                    <Input type="number" min={0} max={20} value={mock.human_check_attempt} onChange={(e) => setMock({ ...mock, human_check_attempt: Number(e.target.value) || 0 })} />
                  </div>
                  <label className="flex items-end gap-2 pb-2 font-sans text-[10px] uppercase tracking-[0.12em]">
                    <input type="checkbox" className="accent-primary" checked={mock.login_required} onChange={(e) => setMock({ ...mock, login_required: e.target.checked })} /> Require login
                  </label>
                </div>
              ) : null}
            </div>
          </HudPanel>

          <HudPanel title="4 · LIMITS & BEHAVIOUR">
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-1">
                <Label>Priority (1 = highest)</Label>
                <Input type="number" min={1} max={10} value={priority} onChange={(e) => setPriority(Math.min(10, Math.max(1, Number(e.target.value) || 5)))} />
              </div>
              <div className="space-y-1">
                <Label>Max retries per scene</Label>
                <Input type="number" min={0} max={limits.max_retries ?? 10} value={config.max_retries} onChange={(e) => setConfig({ ...config, max_retries: Number(e.target.value) || 0 })} />
              </div>
              <div className="space-y-1">
                <Label>Agent mode</Label>
                <Select value={config.agent_mode} onChange={(e) => setConfig({ ...config, agent_mode: e.target.value as typeof config.agent_mode })}>
                  <option value="auto">auto (adapter if available)</option>
                  <option value="adapter">adapter only</option>
                  <option value="generic">generic AI browser agent</option>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Generation timeout (s)</Label>
                <Input type="number" min={30} max={7200} value={config.generation_timeout_seconds} onChange={(e) => setConfig({ ...config, generation_timeout_seconds: Number(e.target.value) || 900 })} />
              </div>
              <div className="space-y-1">
                <Label>Action timeout (s)</Label>
                <Input type="number" min={5} max={300} value={config.action_timeout_seconds} onChange={(e) => setConfig({ ...config, action_timeout_seconds: Number(e.target.value) || 30 })} />
              </div>
              <div className="space-y-1">
                <Label>Browser window</Label>
                <Select value={config.headless} onChange={(e) => setConfig({ ...config, headless: e.target.value as typeof config.headless })}>
                  <option value="">server default</option>
                  <option value="true">headless</option>
                  <option value="false">headed (visible on the worker)</option>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Browser action approval</Label>
                <Select value={approvalMode} onChange={(e) => setApprovalMode(e.target.value as typeof approvalMode)}>
                  <option value="confirm_writes">Confirm browser and file changes</option>
                  <option value="confirm_all">Confirm every browser and file tool</option>
                  <option value="never">Do not pause for approval</option>
                </Select>
                <p className="text-[10px] text-muted-foreground">Applies to generic-agent and platform-adapter browser actions. Confirmed text, selected values and file paths are hidden in the review panel; confirm_all also gates navigation and page-state changes.</p>
              </div>
              <div className="space-y-1 sm:col-span-2">
                <Label>Allowed browser domains (optional)</Label>
                <Textarea rows={3} value={allowedDomains} onChange={(e) => setAllowedDomains(e.target.value)} placeholder="example.com\nwww.example.org" />
                <p className="text-[10px] text-muted-foreground">One hostname per line. Each entry also allows its subdomains. Leave blank to allow any destination permitted by the global URL-safety policy.</p>
              </div>
              {isVideo ? (
                <label className="flex items-center gap-2 font-sans text-[10px] uppercase tracking-[0.12em] sm:col-span-3">
                  <input type="checkbox" className="accent-primary" checked={config.retry_failed_only} onChange={(e) => setConfig({ ...config, retry_failed_only: e.target.checked })} /> Only (re)generate scenes that are not completed yet
                </label>
              ) : null}
              <p className="text-[10px] text-muted-foreground sm:col-span-3">
                Organisation limits: max task duration {Math.round((limits.max_task_duration_seconds ?? 0) / 60)} min · max scenes {limits.max_scenes_per_task ?? "—"} · concurrent workers {limits.max_concurrent_workers ?? "—"}. Scenes run sequentially unless parallel generation is enabled in the project.
              </p>
            </div>
          </HudPanel>
        </div>

        <div className="space-y-4">
          <HudPanel title="LAUNCH" corners>
            <div className="space-y-3">
              <dl className="space-y-1 font-sans text-[11px]">
                <div className="flex justify-between"><dt className="text-muted-foreground">Workflow</dt><dd>{template?.name ?? "—"}</dd></div>
                <div className="flex justify-between"><dt className="text-muted-foreground">Platform</dt><dd>{platform?.name ?? effectivePlatform}</dd></div>
                <div className="flex justify-between"><dt className="text-muted-foreground">Browser</dt><dd className="truncate pl-2">{sessionId ? sessions.data?.find((s) => s.id === sessionId)?.name : "ephemeral"}</dd></div>
                <div className="flex justify-between"><dt className="text-muted-foreground">Priority</dt><dd>P{priority}</dd></div>
                {estimate.data ? (
                  <>
                    <div className="flex justify-between"><dt className="text-muted-foreground">Scenes</dt><dd>{estimate.data.scenes}</dd></div>
                    <div className="flex justify-between"><dt className="text-muted-foreground">Output</dt><dd>{estimate.data.output_seconds}s of video</dd></div>
                    <div className="flex justify-between"><dt className="text-muted-foreground">Est. duration</dt><dd>{estimate.data.estimated_minutes} min</dd></div>
                  </>
                ) : null}
              </dl>
              {estimate.data?.warnings.map((w) => <p key={w} className="text-[10px] text-hud-amber">⚠ {w}</p>)}
              {estimate.data ? <p className="text-[10px] text-muted-foreground">{estimate.data.platform_credits_note}</p> : null}
              <label className="flex items-center gap-2 font-sans text-[10px] uppercase tracking-[0.12em]">
                <input type="checkbox" className="accent-primary" checked={startNow} onChange={(e) => setStartNow(e.target.checked)} /> Start immediately
              </label>
              {error ? <p className="text-xs text-hud-red">{error}</p> : null}
              <Button className="w-full" loading={submit.isPending} onClick={() => submit.mutate()}>
                {startNow ? "Queue task" : "Save draft"}
              </Button>
              <p className="text-[10px] text-muted-foreground">The worker opens the browser, verifies the login state, and pauses for you whenever a CAPTCHA, MFA or login screen appears.</p>
            </div>
          </HudPanel>
        </div>
      </div>
    </div>
  );
}

export default function NewTaskPage() {
  return (
    <React.Suspense fallback={null}>
      <NewTaskForm />
    </React.Suspense>
  );
}
