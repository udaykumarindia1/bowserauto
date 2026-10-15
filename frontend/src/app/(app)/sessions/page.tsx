"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, LogIn, Plus, Power, PowerOff, Trash2 } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudPanel } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { canWrite, toast, useAppStore } from "@/lib/store";
import type { BrowserSession } from "@/lib/types";
import { cn, timeAgo, truncate } from "@/lib/utils";

function NewSessionDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const platforms = useQuery({ queryKey: ["platforms"], queryFn: api.platforms.list, enabled: open });
  const [form, setForm] = React.useState({ name: "", platform_key: "generic", start_url: "", open: true });
  const platform = platforms.data?.find((p) => p.key === form.platform_key);
  const create = useMutation({
    mutationFn: async () => {
      const s = await api.sessions.create({ name: form.name.trim() || `${platform?.name ?? form.platform_key} session`, platform_key: form.platform_key, start_url: form.start_url || undefined });
      if (form.open) await api.sessions.open(s.id);
      return s;
    },
    onSuccess: (s) => { queryClient.invalidateQueries({ queryKey: ["sessions"] }); onOpenChange(false); router.push(`/sessions/${s.id}`); },
    onError: (e: Error) => toast({ title: "Could not create session", description: e.message, variant: "destructive" }),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>New browser session</DialogTitle>
        <DialogDescription>A persistent Chromium profile. Log in once by hand; cookies stay encrypted on the worker and are never sent to this UI.</DialogDescription>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>Platform</Label>
            <Select value={form.platform_key} onChange={(e) => setForm({ ...form, platform_key: e.target.value })}>
              {(platforms.data ?? []).filter((p) => p.enabled).map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
            </Select>
            {platform?.login_hint ? <p className="text-[10px] text-muted-foreground">{platform.login_hint}</p> : null}
          </div>
          <div className="space-y-1"><Label>Name</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={`${platform?.name ?? "Browser"} session`} /></div>
          <div className="space-y-1"><Label>Start URL (optional)</Label><Input value={form.start_url} onChange={(e) => setForm({ ...form, start_url: e.target.value })} placeholder={platform?.start_url || platform?.base_url || "https://"} /></div>
          <label className="flex items-center gap-2 font-sans text-[10px] uppercase tracking-[0.12em]"><input type="checkbox" className="accent-primary" checked={form.open} onChange={(e) => setForm({ ...form, open: e.target.checked })} /> Launch the browser now</label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button loading={create.isPending} onClick={() => create.mutate()}>Create</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function SessionsPage() {
  const role = useAppStore((s) => s.auth?.role);
  const queryClient = useQueryClient();
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions.list, refetchInterval: 8000 });
  const [open, setOpen] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState<BrowserSession | null>(null);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["sessions"] });
  const openSession = useMutation({ mutationFn: (id: string) => api.sessions.open(id), onSuccess: invalidate, onError: (e: Error) => toast({ title: "Launch failed", description: e.message, variant: "destructive" }) });
  const closeSession = useMutation({ mutationFn: (id: string) => api.sessions.close(id), onSuccess: invalidate, onError: (e: Error) => toast({ title: "Close failed", description: e.message, variant: "destructive" }) });
  const remove = useMutation({ mutationFn: (id: string) => api.sessions.remove(id), onSuccess: () => { invalidate(); setConfirmDelete(null); toast({ title: "Session deleted", variant: "success" }); }, onError: (e: Error) => toast({ title: "Delete failed", description: e.message, variant: "destructive" }) });
  const writable = canWrite(role);
  return (
    <div className="space-y-4 p-4">
      <PageHeader eyebrow="Persistent profiles" title="Browser sessions" description="Each session is an isolated Chromium profile the agent reuses. Log in manually once — nothing asks for your passwords." actions={writable ? <Button size="sm" onClick={() => setOpen(true)}><Plus /> New session</Button> : null} />
      <NewSessionDialog open={open} onOpenChange={setOpen} />
      <ConfirmBanner open={!!confirmDelete} title="DELETE SESSION" detail={`Delete “${confirmDelete?.name ?? ""}” and wipe its browser profile (cookies, logins).`} tone="danger" busy={remove.isPending} onConfirm={() => confirmDelete && remove.mutate(confirmDelete.id)} onCancel={() => setConfirmDelete(null)} />
      {sessions.isLoading ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3"><Skeleton className="h-32" /><Skeleton className="h-32" /></div>
      ) : sessions.isError ? (
        <ErrorState error={sessions.error} retry={() => sessions.refetch()} />
      ) : (sessions.data?.length ?? 0) === 0 ? (
        <HudPanel><EmptyState icon={Globe} title="No browser sessions" description="Create one per platform (Google Flow, Runway, ChatGPT…) and log in by hand." action={writable ? <Button size="sm" onClick={() => setOpen(true)}>New session</Button> : undefined} /></HudPanel>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {sessions.data!.map((s) => {
            const live = s.status === "ready" || s.status === "in_use";
            return (
              <div key={s.id} className={cn("hud-panel hud-corners flex flex-col gap-2 p-3", s.status === "error" && "border-hud-red/50")}>
                <div className="flex items-start gap-2">
                  <span className={cn("hud-dot mt-1.5", live ? "bg-hud-green text-hud-green" : s.status === "launching" ? "bg-hud-amber text-hud-amber animate-blink" : s.status === "error" ? "bg-hud-red text-hud-red" : "bg-muted-foreground text-muted-foreground")} />
                  <div className="min-w-0 flex-1">
                    <Link href={`/sessions/${s.id}`} className="block truncate font-sans text-xs font-bold uppercase tracking-[0.1em] hover:text-primary">{s.name}</Link>
                    <p className="hud-label">{s.platform_key} · {s.browser_type}</p>
                  </div>
                  <StatusBadge status={s.status} />
                </div>
                <div className="font-sans text-[10px] text-muted-foreground">
                  <p className="flex items-center gap-1"><LogIn className="h-3 w-3" /> {s.login_status.replace("_", " ")}{s.interactive ? " · human control" : ""}{s.locked_by_task_id ? " · locked by task" : ""}</p>
                  <p className="truncate" title={s.current_url}>{s.current_url ? truncate(s.current_url, 48) : s.start_url ? `start: ${truncate(s.start_url, 40)}` : "no page open"}</p>
                  <p>{s.last_used_at ? `used ${timeAgo(s.last_used_at)}` : `created ${timeAgo(s.created_at)}`}{s.error ? ` · ⚠ ${truncate(s.error, 40)}` : ""}</p>
                </div>
                {writable ? (
                  <div className="mt-auto flex items-center gap-1.5">
                    {live || s.status === "launching" ? (
                      <Button size="sm" variant="outline" loading={closeSession.isPending && closeSession.variables === s.id} onClick={() => closeSession.mutate(s.id)}><PowerOff /> Close</Button>
                    ) : (
                      <Button size="sm" loading={openSession.isPending && openSession.variables === s.id} onClick={() => openSession.mutate(s.id)}><Power /> Launch</Button>
                    )}
                    <Link href={`/sessions/${s.id}`}><Button size="sm" variant="ghost">Open viewer</Button></Link>
                    <span className="flex-1" />
                    <Button size="icon-sm" variant="ghost" disabled={!!s.locked_by_task_id} onClick={() => setConfirmDelete(s)} title="Delete"><Trash2 /></Button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
