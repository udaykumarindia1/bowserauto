"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Check, QrCode, Smartphone, Trash2, X } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";

import { LLMSettingsPanel } from "@/components/settings/llm-settings-panel";
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
  max_file_size_mb: { label: "Max generated file size", unit: "MiB" },
  max_storage_mb: { label: "Organisation storage cap", unit: "MiB" },
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
        <p className="font-sans text-[10px] text-muted-foreground">Assistant name, voice, colours and HUD style live in the ⚙ customise panel (top bar).</p>
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
      new window.Notification(`${useJarvis.getState().prefs.assistantName} online`, { body: "Browser notifications enabled.", icon: "/favicon.svg" });
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
        {perm === "denied" ? <p className="mt-2 font-sans text-[10px] text-hud-amber">Notifications are blocked for this site in your browser settings.</p> : null}
      </HudPanel>
      <HudPanel title="CHANNELS" corners>
        <div className="flex flex-wrap gap-1.5">
          {["in_app", "browser", "email", "telegram", "webhook"].map((c) => (
            <HudPill key={c} tone={channels.includes(c) ? "green" : "dim"}>{c.replace("_", " ")} · {channels.includes(c) ? "active" : "not configured"}</HudPill>
          ))}
        </div>
        <p className="mt-2 font-sans text-[10px] text-muted-foreground">Email (SMTP_*), Telegram (TELEGRAM_BOT_TOKEN / CHAT_ID) and generic webhook (WhatsApp gateways, Slack) are enabled through server environment variables — no secrets are entered in the UI.</p>
      </HudPanel>
      <HudPanel title="EVENTS" corners className="lg:col-span-2">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {Object.keys(prefs).map((k) => (
            <label key={k} className="flex items-center gap-2 font-sans text-[10px] uppercase tracking-[0.12em]">
              <input type="checkbox" className="accent-primary" disabled={!admin} checked={prefs[k]} onChange={(e) => setPrefs({ ...prefs, [k]: e.target.checked })} /> {titleCase(k)}
            </label>
          ))}
        </div>
        {admin ? <Button size="sm" className="mt-3" loading={save.isPending} onClick={() => save.mutate()}>Save</Button> : <p className="mt-2 font-sans text-[10px] text-muted-foreground">Only admins can change organisation-wide preferences.</p>}
      </HudPanel>
    </div>
  );
}

function OrganisationTab({ settings }: { settings: OrgSettings }) {
  const queryClient = useQueryClient();
  const { refresh } = useAuth();
  const [name, setName] = React.useState(settings.organization.name);
  const [limits, setLimits] = React.useState<Record<string, string>>(Object.fromEntries(Object.keys(LIMIT_LABELS).map((k) => [k, settings.limits[k] != null ? String(settings.limits[k]) : ""])));
  const save = useMutation({
    mutationFn: () => api.settings.update({
      name: name.trim() || undefined,
      limits: Object.fromEntries(Object.entries(limits).map(([k, v]) => [k, v === "" ? null : Number(v)])),
    }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["settings"] }); refresh(); toast({ title: "Organisation settings saved", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Save failed", description: e.message, variant: "destructive" }),
  });
  const sys = settings.system.system_limits as Record<string, number> | undefined;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <HudPanel title="ORGANISATION" corners>
        <div className="space-y-3">
          <div className="space-y-1"><Label>Name</Label><Input value={name} onChange={(e) => setName(e.target.value)} /></div>
          <p className="font-sans text-[10px] text-muted-foreground">slug {settings.organization.slug} · id {settings.organization.id.slice(0, 8)}</p>
        </div>
      </HudPanel>
      <LLMSettingsPanel settings={settings} />
      <HudPanel title="COST & SAFETY LIMITS" corners className="lg:col-span-2">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(LIMIT_LABELS).map(([k, meta]) => (
            <div key={k} className="space-y-1">
              <Label>{meta.label}{meta.unit ? ` (${meta.unit})` : ""}</Label>
              <Input type="number" value={limits[k]} onChange={(e) => setLimits({ ...limits, [k]: e.target.value })} placeholder={`default ${settings.effective_limits[k] ?? ""}`} />
            </div>
          ))}
        </div>
        <p className="mt-2 font-sans text-[10px] text-muted-foreground">
          Blank = server default. Organisation storage and file caps are in MiB and can only be tightened. Hard server ceilings: task {sys ? formatDuration(sys.max_task_duration_seconds) : "—"}, retries {sys?.max_retries_limit ?? "—"}, scenes {sys?.max_scenes_per_task ?? "—"}, sessions {sys?.max_browser_sessions_per_org ?? "—"}, file {sys?.max_generated_file_size_mb ?? "—"} MiB, storage {sys?.max_storage_per_org_mb ?? "—"} MiB, {sys?.max_concurrent_tasks_per_worker ?? "—"} tasks/worker.
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
        <table className="w-full font-sans text-[11px]">
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
      <p className="p-3 font-sans text-[10px] text-muted-foreground">viewer = read-only · member = run tasks & edit projects · admin = settings, platforms, members.</p>
    </HudPanel>
  );
}

function DevicesTab() {
  const queryClient = useQueryClient();
  const [pairing, setPairing] = React.useState<{ id: string; code: string; expires_at: string } | null>(null);
  const [notice, setNotice] = React.useState("");
  const pairingStatus = useQuery({
    queryKey: ["device-pairing", pairing?.id],
    queryFn: () => api.auth.pairings.get(pairing!.id),
    enabled: !!pairing,
    refetchInterval: (query) => {
      const value = query.state.data?.status;
      return value && ["pending", "claimed", "approved"].includes(value) ? 1800 : false;
    },
  });
  const sessions = useQuery({ queryKey: ["login-sessions"], queryFn: api.auth.loginSessions });
  const create = useMutation({
    mutationFn: api.auth.pairings.create,
    onSuccess: (result) => { setPairing(result); setNotice(""); },
    onError: (error: Error) => toast({ title: "Could not start phone pairing", description: error.message, variant: "destructive" }),
  });
  const approve = useMutation({
    mutationFn: () => api.auth.pairings.approve(pairing!.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["device-pairing", pairing?.id] }),
    onError: (error: Error) => toast({ title: "Approval failed", description: error.message, variant: "destructive" }),
  });
  const reject = useMutation({
    mutationFn: () => api.auth.pairings.reject(pairing!.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["device-pairing", pairing?.id] }),
    onError: (error: Error) => toast({ title: "Could not reject pairing", description: error.message, variant: "destructive" }),
  });
  const revoke = useMutation({
    mutationFn: api.auth.revokeLoginSession,
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["login-sessions"] }); toast({ title: "Device session revoked", variant: "success" }); },
    onError: (error: Error) => toast({ title: "Could not revoke session", description: error.message, variant: "destructive" }),
  });

  const state = pairingStatus.data?.status ?? (pairing ? "pending" : "");
  const expiry = pairingStatus.data?.expires_at ?? pairing?.expires_at ?? "";
  const qrValue = pairing?.code && state === "pending" && typeof window !== "undefined"
    ? `${window.location.origin}/pair#${new URLSearchParams({ id: pairing.id, code: pairing.code })}`
    : "";
  React.useEffect(() => {
    if (state === "completed" && pairing?.code) {
      setPairing({ ...pairing, code: "" });
      setNotice("Phone paired. Its app session is separate and can be revoked below.");
      queryClient.invalidateQueries({ queryKey: ["login-sessions"] });
    }
  }, [state, pairing, queryClient]);

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <HudPanel title="PAIR A PHONE" corners className="lg:col-span-2">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          <div className="min-w-0 flex-1 space-y-3">
            <p className="font-sans text-xs leading-relaxed text-muted-foreground">
              Scan this short-lived QR code with the phone you want to use. The phone sends a pairing request; access is not granted until you approve it here. Pairing stays on this app's origin and uses a separate revocable HttpOnly login session.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" loading={create.isPending} onClick={() => create.mutate()}><QrCode /> Generate pairing QR</Button>
              {pairing ? <HudPill tone={state === "completed" ? "green" : state === "claimed" ? "amber" : state === "rejected" || state === "expired" ? "red" : "dim"}>{state || "pending"}</HudPill> : null}
            </div>
            {pairing ? (
              <div className="space-y-2 font-sans text-[11px] text-muted-foreground">
                <p>Request expires at {expiry ? new Date(expiry).toLocaleTimeString() : "—"}. Pairing codes expire after two minutes and can be used once.</p>
                {state === "claimed" ? (
                  <div className="space-y-2 rounded-lg border border-hud-amber/25 bg-hud-amber/5 p-3">
                    <p className="font-semibold text-foreground">{pairingStatus.data?.device_name || "Phone browser"} is asking to pair.</p>
                    <p className="break-all">{pairingStatus.data?.user_agent || "Device details unavailable"}</p>
                    <p>Approve only if you initiated this scan on your own device.</p>
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" loading={approve.isPending} onClick={() => approve.mutate()}><Check /> Approve phone</Button>
                      <Button size="sm" variant="outline" loading={reject.isPending} onClick={() => reject.mutate()}><X /> Reject</Button>
                    </div>
                  </div>
                ) : null}
                {state === "approved" ? <p className="text-hud-green">Approved. Waiting for the phone to finish its secure sign-in.</p> : null}
                {state === "completed" ? <p className="text-hud-green">Phone paired successfully. Manage or revoke its login session below.</p> : null}
                {state === "rejected" ? <p className="text-hud-red">Pairing was rejected. Generate a new QR to try again.</p> : null}
                {state === "expired" ? <p className="text-hud-red">This QR expired. Generate a new one to try again.</p> : null}
                {pairingStatus.isError ? <ErrorState error={pairingStatus.error} retry={() => pairingStatus.refetch()} /> : null}
              </div>
            ) : null}
            {notice ? <p className="font-sans text-[11px] text-hud-green">{notice}</p> : null}
          </div>
          {qrValue ? (
            <div className="shrink-0 rounded-xl border border-border bg-white p-3" aria-label="Phone pairing QR code">
              <QRCodeSVG value={qrValue} size={190} level="M" title="Scan to request phone pairing" />
            </div>
          ) : pairingStatus.isLoading && pairing ? <div className="h-[214px] w-[214px] animate-pulse rounded-xl bg-muted" /> : null}
        </div>
        <p className="mt-3 flex items-start gap-2 border-t border-border/70 pt-3 font-sans text-[10px] leading-relaxed text-muted-foreground"><Smartphone className="mt-0.5 h-3.5 w-3.5 shrink-0" />Never approve a request you did not initiate. Revoke a paired phone from the active sessions list if it is lost. The QR grants no access until you approve it.</p>
      </HudPanel>

      <HudPanel title="ACTIVE APP SESSIONS" corners className="lg:col-span-2">
        {sessions.isLoading ? <Skeleton className="h-24" /> : sessions.isError ? <ErrorState error={sessions.error} retry={() => sessions.refetch()} /> : sessions.data?.length ? (
          <div className="divide-y divide-border/70">
            {sessions.data.map((session) => (
              <div key={session.id} className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0">
                <div className="min-w-0 flex-1">
                  <p className="break-all font-sans text-[11px] font-medium">{session.user_agent || "Unknown browser"}{session.current ? <span className="ml-2 text-primary">· this device</span> : null}</p>
                  <p className="mt-0.5 font-sans text-[10px] text-muted-foreground">Created {new Date(session.created_at).toLocaleString()} · Last active {session.last_seen_at ? new Date(session.last_seen_at).toLocaleString() : "unknown"}</p>
                </div>
                <Button size="sm" variant="outline" disabled={session.current || revoke.isPending} onClick={() => {
                  if (window.confirm("Revoke this app session? That device will need to pair or sign in again.")) revoke.mutate(session.id);
                }}><Trash2 /> Revoke</Button>
              </div>
            ))}
          </div>
        ) : <p className="font-sans text-xs text-muted-foreground">No active sessions.</p>}
      </HudPanel>
    </div>
  );
}

function SystemTab({ settings }: { settings: OrgSettings }) {
  const metrics = useQuery({ queryKey: ["system-metrics"], queryFn: api.assistant.system, refetchInterval: 5000 });
  const sys = settings.system;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <HudPanel title="RUNTIME" corners>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-sans text-[11px]">
          <dt className="text-muted-foreground">Environment</dt><dd>{String(sys.environment)}</dd>
          <dt className="text-muted-foreground">Event bus</dt><dd>{String(sys.bus)}</dd>
          <dt className="text-muted-foreground">Worker</dt><dd>{sys.embedded_worker ? "embedded in API (dev)" : "separate worker process(es)"}</dd>
          <dt className="text-muted-foreground">LLM</dt><dd>{sys.llm_active ? String(sys.llm_model ?? "active") : "heuristic planner"}</dd>
          {sys.mock_platform_url ? <><dt className="text-muted-foreground">Mock platform</dt><dd><a className="text-primary hover:underline" href={String(sys.mock_platform_url)} target="_blank" rel="noreferrer">{String(sys.mock_platform_url)}</a></dd></> : null}
        </dl>
      </HudPanel>
      <HudPanel title="HOST METRICS" corners>
        {metrics.data ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-sans text-[11px]">
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
          <TabsTrigger value="devices">Devices</TabsTrigger>
          <TabsTrigger value="memory">Memory</TabsTrigger>
          {admin ? <TabsTrigger value="organisation">Organisation & AI</TabsTrigger> : null}
          {admin ? <TabsTrigger value="members">Members</TabsTrigger> : null}
          <TabsTrigger value="system">System</TabsTrigger>
        </TabsList>
        <TabsContent value="profile" className="mt-3"><ProfileTab /></TabsContent>
        <TabsContent value="notifications" className="mt-3"><NotificationsTab settings={settings.data} admin={admin} /></TabsContent>
        <TabsContent value="devices" className="mt-3"><DevicesTab /></TabsContent>
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
