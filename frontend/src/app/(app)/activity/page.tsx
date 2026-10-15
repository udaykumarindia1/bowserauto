"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, ScrollText } from "lucide-react";

import { HudPanel } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { isAdmin, useAppStore } from "@/lib/store";
import type { ActivityEntry } from "@/lib/types";
import { cn, formatDateTime, formatDuration, formatTime } from "@/lib/utils";

const LEVEL_COLOR: Record<string, string> = { error: "text-hud-red", warning: "text-hud-amber", success: "text-hud-green", debug: "text-muted-foreground", info: "text-foreground" };

function ActivityStream() {
  const queryClient = useQueryClient();
  const [level, setLevel] = React.useState("");
  const [eventType, setEventType] = React.useState("");
  const [search, setSearch] = React.useState("");
  const [paused, setPaused] = React.useState(false);
  const [expanded, setExpanded] = React.useState<string | null>(null);
  const activity = useQuery({ queryKey: ["activity", level, eventType], queryFn: () => api.activity({ limit: 300, level: level || undefined, event_type: eventType || undefined }), refetchInterval: paused ? false : 5000 });
  const items = (activity.data ?? []).filter((a) => !search || a.message.toLowerCase().includes(search.toLowerCase()) || a.event_type.includes(search.toLowerCase()));
  const eventTypes = Array.from(new Set((activity.data ?? []).map((a) => a.event_type))).sort();
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input placeholder="Search messages…" value={search} onChange={(e) => setSearch(e.target.value)} className="w-56" />
        <Select value={level} onChange={(e) => setLevel(e.target.value)} className="w-36"><option value="">All levels</option>{["debug", "info", "success", "warning", "error"].map((l) => <option key={l} value={l}>{l}</option>)}</Select>
        <Select value={eventType} onChange={(e) => setEventType(e.target.value)} className="w-56"><option value="">All events</option>{eventTypes.map((t) => <option key={t} value={t}>{t}</option>)}</Select>
        <span className="flex-1" />
        <Button size="sm" variant="outline" onClick={() => { setPaused((p) => !p); if (paused) queryClient.invalidateQueries({ queryKey: ["activity"] }); }}>{paused ? <><Play /> Resume live</> : <><Pause /> Pause</>}</Button>
      </div>
      <HudPanel padded={false}>
        {activity.isLoading ? (
          <div className="space-y-2 p-3"><Skeleton className="h-6" /><Skeleton className="h-6" /><Skeleton className="h-6" /></div>
        ) : activity.isError ? (
          <div className="p-3"><ErrorState error={activity.error} retry={() => activity.refetch()} /></div>
        ) : items.length === 0 ? (
          <div className="p-3"><EmptyState icon={ScrollText} title="No activity" description="Structured events from tasks, sessions and the agent appear here in real time." /></div>
        ) : (
          <div className="max-h-[70vh] overflow-y-auto font-mono text-[11px]">
            {items.map((a: ActivityEntry) => (
              <div key={a.id} className="border-b border-border/60 last:border-b-0">
                <button type="button" onClick={() => setExpanded(expanded === a.id ? null : a.id)} className="flex w-full items-start gap-2 px-3 py-1.5 text-left hover:bg-hud-ghost">
                  <span className="shrink-0 text-[9px] text-muted-foreground" title={formatDateTime(a.created_at)}>{formatTime(a.created_at)}</span>
                  <span className={cn("w-16 shrink-0 text-[9px] uppercase", LEVEL_COLOR[a.level] ?? "")}>{a.level}</span>
                  <span className="w-40 shrink-0 truncate text-[10px] text-primary/80">{a.event_type}</span>
                  <span className={cn("min-w-0 flex-1 break-words", LEVEL_COLOR[a.level] ?? "text-foreground")}>{a.message}</span>
                  {a.duration_ms != null ? <span className="shrink-0 text-[9px] text-muted-foreground">{formatDuration(a.duration_ms / 1000)}</span> : null}
                </button>
                {expanded === a.id ? (
                  <div className="space-y-1 bg-hud-dark px-3 py-2 text-[10px]">
                    <div className="flex flex-wrap gap-3 text-muted-foreground">
                      {a.task_id ? <Link href={`/tasks/${a.task_id}`} className="hover:text-primary">task {a.task_id.slice(0, 8)} →</Link> : null}
                      {a.project_id ? <Link href={`/projects/${a.project_id}`} className="hover:text-primary">project {a.project_id.slice(0, 8)} →</Link> : null}
                      {a.browser_session_id ? <Link href={`/sessions/${a.browser_session_id}`} className="hover:text-primary">session {a.browser_session_id.slice(0, 8)} →</Link> : null}
                      {a.scene_id ? <span>scene {a.scene_id.slice(0, 8)}</span> : null}
                    </div>
                    {Object.keys(a.data ?? {}).length ? <pre className="max-h-48 overflow-auto text-muted-foreground">{JSON.stringify(a.data, null, 2)}</pre> : null}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </HudPanel>
    </div>
  );
}

function AuditLog() {
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api.audit(200) });
  if (audit.isLoading) return <Skeleton className="h-40" />;
  if (audit.isError) return <ErrorState error={audit.error} retry={() => audit.refetch()} />;
  return (
    <HudPanel padded={false}>
      <table className="w-full font-mono text-[11px]">
        <thead><tr className="hud-label border-b border-border text-left"><th className="px-3 py-2">When</th><th className="px-2 py-2">Action</th><th className="px-2 py-2">Resource</th><th className="px-2 py-2">User</th><th className="px-2 py-2">IP</th><th className="px-2 py-2">Data</th></tr></thead>
        <tbody className="divide-y divide-border">
          {(audit.data ?? []).map((e) => (
            <tr key={e.id} className="hover:bg-hud-ghost">
              <td className="px-3 py-1.5 text-muted-foreground">{formatDateTime(e.created_at)}</td>
              <td className="px-2 py-1.5 text-primary">{e.action}</td>
              <td className="px-2 py-1.5">{e.resource_type} {e.resource_id ? e.resource_id.slice(0, 8) : ""}</td>
              <td className="px-2 py-1.5">{e.user_id ? e.user_id.slice(0, 8) : "system"}</td>
              <td className="px-2 py-1.5 text-muted-foreground">{e.ip_address}</td>
              <td className="max-w-[300px] truncate px-2 py-1.5 text-muted-foreground" title={JSON.stringify(e.data)}>{Object.keys(e.data ?? {}).length ? JSON.stringify(e.data) : ""}</td>
            </tr>
          ))}
          {(audit.data?.length ?? 0) === 0 ? <tr><td colSpan={6} className="px-3 py-4 text-center text-muted-foreground">No audit entries</td></tr> : null}
        </tbody>
      </table>
    </HudPanel>
  );
}

export default function ActivityPage() {
  const role = useAppStore((s) => s.auth?.role);
  return (
    <div className="space-y-4 p-4">
      <PageHeader eyebrow="Telemetry" title="Activity logs" description="Structured, timestamped events with task / session / scene correlation. Admins also see the security audit trail." />
      {isAdmin(role) ? (
        <Tabs defaultValue="activity">
          <TabsList><TabsTrigger value="activity">Activity</TabsTrigger><TabsTrigger value="audit">Audit trail</TabsTrigger></TabsList>
          <TabsContent value="activity" className="mt-3"><ActivityStream /></TabsContent>
          <TabsContent value="audit" className="mt-3"><AuditLog /></TabsContent>
        </Tabs>
      ) : (
        <ActivityStream />
      )}
    </div>
  );
}
