"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, KeyRound, Trash2 } from "lucide-react";

import { MemoryPanel } from "@/components/hud/memory-panel";
import { HudPanel, HudPill } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/hooks/use-auth";
import { browserNotificationsEnabled } from "@/hooks/use-realtime";
import { api } from "@/lib/api";
import { useJarvis } from "@/lib/jarvis-store";
import { isAdmin, toast, useAppStore } from "@/lib/store";
import type { OrgSettings } from "@/lib/types";
import { formatDuration, titleCase } from "@/lib/utils";

const LIMIT_LABELS: Record<string, { label: string; unit: string }> = {
  max_task_duration_seconds: { label: "Max task duration", unit: "seconds" },
  max_retries: { label: "Max retries per scene", unit: "" },
  max_scenes_per_task: { label: "Max scenes per task", unit: "" },
  max_concurrent_tasks: { label: "Max concurrent tasks", unit: "" },
  max_file_size_mb: { label: "Max generated file size", unit: "MB" },
  max_browser_sessions: { label: "Max browser sessions", unit: "" },
  generation_timeout_seconds: { label: "Default generation timeout", unit: "seconds" },
};

function ProfileTab() {
  const { user, refresh } = useAuth();
  const [name, setName] = React.useState(user?.full_name ?? "");
  React.useEffect(() => setName(user?.full_name ?? ""), [user?.full_name]);
  const save = useMutation({
    mutationFn: () => api.auth.updateMe({ full_name: name.trim() }),
    onSuccess: () => { refresh(); toast({ title: "Profile saved", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Save failed", description: e.message, variant: "destructive" }),
  });
  return (
    <HudPanel title="PROFILE" corners className="max-w-xl">
      <div className="space-y-3">
        <div className="space-y-1"><Label>Email</Label><Input value={user?.email ?? ""} disabled /></div>
        <div className="space-y-1"><Label>Full name</Label><Input value={name} onChange={(e) => setName(e.target.value)} /></div>
        <Button size="sm" disabled={name.trim() === (user?.full_name ?? "")} loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
        <p className="font-mono text-[10px] text-muted-foreground">Assistant name, voice, colours and HUD style live in the ⚙ customise panel (top bar).</p>
      </div>
    </HudPanel>
  );
}

function NotificationsTab({ settings, admin }: { settings: OrgSettings; admin: boolean }) {
  const queryClient = useQueryClient();
  const [perm, setPerm] = React.useState<NotificationPermission | "unsupported">("default");
  const [enabled, setEnabled] = React.useState(false);
  React.useEffect(() => {
    if (typeof window === "undefined" || !("Notification" in window)) { setPerm("unsupported"); return; }
    setPerm(window.Notification.permission);
    setEnabled(browserNotificationsEnabled());
  }, []);
  const toggleBrowser = async () => {
    if (perm === "unsupported") return;
    if (enabled) { window.localStorage.setItem("ba.browserNotify", "off"); setEnabled(false); return; }
    const result = await window.Notification.requestPermission();
    setPerm(result);
    if (result === "granted") {
      window.localStorage.setItem("ba.browserNotify", "on");
      setEnabled(true);
      new window.Notification("JARVIS online", { body: "Browser notifications enabled.", icon: "/favicon.svg" });
    }
  };
  const orgPrefs = ((settings.organization.settings.notifications as Record<string, boolean> | undefined) ?? {});
  const [prefs, setPrefs] = React.useState<Record<string, boolean>>({ task_completed: true, task_failed: true, human_required: true, task_attention: true, session_expired: true, ...orgPrefs });
  const save = useMutation({
    mutationFn: () => api.settings.update({ settings: { notifications: prefs } }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["settings"] }); toast({ title: "Notification settings saved", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Save failed", description: e.message, variant: "destructive" }),
  });
  const channels = (settings.system.notification_channels as string[] | undefined) ?? [];
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <HudPanel title="BROWSER NOTIFICATIONS" corners>
        <p className="text-[11px] text-muted-foreground">Native desktop notifications when a task completes, fails or needs you — even when this tab is in the background.</p>
        <div className="mt-3 flex items-center gap-2">
          <Button size="sm" variant={enabled ? "outline" : "default"} onClick={toggleBrowser} disabled={perm === "unsupported" || perm === "denied"}><BellRing /> {enabled ? "Disable" : "Enable"}</Button>
          <HudPill tone={perm === "granted" ? "green" : perm === "denied" ? "red" : "dim"}>{perm === "unsupported" ? "unsupported" : `permission: ${perm}`}</HudPill>
        </div>
        {perm === "denied" ? <p className="mt-2 font-mono text-[10px] text-hud-amber">Notifications are blocked for this site in your browser settings.</p> : null}
      </HudPanel>
      <HudPanel title="CHANNELS" corners>
        <div className="flex flex-wrap gap-1.5">
          {["in_app", "browser", "email", "telegram", "webhook"].map((c) => (
            <HudPill key={c} tone={channels.includes(c) ? "green" : "dim"}>{c.replace("_", " ")} · {channels.includes(c) ? "active" : "not configured"}</HudPill>
          ))}
        </div>
        <p className="mt-2 font-mono text-[10px] text-muted-foreground">Email (SMTP_*), Telegram (TELEGRAM_BOT_TOKEN / CHAT_ID) and generic webhook (WhatsApp gateways, Slack) are enabled through server environment variables — no secrets are entered in the UI.</p>
      </HudPanel>
      <HudPanel title="EVENTS" corners className="lg:col-span-2">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {Object.keys(prefs).map((k) => (
            <label key={k} className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.12em]">
              <input type="checkbox" className="accent-primary" disabled={!admin} checked={prefs[k]} onChange={(e) => setPrefs({ ...prefs, [k]: e.target.checked })} /> {titleCase(k)}
            </label>
          ))}
        </div>
        {admin ? <Button size="sm" className="mt-3" loading={save.isPending} onClick={() => save.mutate()}>Save</Button> : <p className="mt-2 font-mono text-[10px] text-muted-foreground">Only admins can change organisation-wide preferences.</p>}
      </HudPanel>
    </div>
  );
}

function OrganisationTab({ settings }: { settings: OrgSettings }) {
  const queryClient = useQueryClient();
  const { refresh } = useAuth();
  const [name, setName] = React.useState(settings.organization.name);
  const [llm, setLlm] = React.useState({ provider: settings.llm.provider, model: settings.llm.model ?? "", base_url: settings.llm.base_url ?? "", api_key: "", clear: false });
  const [limits, setLimits] = React.useState<Record<string, string>>(Object.fromEntries(Object.keys(LIMIT_LABELS).map((k) => [k, settings.limits[k] != null ? String(settings.limits[k]) : ""])));
  const save = useMutation({
    mutationFn: () => api.settings.update({
      name: name.trim() || undefined,
      llm_provider: llm.provider,
      llm_model: llm.model,
      llm_base_url: llm.base_url,
      llm_api_key: llm.api_key || undefined,
      clear_llm_api_key: llm.clear,
      limits: Object.fromEntries(Object.entries(limits).map(([k, v]) => [k, v === "" ? null : Number(v)])),
    }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["settings"] }); refresh(); setLlm((l) => ({ ...l, api_key: "", clear: false })); toast({ title: "Organisation settings saved", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Save failed", description: e.message, variant: "destructive" }),
  });
  const sys = settings.system.system_limits as Record<string, number> | undefined;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <HudPanel title="ORGANISATION" corners>
        <div className="space-y-3">
          <div className="space-y-1"><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} /></div>
          <p className="font-mono text-[10px] text-muted-foreground">slug {settings.organization.slug} · id {settings.organization.id.slice(0, 8)}</p>
        </div>
      </HudPanel>
      <HudPanel title="AI MODEL (LLM)" corners>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>Provider</Label>
              <Select value={llm.provider} onChange={(e) => setLlm({ ...llm, provider: e.target.value })}>
                <option value="inherit">Server default ({settings.llm.effective_provider ?? "none"})</option>
                <option value="none">None (heuristic planner)</option>
                <option value="openai">OpenAI-compatible</option>
                <option value="anthropic">Anthropic</option>
              </Select>
            </div>
            <div className="space-y-1"><Label>Model</Label><Input value={llm.model} onChange={(e) => setLlm({ ...llm, model: e.target.value })} placeholder="gpt-4o-mini / claude-3-5-sonnet" /></div>
          </div>
          <div className="space-y-1"><Label>Base URL (optional, OpenAI-compatible)</Label><Input value={llm.base_url} onChange={(e) => setLlm({ ...llm, base_url: e.target.value })} placeholder="https://api.openai.com/v1" /></div>
          <div className="space-y-1">
            <Label>API key {settings.llm.has_api_key ? "· stored (encrypted)" : "· not set"}</Label>
            <div className="flex gap-1.5">
              <Input type="password" autoComplete="off" value={llm.api_key} onChange={(e) => setLlm({ ...llm, api_key: e.target.value, clear: false })} placeholder={settings.llm.has_api_key ? "•••••••• (leave blank to keep)" : "sk-…"} />
              {settings.llm.has_api_key ? <Button size="sm" variant="ghost" onClick={() => setLlm({ ...llm, api_key: "", clear: !llm.clear })} title="Remove stored key"><Trash2 />{llm.clear ? " will clear" : ""}</Button> : null}
            </div>
          </div>
          <p className="flex items-center gap-1 font-mono text-[10px] text-muted-foreground"><KeyRound className="h-3 w-3" /> Keys are encrypted at rest and never returned to the browser. Active: {settings.system.llm_active ? `yes (${String(settings.system.llm_model ?? "")})` : "no — heuristic planner"}.</p>
        </div>
      </HudPanel>
      <HudPanel title="COST & SAFETY LIMITS" corners className="lg:col-span-2">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(LIMIT_LABELS).map(([k, meta]) => (
            <div key={k} className="space-y-1">
              <Label>{meta.label}{meta.unit ? ` (${meta.unit})` : ""}</Label>
              <Input type="number" value={limits[k]} onChange={(e) => setLimits({ ...limits, [k]: e.target.value })} placeholder={`default ${settings.effective_limits[k] ?? ""}`} />
            </div>
          ))}
        </div>
        <p className="mt-2 font-mono text-[10px] text-muted-foreground">
          Blank = server default. Hard server ceilings: task {sys ? formatDuration(sys.max_task_duration_seconds) : "—"}, retries {sys?.max_retries_limit ?? "—"}, scenes {sys?.max_scenes_per_task ?? "—"}, sessions {sys?.max_browser_sessions_per_org ?? "—"}, file {sys?.max_generated_file_size_mb ?? "—"} MB, {sys?.max_concurrent_tasks_per_worker ?? "—"} tasks/worker.
        </p>
        <Button size="sm" className="mt-3" loading={save.isPending} onClick={() => save.mutate()}>Save organisation settings</Button>
      </HudPanel>
    </div>
  );
}

function MembersTab() {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const members = useQuery({ queryKey: ["members"], queryFn: api.settings.members.list });
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState("member");
  const err = (title: string) => (e: Error) => toast({ title, description: e.message, variant: "destructive" });
  const add = useMutation({ mutationFn: () => api.settings.members.add(email.trim(), role), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["members"] }); setEmail(""); toast({ title: "Member added", variant: "success" }); }, onError: err("Could not add member") });
  const update = useMutation({ mutationFn: ({ id, role }: { id: string; role: string }) => api.settings.members.update(id, role), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["members"] }), onError: err("Update failed") });
  const remove = useMutation({ mutationFn: (id: string) => api.settings.members.remove(id), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["members"] }), onError: err("Remove failed") });
  return (
    <HudPanel title="MEMBERS" corners padded={false}>
      <div className="flex flex-wrap items-end gap-2 border-b border-border p-3">
        <div className="min-w-[240px] flex-1 space-y-1"><Label>Email of a registered user</Label><Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="colleague@example.com" /></div>
        <div className="space-y-1"><Label>Role</Label><Select value={role} onChange={(e) => setRole(e.target.value)}><option value="viewer">viewer</option><option value="member">member</option><option value="admin">admin</option></Select></div>
        <Button size="sm" disabled={!email.trim()} loading={add.isPending} onClick={() => add.mutate()}>Add</Button>
      </div>
      {members.isLoading ? <Skeleton className="m-3 h-20" /> : (
        <table className="w-full font-mono text-[11px]">
          <thead><tr className="hud-label border-b border-border text-left"><th className="px-3 py-2">User</th><th className="px-2 py-2">Role</th><th className="px-2 py-2" /></tr></thead>
          <tbody className="divide-y divide-border">
            {(members.data ?? []).map((m) => (
              <tr key={m.id}>
                <td className="px-3 py-1.5"><span className="font-bold">{m.full_name || m.email}</span> <span className="text-muted-foreground">{m.email}</span>{m.user_id === user?.id ? <span className="hud-pill ml-2">you</span> : null}</td>
                <td className="px-2 py-1.5">
                  {m.role === "owner" ? <HudPill tone="green">owner</HudPill> : (
                    <Select value={m.role} className="h-7 w-28" disabled={m.user_id === user?.id} onChange={(e) => update.mutate({ id: m.id, role: e.target.value })}><option value="viewer">viewer</option><option value="member">member</option><option value="admin">admin</option></Select>
                  )}
                </td>
                <td className="px-2 py-1.5 text-right">{m.role !== "owner" && m.user_id !== user?.id ? <Button size="icon-sm" variant="ghost" onClick={() => remove.mutate(m.id)}><Trash2 /></Button> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="p-3 font-mono text-[10px] text-muted-foreground">viewer = read-only · member = run tasks & edit projects · admin = settings, platforms, members.</p>
    </HudPanel>
  );
}

function SystemTab({ settings }: { settings: OrgSettings }) {
  const metrics = useQuery({ queryKey: ["system-metrics"], queryFn: api.assistant.system, refetchInterval: 5000 });
  const sys = settings.system;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <HudPanel title="RUNTIME" corners>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-[11px]">
          <dt className="text-muted-foreground">Environment</dt><dd>{String(sys.environment)}</dd>
          <dt className="text-muted-foreground">Event bus</dt><dd>{String(sys.bus)}</dd>
          <dt className="text-muted-foreground">Worker</dt><dd>{sys.embedded_worker ? "embedded in API (dev)" : "separate worker process(es)"}</dd>
          <dt className="text-muted-foreground">LLM</dt><dd>{sys.llm_active ? String(sys.llm_model ?? "active") : "heuristic planner"}</dd>
          {sys.mock_platform_url ? <><dt className="text-muted-foreground">Mock platform</dt><dd><a className="text-primary hover:underline" href={String(sys.mock_platform_url)} target="_blank" rel="noreferrer">{String(sys.mock_platform_url)}</a></dd></> : null}
        </dl>
      </HudPanel>
      <HudPanel title="HOST METRICS" corners>
        {metrics.data ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-[11px]">
            {Object.entries(metrics.data).filter(([, v]) => typeof v !== "object").map(([k, v]) => (
              <React.Fragment key={k}><dt className="text-muted-foreground">{k}</dt><dd>{String(v)}</dd></React.Fragment>
            ))}
          </dl>
        ) : <Skeleton className="h-24" />}
      </HudPanel>
    </div>
  );
}

function SettingsView() {
  const params = useSearchParams();
  const role = useAppStore((s) => s.auth?.role);
  const assistant = useJarvis((s) => s.prefs.assistantName);
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings.get });
  const [tab, setTab] = React.useState(params.get("tab") ?? "profile");
  const admin = isAdmin(role);
  if (settings.isLoading) return <div className="p-4"><Skeleton className="h-64" /></div>;
  if (settings.isError || !settings.data) return <div className="p-6"><ErrorState error={settings.error} retry={() => settings.refetch()} /></div>;
  return (
    <div className="space-y-4 p-4">
      <PageHeader eyebrow="Configuration" title="Settings" description={`Profile, organisation, AI model, limits, notifications, members and ${assistant}'s memory.`} />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="flex-wrap">
          <TabsTrigger value="profile">Profile</TabsTrigger>
          <TabsTrigger value="notifications">Notifications</TabsTrigger>
          <TabsTrigger value="memory">Memory</TabsTrigger>
          {admin ? <TabsTrigger value="organisation">Organisation & AI</TabsTrigger> : null}
          {admin ? <TabsTrigger value="members">Members</TabsTrigger> : null}
          <TabsTrigger value="system">System</TabsTrigger>
        </TabsList>
        <TabsContent value="profile" className="mt-3"><ProfileTab /></TabsContent>
        <TabsContent value="notifications" className="mt-3"><NotificationsTab settings={settings.data} admin={admin} /></TabsContent>
        <TabsContent value="memory" className="mt-3"><MemoryPanel className="max-w-3xl" /></TabsContent>
        {admin ? <TabsContent value="organisation" className="mt-3"><OrganisationTab settings={settings.data} /></TabsContent> : null}
        {admin ? <TabsContent value="members" className="mt-3"><MembersTab /></TabsContent> : null}
        <TabsContent value="system" className="mt-3"><SystemTab settings={settings.data} /></TabsContent>
      </Tabs>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <React.Suspense fallback={null}>
      <SettingsView />
    </React.Suspense>
  );
}
