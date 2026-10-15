"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Camera, LogIn, LogOut, Power, PowerOff, Trash2 } from "lucide-react";

import { LiveViewer } from "@/components/browser/live-viewer";
import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudPanel, HudPill } from "@/components/hud/hud-primitives";
import { TaskRow } from "@/components/tasks/task-card";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/misc";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { canWrite, toast, useAppStore } from "@/lib/store";
import { formatDateTime, formatTime, timeAgo, truncate } from "@/lib/utils";

export default function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const role = useAppStore((s) => s.auth?.role);
  const session = useQuery({ queryKey: [`session:${id}`], queryFn: () => api.sessions.get(id), refetchInterval: 6000 });
  const platform = useQuery({ queryKey: ["platform", session.data?.platform_key], queryFn: () => api.platforms.get(session.data!.platform_key), enabled: !!session.data });
  const tasks = useQuery({ queryKey: ["tasks", "session", id], queryFn: () => api.tasks.list({ status: "active", limit: 20 }), refetchInterval: 10000 });
  const shots = useQuery({ queryKey: [`session-shots:${id}`], queryFn: () => api.sessions.screenshots(id), refetchInterval: 20000 });
  const [confirm, setConfirm] = React.useState<"delete" | "logout" | null>(null);
  const [name, setName] = React.useState("");
  React.useEffect(() => { if (session.data) setName(session.data.name); }, [session.data]);

  const invalidate = () => { queryClient.invalidateQueries({ queryKey: [`session:${id}`] }); queryClient.invalidateQueries({ queryKey: ["sessions"] }); };
  const err = (title: string) => (e: Error) => toast({ title, description: e.message, variant: "destructive" });
  const open = useMutation({ mutationFn: () => api.sessions.open(id), onSuccess: invalidate, onError: err("Launch failed") });
  const close = useMutation({ mutationFn: () => api.sessions.close(id), onSuccess: invalidate, onError: err("Close failed") });
  const takeover = useMutation({ mutationFn: () => api.sessions.takeover(id), onSuccess: invalidate, onError: err("Takeover failed") });
  const release = useMutation({ mutationFn: () => api.sessions.release(id), onSuccess: invalidate, onError: err("Release failed") });
  const markLogin = useMutation({ mutationFn: (logged_in: boolean) => api.sessions.markLogin(id, logged_in), onSuccess: () => { invalidate(); toast({ title: "Login state updated", variant: "success" }); }, onError: err("Update failed") });
  const logout = useMutation({ mutationFn: () => api.sessions.logout(id), onSuccess: () => { invalidate(); setConfirm(null); toast({ title: "Session cookies cleared", variant: "success" }); }, onError: err("Logout failed") });
  const rename = useMutation({ mutationFn: () => api.sessions.update(id, { name }), onSuccess: () => { invalidate(); toast({ title: "Renamed", variant: "success" }); }, onError: err("Rename failed") });
  const remove = useMutation({ mutationFn: () => api.sessions.remove(id), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["sessions"] }); router.push("/sessions"); }, onError: err("Delete failed") });

  if (session.isLoading) return <div className="space-y-3 p-4"><Skeleton className="h-10" /><Skeleton className="h-[520px]" /></div>;
  if (session.isError || !session.data) return <div className="p-6"><ErrorState error={session.error} retry={() => session.refetch()} /></div>;
  const s = session.data;
  const live = s.status === "ready" || s.status === "in_use";
  const writable = canWrite(role);
  const lockedTask = (tasks.data?.items ?? []).find((t) => t.browser_session_id === s.id);

  return (
    <div className="space-y-3 p-3">
      <ConfirmBanner open={confirm === "delete"} title="DELETE SESSION" detail="The browser profile (cookies, local storage, logins) is wiped from disk." tone="danger" busy={remove.isPending} onConfirm={() => remove.mutate()} onCancel={() => setConfirm(null)} />
      <ConfirmBanner open={confirm === "logout"} title="CLEAR LOGIN" detail="Cookies and storage for this profile are deleted. You will need to log in again by hand." busy={logout.isPending} onConfirm={() => logout.mutate()} onCancel={() => setConfirm(null)} />
      <div className="flex flex-wrap items-center gap-2">
        <Link href="/sessions" className="hud-label flex items-center gap-1 hover:text-primary"><ArrowLeft className="h-3 w-3" /> Sessions</Link>
        <span className="text-border">/</span>
        <h1 className="min-w-0 flex-1 truncate font-mono text-sm font-bold uppercase tracking-[0.15em] text-primary">{s.name}</h1>
        <HudPill tone="dim">{s.platform_key}</HudPill>
        <HudPill tone={s.login_status === "logged_in" ? "green" : s.login_status === "logged_out" ? "amber" : "dim"}>{s.login_status.replace("_", " ")}</HudPill>
        <StatusBadge status={s.status} />
        {writable ? (
          live || s.status === "launching" ? (
            <Button size="sm" variant="outline" loading={close.isPending} onClick={() => close.mutate()} disabled={!!s.locked_by_task_id}><PowerOff /> Close browser</Button>
          ) : (
            <Button size="sm" loading={open.isPending} onClick={() => open.mutate()}><Power /> Launch browser</Button>
          )
        ) : null}
      </div>

      {lockedTask ? <TaskRow task={lockedTask} /> : null}

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="h-[600px]">
          <LiveViewer
            session={s}
            className="h-full"
            canControl={writable && !s.locked_by_task_id}
            humanControl={s.interactive}
            takeoverPending={takeover.isPending || release.isPending}
            onTakeover={() => takeover.mutateAsync()}
            onRelease={() => release.mutateAsync()}
          />
        </div>
        <div className="space-y-3">
          <HudPanel title="LOGIN" corners>
            <p className="text-[11px] text-muted-foreground">
              {platform.data?.login_hint || "Take over the browser, sign in to the platform yourself (including any CAPTCHA or 2-step verification), then mark the session as logged in."}
            </p>
            {writable ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Button size="sm" variant="success" loading={markLogin.isPending} onClick={() => markLogin.mutate(true)} disabled={s.login_status === "logged_in"}><LogIn /> Mark logged in</Button>
                <Button size="sm" variant="ghost" loading={markLogin.isPending} onClick={() => markLogin.mutate(false)} disabled={s.login_status === "logged_out"}>Mark logged out</Button>
                <Button size="sm" variant="outline" onClick={() => setConfirm("logout")}><LogOut /> Clear cookies</Button>
              </div>
            ) : null}
          </HudPanel>

          <HudPanel title="DETAILS">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[10px]">
              <dt className="text-muted-foreground">Browser</dt><dd>{s.browser_type}</dd>
              <dt className="text-muted-foreground">Worker</dt><dd>{s.worker_id ? s.worker_id.slice(0, 12) : "—"}</dd>
              <dt className="text-muted-foreground">Heartbeat</dt><dd>{s.last_heartbeat_at ? timeAgo(s.last_heartbeat_at) : "—"}</dd>
              <dt className="text-muted-foreground">Last used</dt><dd>{s.last_used_at ? timeAgo(s.last_used_at) : "—"}</dd>
              <dt className="text-muted-foreground">Expires</dt><dd>{s.expires_at ? formatDateTime(s.expires_at) : "never"}</dd>
              <dt className="text-muted-foreground">Page</dt><dd className="truncate" title={s.current_url}>{s.current_title || truncate(s.current_url, 40) || "—"}</dd>
              {s.error ? <><dt className="text-muted-foreground">Error</dt><dd className="text-hud-red">{s.error}</dd></> : null}
            </dl>
            {writable ? (
              <div className="mt-3 space-y-1">
                <Label>Rename</Label>
                <div className="flex gap-1.5">
                  <Input value={name} onChange={(e) => setName(e.target.value)} />
                  <Button size="sm" variant="outline" disabled={name === s.name || !name.trim()} loading={rename.isPending} onClick={() => rename.mutate()}>Save</Button>
                </div>
                <Button size="sm" variant="ghost" className="mt-2 text-hud-red" disabled={!!s.locked_by_task_id} onClick={() => setConfirm("delete")}><Trash2 /> Delete session</Button>
              </div>
            ) : null}
          </HudPanel>

          <HudPanel title="SCREENSHOTS" padded={false}>
            {(shots.data?.length ?? 0) === 0 ? (
              <div className="p-3"><EmptyState icon={Camera} title="None yet" description="Screenshots taken by tasks using this session appear here." className="py-3" /></div>
            ) : (
              <div className="grid max-h-80 grid-cols-2 gap-1.5 overflow-y-auto p-2">
                {shots.data!.slice(0, 20).map((sh) => (
                  <a key={sh.id} href={sh.image.url} target="_blank" rel="noreferrer" className="border border-border hover:border-primary">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={sh.image.url} alt={sh.stage} loading="lazy" className="aspect-video w-full bg-black object-cover object-top" />
                    <p className="truncate px-1 py-0.5 font-mono text-[9px] text-muted-foreground">{formatTime(sh.created_at)} · {sh.stage}</p>
                  </a>
                ))}
              </div>
            )}
          </HudPanel>
        </div>
      </div>
    </div>
  );
}
