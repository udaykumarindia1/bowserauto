"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Hand, Loader2, MonitorOff, MousePointer2, Paperclip, RotateCw, Sparkles, Upload, Wifi, WifiOff } from "lucide-react";

import { api } from "@/lib/api";
import { realtime } from "@/lib/realtime";
import { toast, useAppStore } from "@/lib/store";
import type { BrowserSession, FrameMessage, SessionActionMessage, SessionStatusMessage } from "@/lib/types";
import { cn, truncate } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Hint } from "@/components/ui/tooltip";

export interface LiveViewerProps {
  session: BrowserSession | null | undefined;
  /** Called when the user presses TAKE OVER / RETURN CONTROL. When omitted the session-level endpoints are used. */
  onTakeover?: () => Promise<unknown> | void;
  onRelease?: () => Promise<unknown> | void;
  takeoverPending?: boolean;
  /** Whether the current user may control the browser (role + task state). */
  canControl?: boolean;
  humanControl?: boolean;
  className?: string;
  compact?: boolean;
  /** Extra toolbar content (e.g. agent state). */
  toolbar?: React.ReactNode;
}

const MODIFIER_KEYS = new Set(["Shift", "Control", "Alt", "Meta"]);

/**
 * Live browser viewer: renders CDP screencast frames streamed by the worker and, while the human
 * has control, forwards pointer/keyboard/scroll/paste events back to the real browser.
 */
export function LiveViewer({ session, onTakeover, onRelease, takeoverPending, canControl = true, humanControl, className, compact, toolbar }: LiveViewerProps) {
  const sessionId = session?.id;
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const [frameInfo, setFrameInfo] = React.useState<{ width: number; height: number; seq: number; ts: number } | null>(null);
  const [status, setStatus] = React.useState<SessionStatusMessage | null>(null);
  const [lastAction, setLastAction] = React.useState<SessionActionMessage | null>(null);
  const [fps, setFps] = React.useState(0);
  const [urlDraft, setUrlDraft] = React.useState("");
  const [editingUrl, setEditingUrl] = React.useState(false);
  const [focused, setFocused] = React.useState(false);
  const connection = useAppStore((s) => s.connection);
  const queryClient = useQueryClient();
  const frameTimes = React.useRef<number[]>([]);
  const lastMove = React.useRef(0);
  const pressedButton = React.useRef<number | null>(null);

  const interactive = (status?.interactive ?? session?.interactive ?? false) && (humanControl ?? true);
  const live = !!session && ["ready", "in_use", "launching"].includes(status?.status ?? session.status);

  // ---------------------------------------------------------------- frame rendering
  React.useEffect(() => {
    if (!sessionId) return;
    const client = realtime();
    client.connect();
    const image = new Image();
    let pendingUrl: string | null = null;
    let drawing = false;
    const draw = (frame: FrameMessage) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      if (canvas.width !== frame.width || canvas.height !== frame.height) {
        canvas.width = frame.width || canvas.width || 1280;
        canvas.height = frame.height || canvas.height || 800;
      }
      const blob = `data:image/jpeg;base64,${frame.data}`;
      if (drawing) {
        pendingUrl = blob; // keep only the newest frame while decoding
        return;
      }
      drawing = true;
      image.onload = () => {
        const ctx = canvas.getContext("2d");
        ctx?.drawImage(image, 0, 0, canvas.width, canvas.height);
        drawing = false;
        if (pendingUrl) {
          const next = pendingUrl;
          pendingUrl = null;
          image.src = next;
          drawing = true;
        }
      };
      image.onerror = () => {
        drawing = false;
      };
      image.src = blob;
      const now = performance.now();
      frameTimes.current.push(now);
      frameTimes.current = frameTimes.current.filter((t) => now - t < 2000);
      setFps(Math.round(frameTimes.current.length / 2));
      setFrameInfo({ width: frame.width, height: frame.height, seq: frame.seq, ts: frame.ts });
    };
    const unsubscribe = client.subscribeSession(sessionId, {
      frame: draw,
      status: (message) => {
        setStatus(message);
        if (!editingUrl) setUrlDraft(message.url || "");
      },
      action: (message) => setLastAction(message),
    });
    return () => {
      unsubscribe();
      setFrameInfo(null);
      setStatus(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  React.useEffect(() => {
    if (session && !editingUrl && !status) setUrlDraft(session.current_url || "");
  }, [session, editingUrl, status]);

  // ---------------------------------------------------------------- input forwarding
  const send = React.useCallback(
    (event: Record<string, unknown>) => {
      if (!sessionId || !interactive || !canControl) return;
      realtime().sendInput(sessionId, event);
    },
    [sessionId, interactive, canControl],
  );

  const coords = (e: React.MouseEvent | React.WheelEvent): { x: number; y: number } | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return { x: Math.round((e.clientX - rect.left) * scaleX), y: Math.round((e.clientY - rect.top) * scaleY) };
  };

  const onMouseMove = (e: React.MouseEvent) => {
    const now = performance.now();
    if (now - lastMove.current < 33) return; // ~30 events/s max
    lastMove.current = now;
    const c = coords(e);
    if (c) send({ type: "mousemove", ...c });
  };
  const onMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    containerRef.current?.focus();
    const c = coords(e);
    if (!c) return;
    pressedButton.current = e.button;
    send({ type: "mousedown", ...c, button: e.button, click_count: e.detail || 1 });
  };
  const onMouseUp = (e: React.MouseEvent) => {
    e.preventDefault();
    const c = coords(e);
    if (!c) return;
    send({ type: "mouseup", ...c, button: e.button, click_count: e.detail || 1 });
    pressedButton.current = null;
  };
  const onWheel = (e: React.WheelEvent) => {
    const c = coords(e);
    if (!c) return;
    send({ type: "wheel", ...c, delta_x: Math.round(e.deltaX), delta_y: Math.round(e.deltaY) });
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!interactive) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") return; // handled by onPaste
    if (e.key === "F5" || (e.ctrlKey && e.key.toLowerCase() === "r")) {
      e.preventDefault();
      send({ type: "reload" });
      return;
    }
    e.preventDefault();
    if (e.repeat && MODIFIER_KEYS.has(e.key)) return;
    send({ type: "keydown", key: e.key, code: e.code });
  };
  const onKeyUp = (e: React.KeyboardEvent) => {
    if (!interactive) return;
    e.preventDefault();
    send({ type: "keyup", key: e.key, code: e.code });
  };
  const onPaste = (e: React.ClipboardEvent) => {
    if (!interactive) return;
    const text = e.clipboardData.getData("text");
    if (text) {
      e.preventDefault();
      send({ type: "paste", text });
    }
  };
  const onBlur = () => {
    setFocused(false);
    send({ type: "release_keys" });
  };

  // ---------------------------------------------------------------- toolbar actions
  const navigate = useMutation({
    mutationFn: (url: string) => api.sessions.navigate(sessionId!, url),
    onError: (e: Error) => toast({ title: "Navigation failed", description: e.message, variant: "destructive" }),
  });
  const sessionTakeover = useMutation({
    mutationFn: () => api.sessions.takeover(sessionId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sessions"] }),
    onError: (e: Error) => toast({ title: "Takeover failed", description: e.message, variant: "destructive" }),
  });
  const sessionRelease = useMutation({
    mutationFn: () => api.sessions.release(sessionId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sessions"] }),
    onError: (e: Error) => toast({ title: "Release failed", description: e.message, variant: "destructive" }),
  });
  const upload = useMutation({
    mutationFn: (file: File) => api.sessions.upload(sessionId!, file),
    onSuccess: (r) => toast({ title: "File sent to browser", description: r.filename, variant: "success" }),
    onError: (e: Error) => toast({ title: "Upload failed", description: e.message, variant: "destructive" }),
  });

  const submitUrl = (e: React.FormEvent) => {
    e.preventDefault();
    let url = urlDraft.trim();
    if (!url) return;
    if (!/^[a-z]+:\/\//i.test(url)) url = `https://${url}`;
    setEditingUrl(false);
    if (interactive) send({ type: "navigate", url });
    else navigate.mutate(url);
  };

  const takeover = onTakeover ?? (() => sessionTakeover.mutate());
  const release = onRelease ?? (() => sessionRelease.mutate());
  const busy = takeoverPending || sessionTakeover.isPending || sessionRelease.isPending;
  const title = status?.title || session?.current_title || "";
  const currentUrl = status?.url || session?.current_url || "";
  const action = status?.action || lastAction?.action || "";
  const agentDriving = !!status?.task_id && !interactive;

  return (
    <div ref={containerRef} tabIndex={0} onFocus={() => setFocused(true)} onBlur={onBlur} onKeyDown={onKeyDown} onKeyUp={onKeyUp} onPaste={onPaste} className={cn("hud-corners flex min-h-0 flex-col overflow-hidden border border-border bg-black/95 outline-none", focused && interactive && "ring-1 ring-hud-green/80", className)}>
      {/* URL bar + controls */}
      <div className="flex items-center gap-1.5 border-b border-border bg-card px-2 py-1.5">
        <Hint label="Back">
          <Button variant="ghost" size="icon-sm" disabled={!interactive} onClick={() => send({ type: "back" })}>
            <ArrowLeft />
          </Button>
        </Hint>
        <Hint label="Forward">
          <Button variant="ghost" size="icon-sm" disabled={!interactive} onClick={() => send({ type: "forward" })}>
            <ArrowRight />
          </Button>
        </Hint>
        <Hint label="Reload">
          <Button variant="ghost" size="icon-sm" disabled={!interactive} onClick={() => send({ type: "reload" })}>
            <RotateCw />
          </Button>
        </Hint>
        <form onSubmit={submitUrl} className="flex min-w-0 flex-1 items-center gap-2 border border-border bg-background px-2">
          <span className={cn("hud-dot shrink-0", live ? "bg-hud-green text-hud-green" : "bg-muted-foreground text-muted-foreground")} />
          <input
            value={urlDraft}
            onChange={(e) => setUrlDraft(e.target.value)}
            onFocus={() => setEditingUrl(true)}
            onBlur={() => setEditingUrl(false)}
            placeholder={live ? "Enter a URL" : "Browser is not open"}
            disabled={!live || (!interactive && !!status?.task_id)}
            className="h-7 min-w-0 flex-1 bg-transparent font-mono text-[11px] outline-none placeholder:text-muted-foreground disabled:opacity-60"
            spellCheck={false}
          />
          {title ? <span className="hidden max-w-[30%] truncate text-[11px] text-muted-foreground lg:inline">{title}</span> : null}
        </form>
        {toolbar}
        {interactive ? (
          <>
            <input ref={fileInputRef} type="file" className="hidden" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
            <Hint label={status?.file_chooser_pending ? "The page is asking for a file – choose one to upload" : "Upload a file into the page's pending file chooser"}>
              <Button variant={status?.file_chooser_pending ? "warning" : "ghost"} size="icon-sm" onClick={() => fileInputRef.current?.click()} loading={upload.isPending}>
                {status?.file_chooser_pending ? <Upload /> : <Paperclip />}
              </Button>
            </Hint>
          </>
        ) : null}
        {canControl ? (
          interactive ? (
            <Button size="sm" variant="success" onClick={() => void release()} loading={busy}>
              <Sparkles /> {compact ? "Return to AI" : "RETURN CONTROL TO AI"}
            </Button>
          ) : (
            <Button size="sm" variant="warning" onClick={() => void takeover()} loading={busy} disabled={!live}>
              <Hand /> {compact ? "Take over" : "TAKE OVER BROWSER"}
            </Button>
          )
        ) : null}
      </div>

      {/* Viewport */}
      <div className="relative flex min-h-0 flex-1 items-center justify-center bg-[#0a0a0a]">
        <canvas
          ref={canvasRef}
          width={1280}
          height={800}
          className={cn("max-h-full max-w-full select-none object-contain", interactive ? "cursor-crosshair" : "cursor-default")}
          onMouseMove={onMouseMove}
          onMouseDown={onMouseDown}
          onMouseUp={onMouseUp}
          onContextMenu={(e) => e.preventDefault()}
          onWheel={onWheel}
          onDoubleClick={(e) => e.preventDefault()}
        />
        {!session ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
            <MonitorOff className="h-8 w-8" />
            No browser session attached
          </div>
        ) : !live ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/70 text-sm text-muted-foreground">
            <MonitorOff className="h-8 w-8" />
            Browser is {status?.status ?? session.status}
          </div>
        ) : !frameInfo ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/60 text-sm text-muted-foreground">
            <Loader2 className="h-6 w-6 animate-spin" />
            {connection === "open" ? "Waiting for first frame…" : "Connecting to live stream…"}
          </div>
        ) : null}
        {interactive ? (
          <div className="pointer-events-none absolute left-2 top-2 flex items-center gap-1 bg-hud-green/90 px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-black">
            <Hand className="h-3 w-3" /> You are in control {focused ? "" : "— click the viewer to type"}
          </div>
        ) : agentDriving ? (
          <div className="pointer-events-none absolute left-2 top-2 flex items-center gap-1 bg-primary/90 px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-black">
            <MousePointer2 className="h-3 w-3" /> AI agent is driving
          </div>
        ) : null}
        {status?.tabs && status.tabs.length > 1 ? (
          <div className="absolute bottom-2 left-2 flex max-w-[70%] gap-1 overflow-x-auto">
            {status.tabs.map((tab) => (
              <button
                key={tab.index}
                disabled={!interactive}
                onClick={() => send({ type: "switch_tab", index: tab.index })}
                className={cn("truncate border px-2 py-0.5 font-mono text-[10px]", tab.active ? "border-primary bg-primary/20 text-foreground" : "border-white/20 bg-black/60 text-muted-foreground")}
              >
                {truncate(tab.title || tab.url || `Tab ${tab.index + 1}`, 28)}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {/* Status strip */}
      <div className="flex items-center gap-2 border-t border-border bg-card px-2 py-1 font-mono text-[10px] text-muted-foreground">
        {connection === "open" ? <Wifi className="h-3 w-3 text-hud-green" /> : <WifiOff className="h-3 w-3 text-hud-amber" />}
        <span className="truncate">{action ? <span className="text-foreground">{action}</span> : agentDriving ? "Agent working…" : interactive ? "Human control" : live ? "Idle" : "Offline"}</span>
        <span className="flex-1" />
        {currentUrl ? <span className="hidden max-w-[40%] truncate font-mono md:inline">{currentUrl}</span> : null}
        {frameInfo ? (
          <Badge variant="muted" className="font-mono">
            {frameInfo.width}×{frameInfo.height} · {fps} fps
          </Badge>
        ) : null}
      </div>
    </div>
  );
}
