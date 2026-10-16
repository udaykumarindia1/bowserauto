"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, Clock3, Globe, Pause, Play, Plus, RotateCcw, ShieldCheck, Trash2 } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudPanel, HudPill } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import { canWrite, toast, useAppStore } from "@/lib/store";
import type { TaskSchedule } from "@/lib/types";

const REPEAT_OPTIONS = [
  { value: "once", label: "One time only" },
  { value: "60", label: "Every hour" },
  { value: "360", label: "Every 6 hours" },
  { value: "1440", label: "Daily" },
  { value: "10080", label: "Weekly" },
];

function localDateTimeValue(date = new Date(Date.now() + 60 * 60 * 1000)) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function displayDate(value: string | null) {
  if (!value) return "Not run yet";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown time" : date.toLocaleString();
}

function repeatLabel(minutes: number | null) {
  if (!minutes) return "One time";
  if (minutes % 1440 === 0) return `Every ${minutes / 1440} day${minutes === 1440 ? "" : "s"}`;
  if (minutes % 60 === 0) return `Every ${minutes / 60} hour${minutes === 60 ? "" : "s"}`;
  return `Every ${minutes} minutes`;
}

function ScheduleCard({
  schedule,
  writable,
  onToggle,
  onDelete,
  busy,
}: {
  schedule: TaskSchedule;
  writable: boolean;
  onToggle: (schedule: TaskSchedule) => void;
  onDelete: (schedule: TaskSchedule) => void;
  busy: boolean;
}) {
  const completedOnce = !schedule.enabled && !schedule.repeat_interval_minutes && !!schedule.last_task_id;
  const domains = Array.isArray(schedule.config.allowed_domains)
    ? (schedule.config.allowed_domains as string[])
    : [];
  return (
    <article className="hud-panel hud-corners flex flex-col gap-3 p-3">
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center border border-primary/20 bg-primary/10 text-primary">
          <CalendarClock className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-sans text-xs font-bold uppercase tracking-[0.1em]">{schedule.title}</h3>
            {completedOnce ? <HudPill tone="green">DISPATCHED</HudPill> : schedule.enabled ? <HudPill tone="green" pulse>ACTIVE</HudPill> : <HudPill tone="dim">PAUSED</HudPill>}
          </div>
          <p className="mt-1 text-[10px] text-muted-foreground">{schedule.kind.replaceAll("_", " ")} · {schedule.platform_key}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-2 border-y border-border/70 py-2 font-sans text-[10px]">
        <div>
          <p className="hud-label flex items-center gap-1"><Clock3 className="h-3 w-3" /> Next run</p>
          <p className="mt-1 text-foreground">{schedule.enabled ? displayDate(schedule.next_run_at) : "—"}</p>
        </div>
        <div>
          <p className="hud-label flex items-center gap-1"><RotateCcw className="h-3 w-3" /> Cadence</p>
          <p className="mt-1 text-foreground">{repeatLabel(schedule.repeat_interval_minutes)}</p>
        </div>
        <div>
          <p className="hud-label">Last run</p>
          <p className="mt-1 text-foreground">{displayDate(schedule.last_run_at)}</p>
        </div>
        <div>
          <p className="hud-label flex items-center gap-1"><ShieldCheck className="h-3 w-3" /> Approval</p>
          <p className="mt-1 text-foreground">{schedule.config.approval_mode === "confirm_all" ? "Every action" : "Writes require approval"}</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <Globe className="h-3 w-3 text-primary" />
        {domains.length ? domains.map((domain) => (
          <span key={domain} className="rounded border border-border bg-secondary/60 px-1.5 py-0.5 font-sans text-[9px] text-muted-foreground">{domain}</span>
        )) : <span className="text-[10px] text-hud-red">No allowed domains</span>}
      </div>

      {schedule.last_error ? (
        <p className="flex items-start gap-1.5 rounded-lg border border-hud-amber/25 bg-hud-amber/5 p-2 font-sans text-[10px] text-hud-amber">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> {schedule.last_error}
        </p>
      ) : null}

      <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">{schedule.instructions}</p>
      <div className="mt-auto flex items-center gap-1.5 border-t border-border/70 pt-2">
        {schedule.last_task_id ? (
          <Link href={`/tasks/${schedule.last_task_id}`}><Button variant="ghost" size="sm">Open latest task</Button></Link>
        ) : null}
        <span className="flex-1" />
        {writable && !completedOnce ? (
          <Button
            variant="outline"
            size="sm"
            loading={busy}
            onClick={() => onToggle(schedule)}
            aria-label={schedule.enabled ? "Pause schedule" : "Resume schedule"}
          >
            {schedule.enabled ? <Pause /> : <Play />}{schedule.enabled ? "Pause" : "Resume"}
          </Button>
        ) : null}
        {writable ? (
          <Button variant="ghost" size="icon-sm" disabled={busy} onClick={() => onDelete(schedule)} title="Delete schedule" aria-label="Delete schedule">
            <Trash2 className="text-hud-red" />
          </Button>
        ) : null}
      </div>
    </article>
  );
}

export default function SchedulesPage() {
  const role = useAppStore((s) => s.auth?.role);
  const writable = canWrite(role);
  const queryClient = useQueryClient();
  const schedules = useQuery({ queryKey: ["schedules"], queryFn: api.schedules.list, refetchInterval: 10_000 });
  const platforms = useQuery({ queryKey: ["platforms"], queryFn: api.platforms.list });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions.list });
  const [confirmDelete, setConfirmDelete] = React.useState<TaskSchedule | null>(null);
  const [runAt, setRunAt] = React.useState("");
  const [form, setForm] = React.useState({
    title: "",
    kind: "browser_workflow",
    instructions: "",
    platform_key: "generic",
    browser_session_id: "",
    priority: "5",
    repeat: "once",
    allowed_domains: "",
    approval_mode: "confirm_writes" as "confirm_writes" | "confirm_all",
  });

  React.useEffect(() => setRunAt(localDateTimeValue()), []);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["schedules"] });
  const create = useMutation({
    mutationFn: () => {
      const allowedDomains = form.allowed_domains.split(/[\n,]/).map((host) => host.trim()).filter(Boolean);
      if (!allowedDomains.length) throw new Error("Add at least one exact allowed hostname.");
      if (!runAt || new Date(runAt).getTime() <= Date.now()) throw new Error("Choose a future run time.");
      return api.schedules.create({
        title: form.title.trim(),
        kind: form.kind,
        instructions: form.instructions.trim(),
        platform_key: form.platform_key,
        browser_session_id: form.browser_session_id || undefined,
        priority: Number(form.priority),
        run_at: new Date(runAt).toISOString(),
        repeat_interval_minutes: form.repeat === "once" ? null : Number(form.repeat),
        config: {
          agent_mode: "generic",
          approval_mode: form.approval_mode,
          allowed_domains: allowedDomains,
        },
      });
    },
    onSuccess: () => {
      refresh();
      setForm((current) => ({ ...current, title: "", instructions: "", allowed_domains: "" }));
      toast({ title: "Schedule created", description: "The worker will queue it at the selected time.", variant: "success" });
    },
    onError: (error: Error) => toast({ title: "Could not create schedule", description: error.message, variant: "destructive" }),
  });
  const update = useMutation({
    mutationFn: (schedule: TaskSchedule) => api.schedules.update(schedule.id, { enabled: !schedule.enabled }),
    onSuccess: () => { refresh(); toast({ title: "Schedule updated", variant: "success" }); },
    onError: (error: Error) => toast({ title: "Could not update schedule", description: error.message, variant: "destructive" }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.schedules.remove(id),
    onSuccess: () => { refresh(); setConfirmDelete(null); toast({ title: "Schedule deleted", variant: "success" }); },
    onError: (error: Error) => toast({ title: "Could not delete schedule", description: error.message, variant: "destructive" }),
  });

  const enabledPlatforms = (platforms.data ?? []).filter((platform) => platform.enabled);
  const visibleSchedules = schedules.data ?? [];
  const busyFor = (id: string) => (update.isPending && update.variables?.id === id) || (remove.isPending && remove.variables === id);

  return (
    <div className="space-y-4 p-4">
      <PageHeader
        eyebrow="Allowlisted automation"
        title="Scheduled workflows"
        description="Run generic browser tasks on a one-time or recurring cadence. Each run uses an exact domain allowlist and the normal action-approval controls."
      />
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(320px,0.78fr)_minmax(0,1.5fr)]">
        {writable ? (
          <HudPanel title="▸ CREATE SCHEDULE" className="xl:sticky xl:top-4" corners>
            <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
              <div className="space-y-1"><Label htmlFor="schedule-title">Workflow name</Label><Input id="schedule-title" required maxLength={200} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder="Daily public status check" /></div>
              <div className="space-y-1"><Label htmlFor="schedule-instructions">Task instructions</Label><Textarea id="schedule-instructions" required maxLength={20_000} value={form.instructions} onChange={(event) => setForm({ ...form, instructions: event.target.value })} placeholder="Describe the task the browser agent should perform…" /></div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1"><Label htmlFor="schedule-kind">Workflow type</Label><Select id="schedule-kind" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
                  <option value="browser_workflow">Browser workflow</option><option value="research">Research</option><option value="data_entry">Data entry</option><option value="website_testing">Website testing</option><option value="content_creation">Content creation</option>
                </Select></div>
                <div className="space-y-1"><Label htmlFor="schedule-platform">Adapter</Label><Select id="schedule-platform" value={form.platform_key} onChange={(event) => setForm({ ...form, platform_key: event.target.value })}>
                  {enabledPlatforms.map((platform) => <option key={platform.key} value={platform.key}>{platform.name}</option>)}
                </Select></div>
              </div>
              <div className="space-y-1"><Label htmlFor="schedule-session">Browser profile (optional)</Label><Select id="schedule-session" value={form.browser_session_id} onChange={(event) => setForm({ ...form, browser_session_id: event.target.value })}>
                <option value="">Worker-managed profile</option>
                {(sessions.data ?? []).filter((session) => session.status !== "expired").map((session) => <option key={session.id} value={session.id}>{session.name} · {session.platform_key}</option>)}
              </Select><p className="text-[10px] text-muted-foreground">Existing logins stay on the worker. Authentication challenges must be completed manually.</p></div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1"><Label htmlFor="schedule-run-at">First run</Label><Input id="schedule-run-at" type="datetime-local" required value={runAt} onChange={(event) => setRunAt(event.target.value)} /></div>
                <div className="space-y-1"><Label htmlFor="schedule-repeat">Repeat</Label><Select id="schedule-repeat" value={form.repeat} onChange={(event) => setForm({ ...form, repeat: event.target.value })}>
                  {REPEAT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </Select></div>
              </div>
              <div className="space-y-1"><Label htmlFor="schedule-domains">Exact allowed hostnames</Label><Textarea id="schedule-domains" required value={form.allowed_domains} onChange={(event) => setForm({ ...form, allowed_domains: event.target.value })} placeholder={'status.example.com\nwww.example.org'} className="min-h-[72px]" /><p className="text-[10px] text-muted-foreground">One hostname per line or comma-separated. No scheme, port, path, wildcard, or IP address.</p></div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1"><Label htmlFor="schedule-approval">Action approval</Label><Select id="schedule-approval" value={form.approval_mode} onChange={(event) => setForm({ ...form, approval_mode: event.target.value as "confirm_writes" | "confirm_all" })}>
                  <option value="confirm_writes">Approve write actions</option><option value="confirm_all">Approve every action</option>
                </Select></div>
                <div className="space-y-1"><Label htmlFor="schedule-priority">Priority (1–10)</Label><Input id="schedule-priority" type="number" min={1} max={10} value={form.priority} onChange={(event) => setForm({ ...form, priority: event.target.value })} /></div>
              </div>
              <div className="flex gap-2 rounded-lg border border-primary/20 bg-primary/5 p-2.5 font-sans text-[10px] leading-relaxed text-muted-foreground"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" /><span>Schedules cannot disable approval. A run is queued as a regular task; approval, domain checks, and manual login challenges still apply.</span></div>
              <Button className="w-full" type="submit" loading={create.isPending}><Plus /> Create scheduled workflow</Button>
            </form>
          </HudPanel>
        ) : null}

        <div className="space-y-3">
          {!writable ? <div className="rounded-xl border border-border bg-card/50 p-3 text-xs text-muted-foreground">Your role can view schedules but cannot change them.</div> : null}
          {schedules.isLoading ? (
            <div className="grid gap-3 md:grid-cols-2"><Skeleton className="h-64" /><Skeleton className="h-64" /></div>
          ) : schedules.isError ? (
            <ErrorState error={schedules.error} retry={() => schedules.refetch()} />
          ) : visibleSchedules.length === 0 ? (
            <HudPanel><EmptyState icon={CalendarClock} title="No scheduled workflows" description="Create a future run with a domain allowlist and explicit approval policy." /></HudPanel>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {visibleSchedules.map((schedule) => (
                <ScheduleCard
                  key={schedule.id}
                  schedule={schedule}
                  writable={writable}
                  onToggle={(item) => update.mutate(item)}
                  onDelete={(item) => setConfirmDelete(item)}
                  busy={busyFor(schedule.id)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
      <ConfirmBanner
        open={!!confirmDelete}
        title="DELETE SCHEDULE"
        detail={`Delete “${confirmDelete?.title ?? ""}”? Existing tasks created from it will not be removed.`}
        tone="danger"
        busy={remove.isPending}
        onConfirm={() => confirmDelete && remove.mutate(confirmDelete.id)}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
