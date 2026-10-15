"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Trash2 } from "lucide-react";

import { ConfirmBanner } from "@/components/hud/confirm-banner";
import { HudPanel } from "@/components/hud/hud-primitives";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { api, ApiError } from "@/lib/api";
import { useJarvis } from "@/lib/jarvis-store";
import { canWrite, toast, useAppStore } from "@/lib/store";
import { formatDateTime } from "@/lib/utils";

/** "🧠 WHAT AVINASH REMEMBERS" — view, add and delete stored facts (Mark-LV MemoryOverlay). */
export function MemoryPanel({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const role = useAppStore((s) => s.auth?.role);
  const assistant = useJarvis((s) => s.prefs.assistantName);
  const writable = canWrite(role);
  const facts = useQuery({ queryKey: ["memory"], queryFn: api.assistant.memory.list });
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["memory"] });
  const add = useMutation({
    mutationFn: () => api.assistant.memory.add({ key: key.trim() || undefined, value: value.trim() }),
    onSuccess: () => {
      setKey("");
      setValue("");
      invalidate();
    },
    onError: (e: Error) => toast({ title: "Not stored", description: e instanceof ApiError ? e.message : "Could not store the fact", variant: "destructive" }),
  });
  const remove = useMutation({ mutationFn: (id: string) => api.assistant.memory.remove(id), onSuccess: invalidate });
  const clear = useMutation({
    mutationFn: () => api.assistant.memory.clear(),
    onSuccess: () => {
      setConfirmClear(false);
      invalidate();
      toast({ title: "Memory cleared", variant: "warning" });
    },
  });

  return (
    <HudPanel
      title={
        <span className="flex items-center gap-2">
          <Brain className="h-3.5 w-3.5" /> What {assistant} remembers
        </span>
      }
      actions={
        writable && (facts.data?.length ?? 0) > 0 ? (
          <button type="button" onClick={() => setConfirmClear(true)} className="font-sans text-[9px] uppercase tracking-[0.12em] text-muted-foreground hover:text-hud-red">
            Clear all
          </button>
        ) : null
      }
      className={className}
    >
      <p className="mb-3 text-[11px] leading-relaxed text-muted-foreground">
        Facts here are visible to everyone in the organisation and are handed to the browser agent as context. Credentials are refused — log in through the browser session instead.
        In the console say <span className="text-primary">&ldquo;remember …&rdquo;</span>, <span className="text-primary">&ldquo;what do you remember&rdquo;</span> or <span className="text-primary">&ldquo;forget …&rdquo;</span>.
      </p>
      {writable && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) add.mutate();
          }}
          className="mb-3 grid gap-1.5 sm:grid-cols-[160px_1fr_auto]"
        >
          <input className="hud-input" placeholder="Label (optional)" value={key} maxLength={120} onChange={(e) => setKey(e.target.value)} />
          <input className="hud-input" placeholder="Fact, e.g. brand colour is teal" value={value} maxLength={4000} onChange={(e) => setValue(e.target.value)} />
          <button type="submit" disabled={!value.trim() || add.isPending} className="border border-primary/60 bg-hud-ghost px-3 py-1.5 font-sans text-[10px] font-bold uppercase tracking-[0.14em] text-primary hover:bg-primary hover:text-primary-foreground disabled:opacity-50">
            Remember
          </button>
        </form>
      )}
      {facts.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
        </div>
      ) : (facts.data?.length ?? 0) === 0 ? (
        <EmptyState icon={Brain} title="Nothing stored yet" description={`Tell ${assistant} to remember something and it will show up here.`} />
      ) : (
        <ul className="divide-y divide-border border border-border">
          {facts.data!.map((fact) => (
            <li key={fact.id} className="flex items-start gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="font-sans text-[10px] font-bold uppercase tracking-[0.12em] text-primary">{fact.key}</div>
                <div className="whitespace-pre-wrap break-words text-xs text-foreground">{fact.value}</div>
                <div className="mt-0.5 font-sans text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                  {fact.category} · {fact.source} · {formatDateTime(fact.updated_at)}
                </div>
              </div>
              {writable && (
                <button type="button" onClick={() => remove.mutate(fact.id)} className="text-muted-foreground hover:text-hud-red" aria-label={`Forget ${fact.key}`}>
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <ConfirmBanner open={confirmClear} title="CLEAR MEMORY" detail={`Delete every fact ${assistant} has stored for this organisation.`} tone="danger" busy={clear.isPending} onConfirm={() => clear.mutate()} onCancel={() => setConfirmClear(false)} />
    </HudPanel>
  );
}
