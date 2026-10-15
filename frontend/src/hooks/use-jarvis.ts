"use client";

import { useCallback, useEffect, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { realtime } from "@/lib/realtime";
import { toast, useAppStore } from "@/lib/store";
import { consoleLog, consoleSys, useJarvis, type PendingConfirm } from "@/lib/jarvis-store";
import { applyAccent } from "@/lib/theme";
import type { AssistantResponse, OrgEvent } from "@/lib/types";
import { createLevelMeter, createRecognizer, matchWakeWord, speak, stopSpeaking, voiceSupport, type LevelMeter, type Recognizer } from "@/lib/voice";

/** Derive the command context (task / session / project) from the current route. */
export function routeContext(pathname: string): Record<string, unknown> {
  const ctx: Record<string, unknown> = { page: pathname, local_hour: new Date().getHours() };
  const task = pathname.match(/^\/tasks\/([0-9a-f-]{36})/i);
  const session = pathname.match(/^\/sessions\/([0-9a-f-]{36})/i);
  const project = pathname.match(/^\/projects\/([0-9a-f-]{36})/i);
  if (task) ctx.task_id = task[1];
  if (session) ctx.session_id = session[1];
  if (project) ctx.project_id = project[1];
  return ctx;
}

const LOG_EVENTS: Record<string, "info" | "warning" | "error" | "success"> = {
  "task.started": "info",
  "task.paused": "warning",
  "task.resumed": "info",
  "task.attention": "warning",
  "task.completed": "success",
  "task.failed": "error",
  "task.cancelled": "warning",
  "scene.started": "info",
  "scene.completed": "success",
  "scene.failed": "error",
  "scene.skipped": "warning",
  "download.completed": "success",
  "download.failed": "error",
  "human.takeover": "warning",
  "human.released": "info",
  "human.required": "warning",
  "session.updated": "info",
  "file.created": "success",
  "worker.status": "info",
};

/**
 * The JARVIS controller: one instance mounted in the shell. Owns command submission, the
 * confirmation gate, spoken replies, microphone capture (push-to-talk + wake word), the
 * waveform level meter, global shortcuts and the live event → console log bridge.
 */
export function useJarvisController(enabled: boolean) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const auth = useAppStore((s) => s.auth);
  const js = useJarvis;
  const recognizer = useRef<Recognizer | null>(null);
  const wakeRecognizer = useRef<Recognizer | null>(null);
  const meter = useRef<LevelMeter | null>(null);
  const meterRaf = useRef(0);
  const speakAmpTimer = useRef<number | null>(null);
  const pathRef = useRef(pathname);
  pathRef.current = pathname;

  // ---------------------------------------------------------------- speaking
  const say = useCallback(
    (text: string) => {
      const { prefs, muted } = js.getState();
      if (!prefs.ttsEnabled || muted || !voiceSupport.synthesis) return;
      speak(text, {
        voiceURI: prefs.voiceURI,
        rate: prefs.rate,
        onStart: () => {
          js.getState().setSpeaking(true);
          if (speakAmpTimer.current) window.clearInterval(speakAmpTimer.current);
          // Synthetic envelope while the browser speaks (no analyser access to TTS output).
          speakAmpTimer.current = window.setInterval(() => js.getState().setAmp(0.25 + Math.random() * 0.55), 90);
        },
        onEnd: () => {
          if (speakAmpTimer.current) window.clearInterval(speakAmpTimer.current);
          speakAmpTimer.current = null;
          js.getState().setAmp(0);
          js.getState().setSpeaking(false);
        },
      });
    },
    [js],
  );

  const interrupt = useCallback(() => {
    stopSpeaking();
    if (speakAmpTimer.current) window.clearInterval(speakAmpTimer.current);
    speakAmpTimer.current = null;
    js.getState().setAmp(0);
    js.getState().setSpeaking(false);
    js.getState().setPendingConfirm(null);
  }, [js]);

  // ---------------------------------------------------------------- command handling
  const applyResponse = useCallback(
    (res: AssistantResponse, original: string, context: Record<string, unknown>) => {
      const s = js.getState();
      s.push({ role: "j", text: res.reply, typed: s.prefs.typewriter, level: res.ok ? undefined : "warning", href: res.navigate ?? undefined });
      if (res.confirm) {
        const pending: PendingConfirm = { title: res.confirm.title, detail: res.confirm.detail, command: original, context };
        s.setPendingConfirm(pending);
      }
      if (res.client) {
        if (res.client.action === "set_color") applyAccent(res.client.value);
        if (res.client.action === "set_user_name") s.setPrefs({ userName: res.client.value });
        if (res.client.action === "set_assistant_name") s.setPrefs({ assistantName: res.client.value });
      }
      if (res.actions.length) {
        queryClient.invalidateQueries({ queryKey: ["tasks"] });
        queryClient.invalidateQueries({ queryKey: ["sessions"] });
        queryClient.invalidateQueries({ queryKey: ["dashboard"] });
        queryClient.invalidateQueries({ queryKey: ["memory"] });
        if (res.data?.task_id) queryClient.invalidateQueries({ queryKey: [`task:${res.data.task_id}`] });
        if (res.data?.session_id) queryClient.invalidateQueries({ queryKey: [`session:${res.data.session_id}`] });
      }
      if (res.navigate && !res.confirm && (res.ok || res.intent === "open_page")) router.push(res.navigate);
      say(res.reply);
    },
    [js, queryClient, router, say],
  );

  /** Local commands that never need the server (mute / unmute / time / clear / theme reset). */
  const handleLocally = useCallback(
    (text: string): boolean => {
      const s = js.getState();
      const t = text.trim().toLowerCase();
      const who = s.prefs.userName || "sir";
      if (/^(mute|be quiet|silence|shut up|quiet)\b/.test(t)) {
        s.setMuted(true);
        stopSpeaking();
        s.push({ role: "j", text: "Muted. I will keep logging here." });
        return true;
      }
      if (/^(unmute|speak( again)?|voice on)\b/.test(t)) {
        s.setMuted(false);
        s.push({ role: "j", text: `Voice restored, ${who}.` });
        say(`Voice restored, ${who}.`);
        return true;
      }
      if (/^(what time is it|time|what's the time|current time)\??$/.test(t)) {
        const reply = `It is ${new Date().toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}, ${who}.`;
        s.push({ role: "j", text: reply, typed: s.prefs.typewriter });
        say(reply);
        return true;
      }
      if (/^(what('s| is) the date|date today|today's date)\??$/.test(t)) {
        const reply = `${new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}.`;
        s.push({ role: "j", text: reply, typed: s.prefs.typewriter });
        say(reply);
        return true;
      }
      if (/^(clear|cls|clear (the )?(log|console))$/.test(t)) {
        s.clear();
        return true;
      }
      if (/^(reset (the )?(colou?r|theme)|default (colou?r|theme))$/.test(t)) {
        applyAccent("#00d4ff");
        s.push({ role: "j", text: "Default palette restored." });
        return true;
      }
      return false;
    },
    [js, say],
  );

  const send = useCallback(
    async (text: string, { confirmed = false, context: overrideContext, echo = true }: { confirmed?: boolean; context?: Record<string, unknown>; echo?: boolean } = {}) => {
      const value = text.trim();
      if (!value) return;
      const s = js.getState();
      if (echo) s.push({ role: "u", text: value });
      if (!confirmed && handleLocally(value)) return;
      const context = overrideContext ?? routeContext(pathRef.current);
      stopSpeaking();
      s.setState("THINKING");
      try {
        const res = await api.assistant.command(value, context, confirmed);
        applyResponse(res, value, context);
      } catch (error) {
        const message = error instanceof ApiError ? error.message : "The console could not reach the server.";
        s.push({ role: "j", text: `Could not do that: ${message}`, level: "error" });
      } finally {
        const cur = js.getState();
        if (cur.state === "THINKING") cur.setState(cur.listening ? "LISTENING" : cur.speaking ? "SPEAKING" : "ONLINE");
      }
    },
    [applyResponse, handleLocally, js],
  );

  const confirmPending = useCallback(async () => {
    const pending = js.getState().pendingConfirm;
    if (!pending) return;
    js.getState().setPendingConfirm(null);
    js.getState().push({ role: "u", text: "Confirmed." });
    await send(pending.command, { confirmed: true, context: pending.context, echo: false });
  }, [js, send]);

  const cancelPending = useCallback(() => {
    const s = js.getState();
    if (!s.pendingConfirm) return;
    s.setPendingConfirm(null);
    s.push({ role: "j", text: "Cancelled. Nothing was changed." });
  }, [js]);

  // ---------------------------------------------------------------- microphone
  const stopMeter = useCallback(() => {
    cancelAnimationFrame(meterRaf.current);
    meter.current?.stop();
    meter.current = null;
    js.getState().setAmp(0);
  }, [js]);

  const startMeter = useCallback(async () => {
    if (meter.current) return;
    meter.current = await createLevelMeter();
    if (!meter.current) return;
    const loop = () => {
      const m = meter.current;
      if (!m) return;
      if (!js.getState().speaking) js.getState().setAmp(m.read());
      meterRaf.current = requestAnimationFrame(loop);
    };
    loop();
  }, [js]);

  const stopListening = useCallback(() => {
    recognizer.current?.stop();
    recognizer.current = null;
    js.getState().setListening(false);
    js.getState().setInterim("");
    if (!js.getState().prefs.wakeEnabled) stopMeter();
  }, [js, stopMeter]);

  const startListening = useCallback(() => {
    if (!voiceSupport.recognition) {
      toast({ title: "Voice input unavailable", description: "This browser has no speech recognition. Chrome or Edge support it.", variant: "warning" });
      return;
    }
    if (recognizer.current) return;
    stopSpeaking();
    const s = js.getState();
    recognizer.current = createRecognizer({
      continuous: false,
      onInterim: (text) => js.getState().setInterim(text),
      onFinal: (text) => {
        js.getState().setInterim("");
        if (text) void send(text);
      },
      onEnd: () => {
        recognizer.current = null;
        js.getState().setListening(false);
        js.getState().setInterim("");
        if (!js.getState().prefs.wakeEnabled) stopMeter();
        else wakeRecognizer.current?.start();
      },
      onError: (error) => {
        if (error === "not-allowed") toast({ title: "Microphone blocked", description: "Allow microphone access in the browser to talk to the console.", variant: "warning" });
      },
    });
    wakeRecognizer.current?.stop();
    s.setListening(true);
    void startMeter();
    recognizer.current?.start();
  }, [js, send, startMeter, stopMeter]);

  const toggleListening = useCallback(() => {
    if (js.getState().listening) stopListening();
    else startListening();
  }, [js, startListening, stopListening]);

  // Wake word ("Hey JARVIS …") — a continuous, client-side recogniser that only acts on the phrase.
  useEffect(() => {
    if (!enabled) return;
    const sync = () => {
      const { prefs } = js.getState();
      if (prefs.wakeEnabled && !wakeRecognizer.current && voiceSupport.recognition) {
        wakeRecognizer.current = createRecognizer({
          continuous: true,
          onFinal: (text) => {
            const rest = matchWakeWord(text, js.getState().prefs.assistantName);
            if (rest === null) return;
            if (rest) void send(rest);
            else {
              const reply = `Yes, ${js.getState().prefs.userName || "sir"}?`;
              js.getState().push({ role: "j", text: reply });
              say(reply);
              startListening();
            }
          },
          onError: (error) => {
            if (error === "not-allowed") {
              js.getState().setPrefs({ wakeEnabled: false });
              toast({ title: "Wake word disabled", description: "Microphone permission was denied.", variant: "warning" });
            }
          },
        });
        wakeRecognizer.current?.start();
        void startMeter();
        consoleSys(`Wake word armed — say "Hey ${js.getState().prefs.assistantName}".`);
      } else if (!prefs.wakeEnabled && wakeRecognizer.current) {
        wakeRecognizer.current.stop();
        wakeRecognizer.current = null;
        if (!js.getState().listening) stopMeter();
      }
    };
    sync();
    const unsub = js.subscribe((state, prev) => {
      if (state.prefs.wakeEnabled !== prev.prefs.wakeEnabled) sync();
    });
    return () => {
      unsub();
      wakeRecognizer.current?.stop();
      wakeRecognizer.current = null;
    };
  }, [enabled, js, say, send, startListening, startMeter, stopMeter]);

  // ---------------------------------------------------------------- shortcuts
  useEffect(() => {
    if (!enabled) return;
    let held = false;
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing = !!target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (e.code === "Space" && (e.ctrlKey || e.metaKey) && !e.repeat) {
        e.preventDefault();
        held = true;
        startListening();
        return;
      }
      if (e.key === "Escape") {
        interrupt();
        if (js.getState().listening) stopListening();
        return;
      }
      if (!typing && e.key === "/" && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        js.getState().requestFocus();
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space" && held) {
        held = false;
        stopListening();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [enabled, interrupt, js, startListening, stopListening]);

  // ---------------------------------------------------------------- online state + briefing
  useEffect(() => {
    if (!enabled || !auth) {
      js.getState().setState(enabled ? "INITIALISING" : "OFFLINE");
      return;
    }
    const prefs = (auth.user.preferences?.jarvis ?? null) as Record<string, unknown> | null;
    js.getState().hydratePrefs(prefs);
    if (prefs && typeof prefs.ui_color === "string") applyAccent(prefs.ui_color);
    js.getState().setState("ONLINE");
    if (js.getState().briefed) return;
    js.getState().setBriefed(true);
    let cancelled = false;
    api.assistant
      .briefing(new Date().getHours())
      .then((b) => {
        if (cancelled) return;
        js.getState().push({ role: "j", text: b.text, typed: true });
        say(b.text);
      })
      .catch(() => {
        js.getState().push({ role: "sys", text: "Briefing unavailable — the API did not answer.", level: "warning" });
      });
    return () => {
      cancelled = true;
    };
  }, [auth, enabled, js, say]);

  // ---------------------------------------------------------------- connection + realtime events → log
  useEffect(() => {
    if (!enabled) return;
    const client = realtime();
    const offState = client.onState((state) => {
      const s = js.getState();
      if (state === "open" && s.state === "OFFLINE") s.setState("ONLINE");
      if ((state === "closed" || state === "error") && s.state !== "OFFLINE") {
        s.setState("OFFLINE");
        consoleSys("Realtime link lost — reconnecting.", "warning");
      }
    });
    const offEvent = client.onEvent((event: OrgEvent) => {
      const level = LOG_EVENTS[event.type];
      if (!level) return;
      const message = typeof event.payload.message === "string" ? event.payload.message : event.type;
      const href = event.task_id ? `/tasks/${event.task_id}` : event.session_id ? `/sessions/${event.session_id}` : undefined;
      consoleLog(message, (event.payload.level as "info" | "warning" | "error" | undefined) ?? level, href);
      const s = js.getState();
      if (event.type === "task.attention" || event.type === "human.required") {
        const text = `Attention, ${s.prefs.userName || "sir"}: ${message}`;
        s.push({ role: "j", text, href, level: "warning" });
        say(text);
      } else if (event.type === "task.completed") {
        say(message);
      } else if (event.type === "task.failed") {
        say(`Task failed. ${message}`);
      }
      if (event.type === "task.started" || event.type === "scene.started") s.setState("PROCESSING");
      if ((event.type === "task.completed" || event.type === "task.failed" || event.type === "task.cancelled") && s.state === "PROCESSING") s.setState("ONLINE");
    });
    return () => {
      offState();
      offEvent();
    };
  }, [enabled, js, say]);

  // Clean up audio on unmount.
  useEffect(
    () => () => {
      recognizer.current?.stop();
      wakeRecognizer.current?.stop();
      stopMeter();
      stopSpeaking();
    },
    [stopMeter],
  );

  return { send, confirmPending, cancelPending, interrupt, startListening, stopListening, toggleListening, say };
}

export type JarvisController = ReturnType<typeof useJarvisController>;
