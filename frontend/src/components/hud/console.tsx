"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { Typewriter } from "@/components/hud/hud-primitives";
import { useJarvisConsole } from "@/components/hud/jarvis-provider";
import { Hint } from "@/components/ui/tooltip";
import { useJarvis, type ConsoleMessage } from "@/lib/jarvis-store";
import { useAppStore } from "@/lib/store";
import { voiceSupport } from "@/lib/voice";
import { cn, formatTime } from "@/lib/utils";

type Filter = "all" | "chat" | "log";

const QUICK_COMMANDS = ["status", "stop everything", "what do you remember", "open sessions", "help"];

function tagFor(message: ConsoleMessage, assistant: string): { label: string; className: string } {
  if (message.role === "u") return { label: "YOU", className: "text-hud-green" };
  if (message.role === "j") return { label: assistant.toUpperCase(), className: "text-primary" };
  if (message.role === "sys") return { label: "SYS", className: "text-hud-amber" };
  const level = message.level ?? "info";
  return {
    label: "LOG",
    className: level === "error" ? "text-hud-red" : level === "warning" ? "text-hud-amber" : level === "success" ? "text-hud-green" : "text-muted-foreground",
  };
}

/** Right-hand JARVIS column: ▸ ACTIVITY LOG feed + ▸ COMMAND INPUT with voice controls. */
export function JarvisConsole({ className }: { className?: string }) {
  const controller = useJarvisConsole();
  const messages = useJarvis((s) => s.messages);
  const listening = useJarvis((s) => s.listening);
  const speaking = useJarvis((s) => s.speaking);
  const muted = useJarvis((s) => s.muted);
  const interim = useJarvis((s) => s.interim);
  const hudState = useJarvis((s) => s.state);
  const prefs = useJarvis((s) => s.prefs);
  const setPrefs = useJarvis((s) => s.setPrefs);
  const setMuted = useJarvis((s) => s.setMuted);
  const focusInput = useJarvis((s) => s.focusInput);
  const role = useAppStore((s) => s.auth?.role);
  const [filter, setFilter] = useState<Filter>("all");
  const [value, setValue] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const feedRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const stickToBottom = useRef(true);

  const visible = useMemo(() => messages.filter((m) => (filter === "all" ? true : filter === "chat" ? m.role !== "log" : m.role === "log")), [messages, filter]);

  useEffect(() => {
    const el = feedRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [visible.length, interim]);

  useEffect(() => {
    if (focusInput) inputRef.current?.focus();
  }, [focusInput]);

  const submit = (text = value) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setHistory((h) => [trimmed, ...h.filter((x) => x !== trimmed)].slice(0, 50));
    setHistoryIndex(-1);
    setValue("");
    void controller.send(trimmed);
  };

  const readOnly = role === "viewer";

  return (
    <aside className={cn("flex h-full min-h-0 flex-col bg-hud-dark", className)} aria-label="JARVIS console">
      {/* ---------------------------------------------------------------- feed */}
      <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
        <h2 className="hud-section border-0 pb-0">ACTIVITY LOG</h2>
        <div className="flex items-center gap-1">
          {(["all", "chat", "log"] as Filter[]).map((f) => (
            <button key={f} type="button" onClick={() => setFilter(f)} className={cn("px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase tracking-[0.14em]", filter === f ? "border border-primary/60 bg-hud-ghost text-primary" : "text-muted-foreground hover:text-primary")}>
              {f}
            </button>
          ))}
          <Hint label="Clear console">
            <button type="button" onClick={() => useJarvis.getState().clear()} className="ml-1 px-1 font-mono text-[10px] text-muted-foreground hover:text-hud-red" aria-label="Clear console">
              ✕
            </button>
          </Hint>
        </div>
      </div>
      <div
        ref={feedRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="min-h-0 flex-1 overflow-y-auto px-3 py-2 font-mono text-[11.5px] leading-relaxed"
      >
        {visible.length === 0 && <div className="py-6 text-center text-[10px] uppercase tracking-[0.2em] text-muted-foreground/70">— awaiting input —</div>}
        {visible.map((m) => {
          const tag = tagFor(m, prefs.assistantName);
          const isChat = m.role === "j" || m.role === "u";
          return (
            <div key={m.id} className={cn("group mb-1.5 flex gap-2", m.role === "u" && "text-hud-green/90", m.role === "log" && "text-hud-med")}>
              <span className="shrink-0 pt-px text-[9px] text-muted-foreground/70">{formatTime(new Date(m.ts).toISOString())}</span>
              <span className={cn("shrink-0 pt-px text-[9px] font-bold tracking-[0.12em]", tag.className)}>{tag.label}</span>
              <span className={cn("min-w-0 flex-1 whitespace-pre-wrap break-words", isChat ? "text-foreground" : "", m.level === "error" && "text-hud-red", m.level === "warning" && m.role !== "u" && "text-hud-amber")}>
                {m.role === "j" && m.typed ? <Typewriter text={m.text} enabled={prefs.typewriter} /> : m.text}
                {m.href && (
                  <Link href={m.href} className="ml-2 inline-block border border-border px-1 text-[9px] uppercase tracking-[0.12em] text-primary hover:bg-hud-ghost">
                    open ↗
                  </Link>
                )}
              </span>
            </div>
          );
        })}
        {interim && (
          <div className="mb-1 flex gap-2 text-hud-green/70">
            <span className="shrink-0 text-[9px]">…</span>
            <span className="shrink-0 text-[9px] font-bold tracking-[0.12em]">YOU</span>
            <span className="italic">{interim}</span>
          </div>
        )}
      </div>

      {/* ---------------------------------------------------------------- command input */}
      <div className="shrink-0 border-t border-border p-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="hud-section border-0 pb-0">COMMAND INPUT</h2>
          <span className={cn("font-mono text-[9px] font-bold tracking-[0.16em]", hudState === "LISTENING" ? "text-hud-green" : hudState === "THINKING" || hudState === "PROCESSING" ? "text-hud-amber" : hudState === "OFFLINE" ? "text-hud-red" : "text-primary")}>
            {muted ? "⊘ MUTED" : `● ${hudState}`}
          </span>
        </div>

        <div className="mb-2 grid grid-cols-2 gap-1.5">
          <button
            type="button"
            onClick={controller.interrupt}
            className={cn("border px-2 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] transition-colors", speaking ? "border-hud-orange text-hud-orange hover:bg-hud-orange/10" : "border-border text-muted-foreground hover:border-primary hover:text-primary")}
          >
            ✋ INTERRUPT <span className="hud-kbd ml-1">ESC</span>
          </button>
          <button
            type="button"
            onClick={() => setMuted(!muted)}
            className={cn("border px-2 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] transition-colors", muted ? "border-hud-red text-hud-red" : "border-border text-hud-green hover:border-hud-green")}
          >
            {muted ? "🔇 VOICE MUTED" : "🔊 VOICE ON"}
          </button>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="flex items-stretch gap-1.5"
        >
          <button
            type="button"
            onPointerDown={(e) => {
              e.preventDefault();
              controller.startListening();
            }}
            onPointerUp={() => controller.stopListening()}
            onPointerLeave={() => listening && controller.stopListening()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                controller.toggleListening();
              }
            }}
            disabled={!voiceSupport.recognition}
            title={voiceSupport.recognition ? "Hold to talk (or Ctrl+Space)" : "Speech recognition not supported in this browser"}
            className={cn(
              "relative w-10 shrink-0 border font-mono text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40",
              listening ? "animate-pulse-ring border-hud-red bg-hud-red/15 text-hud-red" : "border-border text-primary hover:border-primary hover:bg-hud-ghost",
            )}
            aria-pressed={listening}
            aria-label="Hold to talk"
          >
            🎙
          </button>
          <input
            ref={inputRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowUp" && history.length) {
                e.preventDefault();
                const next = Math.min(history.length - 1, historyIndex + 1);
                setHistoryIndex(next);
                setValue(history[next] ?? "");
              } else if (e.key === "ArrowDown") {
                e.preventDefault();
                const next = Math.max(-1, historyIndex - 1);
                setHistoryIndex(next);
                setValue(next === -1 ? "" : (history[next] ?? ""));
              }
            }}
            placeholder={readOnly ? `Ask ${prefs.assistantName} (read-only role)…` : listening ? "Listening…" : `Command ${prefs.assistantName}…`}
            className="hud-input min-w-0 flex-1"
            autoComplete="off"
            spellCheck={false}
            aria-label="Command input"
          />
          <button type="submit" disabled={!value.trim()} className="shrink-0 border border-primary/60 bg-hud-ghost px-3 font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-primary hover:bg-primary hover:text-primary-foreground disabled:opacity-40">
            SEND
          </button>
        </form>

        <div className="mt-2 flex flex-wrap items-center gap-1">
          {QUICK_COMMANDS.map((cmd) => (
            <button key={cmd} type="button" onClick={() => submit(cmd)} className="border border-border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-muted-foreground hover:border-primary hover:text-primary">
              {cmd}
            </button>
          ))}
          <label className={cn("ml-auto flex cursor-pointer items-center gap-1 font-mono text-[9px] uppercase tracking-[0.12em]", prefs.wakeEnabled ? "text-hud-green" : "text-muted-foreground", !voiceSupport.recognition && "opacity-40")}>
            <input type="checkbox" className="h-3 w-3 accent-current" checked={prefs.wakeEnabled} disabled={!voiceSupport.recognition} onChange={(e) => setPrefs({ wakeEnabled: e.target.checked })} />
            wake word
          </label>
        </div>
        <div className="mt-1.5 flex items-center gap-2 font-mono text-[9px] text-muted-foreground/70">
          <span><kbd className="hud-kbd">Ctrl</kbd>+<kbd className="hud-kbd">Space</kbd> hold to talk</span>
          <span><kbd className="hud-kbd">/</kbd> focus</span>
          <span><kbd className="hud-kbd">↑</kbd> history</span>
        </div>
      </div>
    </aside>
  );
}
