"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bug, Clapperboard, Image as ImageIcon, PenLine, Plus, Search, Table2, Trash2, Workflow, type LucideIcon } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudPanel, HudPill } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import { canWrite, toast, useAppStore } from "@/lib/store";
import type { Template } from "@/lib/types";
import { titleCase } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = { clapperboard: Clapperboard, image: ImageIcon, search: Search, table: Table2, bug: Bug, pen: PenLine, workflow: Workflow };
const KINDS = ["browser_workflow", "video_generation", "image_generation", "research", "data_entry", "website_testing", "content_creation"];

function TemplateDialog({ template, open, onOpenChange }: { template: Template | null; open: boolean; onOpenChange: (o: boolean) => void }) {
  const queryClient = useQueryClient();
  const platforms = useQuery({ queryKey: ["platforms"], queryFn: api.platforms.list, enabled: open });
  const initial = React.useMemo(() => ({
    key: template?.key ?? "",
    name: template?.name ?? "",
    description: template?.description ?? "",
    kind: template?.kind ?? "browser_workflow",
    icon: template?.icon ?? "workflow",
    platform_key: (template?.config.platform_key as string) ?? "generic",
    instructions: (template?.config.instructions as string) ?? "",
    visual_style: (template?.config.visual_style as string) ?? "",
  }), [template]);
  const [form, setForm] = React.useState(initial);
  React.useEffect(() => setForm(initial), [initial]);
  const save = useMutation({
    mutationFn: () => {
      const config = { ...(template?.config ?? {}), platform_key: form.platform_key, instructions: form.instructions, ...(form.visual_style ? { visual_style: form.visual_style } : {}) };
      const body = { name: form.name.trim(), description: form.description, kind: form.kind, icon: form.icon, config };
      return template ? api.templates.update(template.id, body) : api.templates.create({ key: form.key.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_"), ...body });
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["templates"] }); onOpenChange(false); toast({ title: template ? "Template updated" : "Template created", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Save failed", description: e.message, variant: "destructive" }),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>{template ? `Edit ${template.name}` : "New template"}</DialogTitle>
        <DialogDescription>Templates pre-fill the New Task wizard: workflow kind, default platform and starting instructions.</DialogDescription>
        <div className="grid gap-3 sm:grid-cols-2">
          {!template ? <div className="space-y-1"><Label>Key</Label><Input value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} placeholder="my_workflow" /></div> : null}
          <div className="space-y-1"><Label>Name</Label><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          <div className="space-y-1">
            <Label>Kind</Label>
            <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{KINDS.map((k) => <option key={k} value={k}>{titleCase(k)}</option>)}</Select>
          </div>
          <div className="space-y-1">
            <Label>Icon</Label>
            <Select value={form.icon} onChange={(e) => setForm({ ...form, icon: e.target.value })}>{Object.keys(ICONS).map((k) => <option key={k} value={k}>{k}</option>)}</Select>
          </div>
          <div className="space-y-1">
            <Label>Default platform</Label>
            <Select value={form.platform_key} onChange={(e) => setForm({ ...form, platform_key: e.target.value })}>{(platforms.data ?? []).map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}</Select>
          </div>
          <div className="space-y-1"><Label>Visual style (video/image)</Label><Input value={form.visual_style} onChange={(e) => setForm({ ...form, visual_style: e.target.value })} /></div>
          <div className="space-y-1 sm:col-span-2"><Label>Description</Label><Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
          <div className="space-y-1 sm:col-span-2"><Label>Default instructions</Label><Textarea rows={4} value={form.instructions} onChange={(e) => setForm({ ...form, instructions: e.target.value })} /></div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button disabled={!form.name.trim() || (!template && !form.key.trim())} loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function TemplatesPage() {
  const role = useAppStore((s) => s.auth?.role);
  const queryClient = useQueryClient();
  const templates = useQuery({ queryKey: ["templates"], queryFn: api.templates.list });
  const [editing, setEditing] = React.useState<Template | null>(null);
  const [open, setOpen] = React.useState(false);
  const [confirm, setConfirm] = React.useState<Template | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => api.templates.remove(id),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["templates"] }); setConfirm(null); },
    onError: (e: Error) => toast({ title: "Delete failed", description: e.message, variant: "destructive" }),
  });
  const writable = canWrite(role);
  return (
    <div className="space-y-4 p-4">
      <PageHeader eyebrow="Playbooks" title="Templates" description="Built-in workflows plus your organisation's custom ones." actions={writable ? <Button size="sm" onClick={() => { setEditing(null); setOpen(true); }}><Plus /> New template</Button> : null} />
      <TemplateDialog template={editing} open={open} onOpenChange={setOpen} />
      <ConfirmBanner open={!!confirm} title="DELETE TEMPLATE" detail={`Remove “${confirm?.name ?? ""}”. Existing projects and tasks are not affected.`} tone="danger" busy={remove.isPending} onConfirm={() => confirm && remove.mutate(confirm.id)} onCancel={() => setConfirm(null)} />
      {templates.isLoading ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3"><Skeleton className="h-32" /><Skeleton className="h-32" /><Skeleton className="h-32" /></div>
      ) : templates.isError ? (
        <ErrorState error={templates.error} retry={() => templates.refetch()} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(templates.data ?? []).map((t) => {
            const Icon = ICONS[t.icon] ?? Workflow;
            return (
              <HudPanel key={t.id} corners title={<span className="flex items-center gap-2"><Icon className="h-3.5 w-3.5 text-primary" /> {t.name}</span>} actions={<HudPill tone={t.is_builtin ? "dim" : "default"}>{t.is_builtin ? "built-in" : "custom"}</HudPill>}>
                <p className="min-h-[2.5rem] text-[11px] text-muted-foreground">{t.description}</p>
                <p className="mt-2 font-mono text-[10px] text-muted-foreground">{titleCase(t.kind)} · platform {(t.config.platform_key as string) ?? "generic"}</p>
                <div className="mt-3 flex items-center gap-1.5">
                  <Link href={`/new-task?template=${t.key}`}><Button size="sm">Use template</Button></Link>
                  {writable && !t.is_builtin ? (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => { setEditing(t); setOpen(true); }}>Edit</Button>
                      <Button size="icon-sm" variant="ghost" onClick={() => setConfirm(t)}><Trash2 /></Button>
                    </>
                  ) : null}
                </div>
              </HudPanel>
            );
          })}
        </div>
      )}
    </div>
  );
}
