"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileVideo, Play, Trash2 } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudPanel } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { StatusBadge } from "@/components/ui/status-badge";
import { api } from "@/lib/api";
import { canWrite, toast, useAppStore } from "@/lib/store";
import type { GeneratedFile } from "@/lib/types";
import { formatBytes, formatDateTime, formatDuration, truncate } from "@/lib/utils";

export default function FilesPage() {
  const role = useAppStore((s) => s.auth?.role);
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = React.useState("");
  const [page, setPage] = React.useState(0);
  const [preview, setPreview] = React.useState<GeneratedFile | null>(null);
  const [confirm, setConfirm] = React.useState<GeneratedFile | null>(null);
  const limit = 50;
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => api.projects.list() });
  const files = useQuery({ queryKey: ["files", projectId, page], queryFn: () => api.files.list({ project_id: projectId || undefined, limit, offset: page * limit }), refetchInterval: 20000 });
  const remove = useMutation({
    mutationFn: (id: string) => api.files.remove(id),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["files"] }); setConfirm(null); toast({ title: "File deleted", variant: "success" }); },
    onError: (e: Error) => toast({ title: "Delete failed", description: e.message, variant: "destructive" }),
  });
  const items = files.data?.items ?? [];
  const totalBytes = items.reduce((a, f) => a + f.size_bytes, 0);
  const projectName = (id: string | null) => projects.data?.find((p) => p.id === id)?.name ?? (id ? id.slice(0, 8) : "—");

  return (
    <div className="space-y-4 p-4">
      <PageHeader eyebrow="Output" title="Generated files" description="Every clip the agent downloaded, verified and renamed. Links are signed and expire." actions={<Select value={projectId} onChange={(e) => { setProjectId(e.target.value); setPage(0); }} className="w-56"><option value="">All projects</option>{(projects.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>} />
      <ConfirmBanner open={!!confirm} title="DELETE FILE" detail={`Remove ${confirm?.filename ?? ""} from disk and the database.`} tone="danger" busy={remove.isPending} onConfirm={() => confirm && remove.mutate(confirm.id)} onCancel={() => setConfirm(null)} />
      <Dialog open={!!preview} onOpenChange={(o) => !o && setPreview(null)}>
        <DialogContent wide>
          <DialogTitle>{preview?.filename}</DialogTitle>
          {preview ? (
            preview.mime_type.startsWith("video/") ? (
              <video src={preview.preview.url} controls autoPlay className="max-h-[70vh] w-full bg-black" />
            ) : preview.mime_type.startsWith("image/") ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={preview.preview.url} alt={preview.filename} className="max-h-[70vh] w-full bg-black object-contain" />
            ) : (
              <iframe src={preview.preview.url} title={preview.filename} className="h-[60vh] w-full border border-border bg-background" />
            )
          ) : null}
          {preview?.prompt ? <p className="font-mono text-[10px] text-muted-foreground">Prompt: {preview.prompt}</p> : null}
        </DialogContent>
      </Dialog>

      <HudPanel padded={false} title={files.data ? `${files.data.total} FILE(S) · ${formatBytes(totalBytes)} ON THIS PAGE` : "FILES"}>
        {files.isLoading ? (
          <div className="space-y-2 p-3"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
        ) : files.isError ? (
          <div className="p-3"><ErrorState error={files.error} retry={() => files.refetch()} /></div>
        ) : items.length === 0 ? (
          <div className="p-3"><EmptyState icon={FileVideo} title="No files yet" description="Run a generation task; downloads are verified, renamed (scene_001.mp4 …) and listed here with metadata." /></div>
        ) : (
          <table className="w-full font-mono text-[11px]">
            <thead>
              <tr className="hud-label border-b border-border text-left">
                <th className="px-3 py-2">File</th>
                <th className="px-2 py-2">Project</th>
                <th className="px-2 py-2">Platform</th>
                <th className="px-2 py-2">Size</th>
                <th className="px-2 py-2">Duration</th>
                <th className="px-2 py-2">Created</th>
                <th className="px-2 py-2">Status</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((f) => (
                <tr key={f.id} className="hover:bg-hud-ghost">
                  <td className="px-3 py-1.5">
                    <button type="button" className="flex items-center gap-2 text-left hover:text-primary" onClick={() => setPreview(f)}>
                      <Play className="h-3 w-3 shrink-0 text-primary" />
                      <span className="truncate font-bold" title={f.prompt}>{f.filename}</span>
                    </button>
                    {f.width ? <span className="text-[9px] text-muted-foreground">{f.width}×{f.height} · gen {f.generation_seconds ? formatDuration(f.generation_seconds) : "—"}{f.retry_count ? ` · ${f.retry_count} retries` : ""}</span> : null}
                  </td>
                  <td className="px-2 py-1.5">{f.project_id ? <Link href={`/projects/${f.project_id}?tab=files`} className="hover:text-primary">{truncate(projectName(f.project_id), 24)}</Link> : "—"}</td>
                  <td className="px-2 py-1.5">{f.platform_key}</td>
                  <td className="px-2 py-1.5">{formatBytes(f.size_bytes)}</td>
                  <td className="px-2 py-1.5">{f.duration_seconds ? formatDuration(f.duration_seconds) : "—"}</td>
                  <td className="px-2 py-1.5 text-muted-foreground">{formatDateTime(f.created_at)}</td>
                  <td className="px-2 py-1.5"><StatusBadge status={f.status} /></td>
                  <td className="px-2 py-1.5">
                    <div className="flex items-center justify-end gap-1">
                      <a href={f.download.url} download className="hud-pill text-primary hover:bg-hud-ghost"><Download className="h-3 w-3" /></a>
                      {canWrite(role) ? <Button size="icon-sm" variant="ghost" onClick={() => setConfirm(f)}><Trash2 /></Button> : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </HudPanel>
      {files.data && files.data.total > limit ? (
        <div className="flex items-center justify-end gap-2">
          <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</Button>
          <span className="font-mono text-[10px] text-muted-foreground">page {page + 1} / {Math.ceil(files.data.total / limit)}</span>
          <Button size="sm" variant="outline" disabled={(page + 1) * limit >= files.data.total} onClick={() => setPage((p) => p + 1)}>Next</Button>
        </div>
      ) : null}
    </div>
  );
}
