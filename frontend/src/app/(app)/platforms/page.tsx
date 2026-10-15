"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plug, ShieldCheck } from "lucide-react";

import { HudPanel, HudPill } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import { isAdmin, toast, useAppStore } from "@/lib/store";
import { cn, titleCase } from "@/lib/utils";

export default function PlatformsPage() {
  const role = useAppStore((s) => s.auth?.role);
  const queryClient = useQueryClient();
  const platforms = useQuery({ queryKey: ["platforms"], queryFn: api.platforms.list });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: api.sessions.list });
  const toggle = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) => api.platforms.setEnabled(key, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["platforms"] }),
    onError: (e: Error) => toast({ title: "Update failed", description: e.message, variant: "destructive" }),
  });
  const [category, setCategory] = React.useState("");
  const categories = Array.from(new Set((platforms.data ?? []).map((p) => p.category)));
  const list = (platforms.data ?? []).filter((p) => !category || p.category === category);
  return (
    <div className="space-y-4 p-4">
      <PageHeader eyebrow="Adapter registry" title="Platforms" description="Each platform is a plug-in adapter (open → verify login → upload refs → prompt → detect state → download). Platforms without a dedicated adapter fall back to the generic AI browser agent." />
      <div className="flex flex-wrap gap-1.5">
        <button type="button" className={cn("hud-pill", !category ? "border-primary text-primary" : "text-muted-foreground")} onClick={() => setCategory("")}>All</button>
        {categories.map((c) => <button key={c} type="button" className={cn("hud-pill", category === c ? "border-primary text-primary" : "text-muted-foreground")} onClick={() => setCategory(c)}>{titleCase(c)}</button>)}
      </div>
      {platforms.isLoading ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3"><Skeleton className="h-36" /><Skeleton className="h-36" /><Skeleton className="h-36" /></div>
      ) : platforms.isError ? (
        <ErrorState error={platforms.error} retry={() => platforms.refetch()} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {list.map((p) => {
            const sess = (sessions.data ?? []).filter((s) => s.platform_key === p.key);
            const loggedIn = sess.filter((s) => s.login_status === "logged_in").length;
            return (
              <HudPanel key={p.key} corners className={cn(!p.enabled && "opacity-60")} title={<span className="flex items-center gap-2"><Plug className="h-3 w-3" /> {p.name}</span>} actions={<HudPill tone={p.enabled ? "green" : "dim"}>{p.enabled ? "enabled" : "disabled"}</HudPill>}>
                <div className="space-y-2">
                  <p className="text-[11px] text-muted-foreground">{p.description}</p>
                  <div className="flex flex-wrap gap-1">
                    <HudPill tone="dim">{titleCase(p.category)}</HudPill>
                    <HudPill tone={p.adapter_available ? "default" : "orange"}>{p.adapter_available ? "dedicated adapter" : "generic agent"}</HudPill>
                    {p.requires_login ? <HudPill tone="amber"><ShieldCheck className="h-3 w-3" /> manual login</HudPill> : null}
                  </div>
                  {p.supported_actions?.length ? <p className="font-sans text-[10px] text-muted-foreground">actions: {p.supported_actions.join(", ")}</p> : null}
                  {p.notes ? <p className="font-sans text-[10px] text-hud-amber">{p.notes}</p> : null}
                  <div className="flex items-center justify-between font-sans text-[10px] text-muted-foreground">
                    <span>{sess.length} session(s) · {loggedIn} logged in</span>
                    {p.base_url ? <a href={p.base_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 hover:text-primary">{p.base_url.replace(/^https?:\/\//, "").slice(0, 28)} <ExternalLink className="h-3 w-3" /></a> : null}
                  </div>
                  <div className="flex items-center gap-1.5 pt-1">
                    <Link href={`/sessions?platform=${p.key}`}><Button size="sm" variant="outline">Sessions</Button></Link>
                    <Link href={`/new-task?platform=${p.key}`}><Button size="sm" variant="ghost" disabled={!p.enabled}>New task</Button></Link>
                    <span className="flex-1" />
                    {isAdmin(role) ? (
                      <Button size="sm" variant={p.enabled ? "ghost" : "default"} loading={toggle.isPending && toggle.variables?.key === p.key} onClick={() => toggle.mutate({ key: p.key, enabled: !p.enabled })}>
                        {p.enabled ? "Disable" : "Enable"}
                      </Button>
                    ) : null}
                  </div>
                </div>
              </HudPanel>
            );
          })}
        </div>
      )}
    </div>
  );
}
