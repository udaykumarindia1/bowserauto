"use client";

import { create } from "zustand";

/** HUD states mirror Mark-LV: the reactor core, status line and waveform all key off this. */
export type HudState = "INITIALISING" | "ONLINE" | "LISTENING" | "THINKING" | "PROCESSING" | "SPEAKING" | "OFFLINE";

export type ConsoleRole = "j" | "u" | "sys" | "log";

export interface ConsoleMessage {
  id: number;
  role: ConsoleRole;
  text: string;
  ts: number;
  level?: "info" | "warning" | "error" | "success";
  /** Optional navigation target rendered as a link chip. */
  href?: string;
  /** Typewriter effect for assistant replies (Mark-LV LogWidget behaviour). */
  typed?: boolean;
}

export interface PendingConfirm {
  title: string;
  detail: string;
  command: string;
  context?: Record<string, unknown>;
}

export interface JarvisPrefs {
  assistantName: string;
  userName: string;
  voiceURI: string | null;
  ttsEnabled: boolean;
  wakeEnabled: boolean;
  typewriter: boolean;
  hudStyle: "core" | "minimal";
  rate: number;
}

const PREFS_KEY = "ba.jarvis";
/** Former default names: a stored value equal to one of these is treated as "not customised". */
const LEGACY_DEFAULT_NAMES = new Set(["JARVIS"]);

export const DEFAULT_PREFS: JarvisPrefs = {
  assistantName: "AVINASH",
  userName: "",
  voiceURI: null,
  ttsEnabled: true,
  wakeEnabled: false,
  typewriter: true,
  hudStyle: "core",
  rate: 1.02,
};

function loadPrefs(): JarvisPrefs {
  if (typeof window === "undefined") return DEFAULT_PREFS;
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (!raw) return DEFAULT_PREFS;
    const stored = JSON.parse(raw) as Partial<JarvisPrefs>;
    if (typeof stored.assistantName === "string" && LEGACY_DEFAULT_NAMES.has(stored.assistantName.trim())) delete stored.assistantName;
    return { ...DEFAULT_PREFS, ...stored };
  } catch {
    return DEFAULT_PREFS;
  }
}

interface JarvisState {
  state: HudState;
  setState: (state: HudState) => void;
  speaking: boolean;
  setSpeaking: (speaking: boolean) => void;
  listening: boolean;
  setListening: (listening: boolean) => void;
  muted: boolean;
  setMuted: (muted: boolean) => void;
  amp: number;
  setAmp: (amp: number) => void;
  interim: string;
  setInterim: (text: string) => void;
  messages: ConsoleMessage[];
  push: (message: Omit<ConsoleMessage, "id" | "ts"> & { ts?: number }) => number;
  clear: () => void;
  pendingConfirm: PendingConfirm | null;
  setPendingConfirm: (confirm: PendingConfirm | null) => void;
  consoleOpen: boolean;
  setConsoleOpen: (open: boolean) => void;
  monitorOpen: boolean;
  setMonitorOpen: (open: boolean) => void;
  prefs: JarvisPrefs;
  setPrefs: (patch: Partial<JarvisPrefs>) => void;
  hydratePrefs: (remote?: Partial<Record<string, unknown>> | null) => void;
  briefed: boolean;
  setBriefed: (value: boolean) => void;
  focusInput: number;
  requestFocus: () => void;
}

let seq = 1;

export const useJarvis = create<JarvisState>((set, get) => ({
  state: "INITIALISING",
  setState: (state) => set({ state }),
  speaking: false,
  setSpeaking: (speaking) => set({ speaking, state: speaking ? "SPEAKING" : get().listening ? "LISTENING" : "ONLINE" }),
  listening: false,
  setListening: (listening) => set({ listening, state: listening ? "LISTENING" : get().speaking ? "SPEAKING" : "ONLINE" }),
  muted: false,
  setMuted: (muted) => set({ muted }),
  amp: 0,
  setAmp: (amp) => set({ amp }),
  interim: "",
  setInterim: (interim) => set({ interim }),
  messages: [],
  push: (message) => {
    const id = seq++;
    set((s) => ({ messages: [...s.messages.slice(-399), { ...message, id, ts: message.ts ?? Date.now() }] }));
    return id;
  },
  clear: () => set({ messages: [] }),
  pendingConfirm: null,
  setPendingConfirm: (pendingConfirm) => set({ pendingConfirm }),
  consoleOpen: false,
  setConsoleOpen: (consoleOpen) => {
    if (typeof window !== "undefined") window.localStorage.setItem("ba.console", consoleOpen ? "1" : "0");
    set({ consoleOpen });
  },
  monitorOpen: false,
  setMonitorOpen: (monitorOpen) => {
    if (typeof window !== "undefined") window.localStorage.setItem("ba.monitor", monitorOpen ? "1" : "0");
    set({ monitorOpen });
  },
  prefs: DEFAULT_PREFS,
  setPrefs: (patch) => {
    const prefs = { ...get().prefs, ...patch };
    if (typeof window !== "undefined") window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    set({ prefs });
  },
  hydratePrefs: (remote) => {
    const local = loadPrefs();
    const merged: JarvisPrefs = { ...local };
    if (remote) {
      if (typeof remote.assistant_name === "string" && remote.assistant_name && !LEGACY_DEFAULT_NAMES.has(remote.assistant_name.trim())) merged.assistantName = remote.assistant_name;
      if (typeof remote.user_name === "string") merged.userName = remote.user_name;
      if (typeof remote.voice_uri === "string" || remote.voice_uri === null) merged.voiceURI = (remote.voice_uri as string | null) ?? merged.voiceURI;
      if (typeof remote.tts_enabled === "boolean") merged.ttsEnabled = remote.tts_enabled;
      if (typeof remote.wake_enabled === "boolean") merged.wakeEnabled = remote.wake_enabled;
      if (remote.hud_style === "core" || remote.hud_style === "minimal") merged.hudStyle = remote.hud_style;
    }
    const savedConsole = typeof window !== "undefined" ? window.localStorage.getItem("ba.console") : null;
    const consoleOpen = savedConsole === null
      ? (typeof window !== "undefined" && window.matchMedia("(min-width: 1280px)").matches)
      : savedConsole === "1";
    const monitorOpen = typeof window !== "undefined" ? window.localStorage.getItem("ba.monitor") === "1" : false;
    set({ prefs: merged, consoleOpen, monitorOpen });
  },
  briefed: false,
  setBriefed: (briefed) => set({ briefed }),
  focusInput: 0,
  requestFocus: () => set((s) => ({ focusInput: s.focusInput + 1, consoleOpen: true })),
}));

/** Convenience for non-React code (realtime handlers) to append console lines. */
export const consoleLog = (text: string, level: ConsoleMessage["level"] = "info", href?: string) => useJarvis.getState().push({ role: "log", text, level, href });
export const consoleSys = (text: string, level: ConsoleMessage["level"] = "info") => useJarvis.getState().push({ role: "sys", text, level });
