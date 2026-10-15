"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { HueWheel } from "@/components/hud/hue-wheel";
import { useJarvisConsole } from "@/components/hud/jarvis-provider";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useAuth } from "@/hooks/use-auth";
import { api } from "@/lib/api";
import { DEFAULT_PREFS, useJarvis } from "@/lib/jarvis-store";
import { toast } from "@/lib/store";
import { applyAccent, currentAccent, DEFAULT_ACCENT, normalizeHex } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { speak, voiceSupport, voicesReady } from "@/lib/voice";

/**
 * "⚙ CUSTOMISE ASSISTANT" — Mark-LV CustomizeOverlay: assistant name, operator name, voice,
 * HUD colour (hue wheel + hex), HUD style. Saved to the user's preferences on the server and
 * mirrored to localStorage so the look applies before the API answers.
 */
export function CustomizeOverlay() {
  const [open, setOpen] = useState(false);
  const prefs = useJarvis((s) => s.prefs);
  const setPrefs = useJarvis((s) => s.setPrefs);
  const { user, refresh } = useAuth();
  const controller = useJarvisConsole();
  const queryClient = useQueryClient();
  const [assistantName, setAssistantName] = useState(prefs.assistantName);
  const [userName, setUserName] = useState(prefs.userName);
  const [voiceURI, setVoiceURI] = useState<string | null>(prefs.voiceURI);
  const [tts, setTts] = useState(prefs.ttsEnabled);
  const [typewriter, setTypewriter] = useState(prefs.typewriter);
  const [hudStyle, setHudStyle] = useState(prefs.hudStyle);
  const [rate, setRate] = useState(prefs.rate);
  const [hex, setHex] = useState(DEFAULT_ACCENT);
  const [originalHex, setOriginalHex] = useState(DEFAULT_ACCENT);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);

  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener("ba:customize", onOpen);
    return () => window.removeEventListener("ba:customize", onOpen);
  }, []);

  useEffect(() => {
    if (!open) return;
    setAssistantName(prefs.assistantName);
    setUserName(prefs.userName);
    setVoiceURI(prefs.voiceURI);
    setTts(prefs.ttsEnabled);
    setTypewriter(prefs.typewriter);
    setHudStyle(prefs.hudStyle);
    setRate(prefs.rate);
    const accent = currentAccent();
    setHex(accent);
    setOriginalHex(accent);
    void voicesReady().then((list) => setVoices(list.filter((v) => /^en/i.test(v.lang)).concat(list.filter((v) => !/^en/i.test(v.lang)))));
  }, [open, prefs]);

  const save = useMutation({
    mutationFn: async () => {
      const name = assistantName.trim().slice(0, 30) || "AVINASH";
      const who = userName.trim().slice(0, 40);
      const colour = normalizeHex(hex) ?? DEFAULT_ACCENT;
      applyAccent(colour);
      setPrefs({ assistantName: name, userName: who, voiceURI, ttsEnabled: tts, typewriter, hudStyle, rate });
      const jarvis = { ...((user?.preferences?.jarvis as Record<string, unknown>) ?? {}), assistant_name: name, user_name: who, voice_uri: voiceURI, tts_enabled: tts, hud_style: hudStyle, ui_color: colour };
      await api.auth.updateMe({ preferences: { ...(user?.preferences ?? {}), jarvis } });
      return name;
    },
    onSuccess: (name) => {
      toast({ title: "Assistant updated", description: `${name} will use the new settings.`, variant: "success" });
      queryClient.invalidateQueries({ queryKey: ["me"] });
      void refresh();
      setOpen(false);
    },
    onError: (e: Error) => toast({ title: "Could not save", description: e.message, variant: "destructive" }),
  });

  const cancel = () => {
    applyAccent(originalHex);
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => (o ? setOpen(true) : cancel())}>
      <DialogContent wide>
        <DialogHeader>
          <DialogTitle className="hud-title">⚙ Customise assistant</DialogTitle>
          <DialogDescription>Name, voice and the HUD colour. Colour changes preview live; Save keeps them.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-5 md:grid-cols-2">
          <div className="space-y-4">
            <label className="block space-y-1">
              <span className="hud-label">Assistant name</span>
              <input className="hud-input" value={assistantName} maxLength={30} onChange={(e) => setAssistantName(e.target.value)} placeholder="AVINASH" />
            </label>
            <label className="block space-y-1">
              <span className="hud-label">Your name (leave blank for &ldquo;sir&rdquo;)</span>
              <input className="hud-input" value={userName} maxLength={40} onChange={(e) => setUserName(e.target.value)} placeholder="sir" />
            </label>
            <div className="space-y-1">
              <span className="hud-label">Assistant voice</span>
              {voiceSupport.synthesis ? (
                <div className="flex gap-1.5">
                  <select className="hud-input flex-1" value={voiceURI ?? ""} onChange={(e) => setVoiceURI(e.target.value || null)}>
                    <option value="">Auto (best English voice)</option>
                    {voices.map((v) => (
                      <option key={v.voiceURI} value={v.voiceURI}>
                        {v.name} · {v.lang}
                      </option>
                    ))}
                  </select>
                  <button type="button" className="border border-border px-2 font-mono text-[10px] uppercase tracking-[0.12em] text-primary hover:bg-hud-ghost" onClick={() => speak(`${assistantName || "AVINASH"} online, ${userName || "sir"}. Systems nominal.`, { voiceURI, rate })}>
                    Test
                  </button>
                </div>
              ) : (
                <p className="text-[11px] text-muted-foreground">This browser cannot synthesise speech; replies stay text-only.</p>
              )}
              <label className="flex items-center gap-2 pt-1 text-[11px]">
                <span className="hud-label">Rate</span>
                <input type="range" min={0.7} max={1.4} step={0.02} value={rate} onChange={(e) => setRate(Number(e.target.value))} className="flex-1" />
                <span className="w-10 text-right font-mono text-[10px]">{rate.toFixed(2)}×</span>
              </label>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="flex items-center gap-2 border border-border px-2 py-1.5 text-[11px]">
                <input type="checkbox" checked={tts} onChange={(e) => setTts(e.target.checked)} className="accent-current" /> Spoken replies
              </label>
              <label className="flex items-center gap-2 border border-border px-2 py-1.5 text-[11px]">
                <input type="checkbox" checked={typewriter} onChange={(e) => setTypewriter(e.target.checked)} className="accent-current" /> Typewriter log
              </label>
            </div>
            <div className="space-y-1">
              <span className="hud-label">HUD centrepiece</span>
              <div className="grid grid-cols-2 gap-2">
                {(["core", "minimal"] as const).map((style) => (
                  <button key={style} type="button" onClick={() => setHudStyle(style)} className={cn("border px-2 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em]", hudStyle === style ? "border-primary bg-hud-ghost text-primary" : "border-border text-muted-foreground hover:text-primary")}>
                    {style === "core" ? "Reactor core" : "Minimal"}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="hud-label">UI colour — drag the handle</span>
              <button
                type="button"
                className="font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground hover:text-primary"
                onClick={() => {
                  setHex(DEFAULT_ACCENT);
                  applyAccent(DEFAULT_ACCENT, { persist: false });
                }}
              >
                Default
              </button>
            </div>
            <div className="flex justify-center">
              <HueWheel
                value={hex}
                size={176}
                onPick={(h) => {
                  setHex(h);
                  applyAccent(h, { persist: false });
                }}
                onCommit={(h) => {
                  setHex(h);
                  applyAccent(h, { persist: false });
                }}
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="h-7 w-7 border border-border" style={{ background: normalizeHex(hex) ?? DEFAULT_ACCENT }} />
              <input
                className="hud-input flex-1"
                value={hex}
                onChange={(e) => {
                  setHex(e.target.value);
                  const normalized = normalizeHex(e.target.value);
                  if (normalized) applyAccent(normalized, { persist: false });
                }}
                placeholder="#00d4ff"
                maxLength={7}
              />
            </div>
            <div className="grid grid-cols-6 gap-1.5">
              {["#00d4ff", "#2f7bff", "#a855f7", "#ff3355", "#ff6b00", "#00ff88", "#ffcc00", "#ff4fa3", "#9aa4ad", "#14b8a6", "#f97316", "#d8f8ff"].map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-label={c}
                  onClick={() => {
                    setHex(c);
                    applyAccent(c, { persist: false });
                  }}
                  className={cn("h-6 border", hex.toLowerCase() === c ? "border-foreground" : "border-border")}
                  style={{ background: c }}
                />
              ))}
            </div>
            <p className="text-[10px] leading-relaxed text-muted-foreground">
              Status colours (orange, amber, green, red) never change so failures always read red. You can also say &ldquo;change colour to purple&rdquo; in the console.
            </p>
          </div>
        </div>
        <div className="mt-4 flex items-center justify-between gap-2 border-t border-border pt-3">
          <button
            type="button"
            className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground hover:text-hud-red"
            onClick={() => {
              setAssistantName(DEFAULT_PREFS.assistantName);
              setUserName("");
              setVoiceURI(null);
              setHex(DEFAULT_ACCENT);
              applyAccent(DEFAULT_ACCENT, { persist: false });
            }}
          >
            Reset all
          </button>
          <div className="flex gap-2">
            <button type="button" onClick={cancel} className="border border-border px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground hover:text-primary">
              Cancel
            </button>
            <button type="button" onClick={() => save.mutate()} disabled={save.isPending} className="border border-primary bg-hud-ghost px-4 py-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-primary hover:bg-primary hover:text-primary-foreground disabled:opacity-50">
              {save.isPending ? "Saving…" : "Save"}
            </button>
            <button type="button" onClick={() => controller.say(`Testing voice, ${userName || "sir"}.`)} className="hidden" aria-hidden />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
