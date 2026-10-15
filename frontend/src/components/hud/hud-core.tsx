"use client";

import { useEffect, useRef } from "react";

import { useJarvis, type HudState } from "@/lib/jarvis-store";
import { hudColors, type HudPalette } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * The reactor core — a canvas port of Mark-LV's `HudCanvas._paint_core`.
 *
 * Atmosphere → corner frame marks → crosshair → graduated rings → five counter-rotating arc
 * groups → audio-reactive spike ring → inner ring with the assistant's name → status line →
 * 36-bar waveform. Colours are read from the live CSS palette every frame so the hue wheel
 * retints it instantly; the arc rate rises while THINKING/PROCESSING and while speaking.
 */
export interface HudCoreProps {
  className?: string;
  /** Override the global state (e.g. a task page showing the agent as PROCESSING). */
  state?: HudState;
  statusText?: string;
  compact?: boolean;
  showWave?: boolean;
  showName?: boolean;
}

function hexToRgb(hex: string): [number, number, number] {
  const v = hex.replace("#", "");
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
}

export function HudCore({ className, state: forcedState, statusText, compact = false, showWave = true, showName = true }: HudCoreProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const paletteRef = useRef<HudPalette>(hudColors());

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let raf = 0;
    let phase = 0;
    let tick = 0;
    let ampDisp = 0;
    let last = performance.now();
    let paletteAt = 0;

    const observer = new ResizeObserver(() => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    });
    observer.observe(canvas);

    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (now - paletteAt > 500) {
        paletteRef.current = hudColors();
        paletteAt = now;
      }
      const P = paletteRef.current;
      const js = useJarvis.getState();
      const state = forcedState ?? js.state;
      const speaking = js.speaking;
      const muted = js.muted;
      const targetAmp = js.amp;
      ampDisp += (targetAmp - ampDisp) * (targetAmp > ampDisp ? 0.45 : 0.12);
      const amp = ampDisp;
      const thinking = state === "THINKING" || state === "PROCESSING";
      const rate = 1 + (thinking ? 1.9 : 0) + (speaking ? 1.2 : 0);
      phase += dt * rate * 0.9;
      tick += 1;

      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const W = canvas.width / dpr;
      const H = canvas.height / dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      const statusH = compact ? 18 : 26;
      const waveH = showWave ? 34 : 0;
      const bandT = 8;
      const bandH = Math.max(40, H - statusH - waveH - bandT - 6);
      const cx = W / 2;
      const cy = bandT + bandH / 2;
      const r = Math.min(W * 0.46, bandH / 2);

      const [br, bg, bb] = hexToRgb(P.bg);
      const main = hexToRgb(muted ? P.muted : P.pri);
      const accHex = muted ? P.muted : speaking ? P.acc : thinking ? P.acc2 : state === "LISTENING" ? P.green : P.pri;
      const acc = hexToRgb(accHex);
      const blend = (col: [number, number, number], a: number) => {
        const k = Math.max(0, Math.min(1, a));
        return `rgb(${Math.round(br + (col[0] - br) * k)},${Math.round(bg + (col[1] - bg) * k)},${Math.round(bb + (col[2] - bb) * k)})`;
      };
      const rgba = (col: [number, number, number], a: number) => `rgba(${col[0]},${col[1]},${col[2]},${Math.max(0, Math.min(1, a))})`;
      const live = (speaking || amp > 0.04) && !muted;

      // 1. atmosphere
      const lift = 1 + 0.55 * amp + (speaking ? 0.18 : 0);
      for (const [gr, a0] of [
        [r * 0.7, 0.3],
        [r * 0.34, 0.34],
      ] as const) {
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, gr);
        g.addColorStop(0, rgba(main, Math.min(0.95, a0 * lift)));
        g.addColorStop(0.45, rgba(main, Math.min(0.95, a0 * lift * 0.52)));
        g.addColorStop(0.78, rgba(main, Math.min(0.95, a0 * lift * 0.18)));
        g.addColorStop(1, rgba(main, 0));
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(cx, cy, gr, 0, Math.PI * 2);
        ctx.fill();
      }

      // 2. corner frame marks
      if (W > 40 && bandH > 40 && !compact) {
        const m = Math.min(W, bandH) * 0.035;
        const arm = Math.min(W, bandH) * 0.055;
        ctx.strokeStyle = blend(main, 0.45);
        ctx.lineWidth = 1.4;
        for (const [sx, sy] of [
          [1, 1],
          [-1, 1],
          [1, -1],
          [-1, -1],
        ]) {
          const x = cx + sx * (W / 2 - m);
          const y = cy + sy * (bandH / 2 - m);
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(x - sx * arm, y);
          ctx.moveTo(x, y);
          ctx.lineTo(x, y - sy * arm);
          ctx.stroke();
        }
      }

      // 3. crosshair broken around the core
      ctx.strokeStyle = blend(main, 0.16);
      ctx.lineWidth = 1;
      const gap = r * 0.62;
      ctx.beginPath();
      ctx.moveTo(0, cy);
      ctx.lineTo(cx - gap, cy);
      ctx.moveTo(cx + gap, cy);
      ctx.lineTo(W, cy);
      ctx.moveTo(cx, bandT);
      ctx.lineTo(cx, cy - gap);
      ctx.moveTo(cx, cy + gap);
      ctx.lineTo(cx, bandT + bandH);
      ctx.stroke();

      // 4. graduated outer rings
      for (const [rr, a] of [
        [1.0, 0.34],
        [0.93, 0.16],
      ] as const) {
        ctx.strokeStyle = blend(main, a);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(cx, cy, r * rr, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.strokeStyle = blend(main, 0.42);
      ctx.lineWidth = 1.3;
      for (let i = 0; i < 72; i += 3) {
        const a = (i * 5 * Math.PI) / 180;
        ctx.moveTo(cx + Math.cos(a) * r * 0.885, cy + Math.sin(a) * r * 0.885);
        ctx.lineTo(cx + Math.cos(a) * r * 0.985, cy + Math.sin(a) * r * 0.985);
      }
      ctx.stroke();
      ctx.beginPath();
      ctx.strokeStyle = blend(main, 0.18);
      ctx.lineWidth = 1;
      for (let i = 0; i < 72; i++) {
        if (i % 3 === 0) continue;
        const a = (i * 5 * Math.PI) / 180;
        ctx.moveTo(cx + Math.cos(a) * r * 0.945, cy + Math.sin(a) * r * 0.945);
        ctx.lineTo(cx + Math.cos(a) * r * 0.985, cy + Math.sin(a) * r * 0.985);
      }
      ctx.stroke();

      // 5. rotating arc groups
      const groups: [number, number, number, number, [number, number, number], number, number][] = [
        [0.955, 118, 2, 1, acc, 0.75, 2.0],
        [0.845, 82, 3, -1, main, 0.38, 1.3],
        [0.76, 150, 1, 1, acc, 0.45, 1.6],
        [0.66, 64, 4, -1, main, 0.26, 1.1],
        [0.545, 128, 2, 1, main, 0.3, 1.2],
      ];
      groups.forEach(([rr, span, count, dirn, col, a, wid], k) => {
        const rad = r * rr;
        ctx.strokeStyle = blend(col, a);
        ctx.lineWidth = wid;
        const base = ((phase * (9 + k * 6) * dirn * 10) % 360) * (Math.PI / 180);
        for (let s = 0; s < count; s++) {
          const start = base + (s * (Math.PI * 2)) / count;
          ctx.beginPath();
          ctx.arc(cx, cy, rad, start, start + (span * Math.PI) / 180);
          ctx.stroke();
        }
      });

      // 6. audio-reactive spike ring
      const n = 60;
      const ring = r * 0.415;
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const a = (i * (Math.PI * 2)) / n;
        const wob = 0.5 + 0.5 * Math.sin(phase * 2.3 + i * 0.42);
        const idle = 0.018 + 0.012 * Math.sin(phase * 1.2 + i * 0.7);
        const h = r * (idle + (live ? amp * 0.2 * wob : 0));
        ctx.moveTo(cx + Math.cos(a) * ring, cy + Math.sin(a) * ring);
        ctx.lineTo(cx + Math.cos(a) * (ring + h), cy + Math.sin(a) * (ring + h));
      }
      ctx.strokeStyle = blend(live ? acc : main, 0.25 + 0.5 * amp);
      ctx.lineWidth = 1.6;
      ctx.stroke();

      // 7. inner ring + name
      const inner = r * 0.355;
      ctx.strokeStyle = blend(acc, 0.3 + 0.45 * amp);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cx, cy, inner, 0, Math.PI * 2);
      ctx.stroke();
      const name = showName ? js.prefs.assistantName.toUpperCase() : "";
      if (name && r > 30) {
        const space = Math.max(1, r * 0.018);
        const fsz = Math.max(8, Math.min(r * 0.105, ((inner * 1.75) / Math.max(1, name.length)) * 1.6 - space));
        ctx.font = `bold ${fsz}px ui-monospace, Menlo, Consolas, "Courier New", monospace`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = blend(hexToRgb(P.white), 0.6 + 0.4 * Math.min(1, amp * 2));
        ctx.letterSpacing = `${space}px`;
        ctx.fillText(name, cx, cy);
        ctx.letterSpacing = "0px";
      }

      // 8. status text
      const sy = bandT + bandH + 4;
      const blink = Math.floor(tick / 30) % 2 === 0;
      let txt: string;
      let col: string;
      if (muted) {
        txt = "⊘  MUTED";
        col = P.muted;
      } else if (speaking) {
        txt = "●  SPEAKING";
        col = P.acc;
      } else if (state === "THINKING") {
        txt = `${blink ? "◈" : "◇"}  THINKING`;
        col = P.acc2;
      } else if (state === "PROCESSING") {
        txt = `${blink ? "▷" : "▶"}  PROCESSING`;
        col = P.acc2;
      } else if (state === "LISTENING") {
        txt = `${blink ? "●" : "○"}  LISTENING`;
        col = P.green;
      } else {
        txt = `${blink ? "●" : "○"}  ${state}`;
        col = state === "OFFLINE" ? P.red : P.pri;
      }
      if (statusText) txt = `${txt}  ·  ${statusText}`;
      ctx.font = `bold ${compact ? 10 : 12}px ui-monospace, Menlo, Consolas, "Courier New", monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.letterSpacing = "2px";
      ctx.fillStyle = col;
      ctx.fillText(txt, cx, sy + statusH / 2);
      ctx.letterSpacing = "0px";

      // 9. waveform
      if (showWave) {
        const wy = sy + statusH + 2;
        const N = Math.min(36, Math.floor(W / 8));
        const bw = 8;
        const wx0 = (W - N * bw) / 2;
        const mid = (N - 1) / 2;
        for (let i = 0; i < N; i++) {
          let hgt: number;
          let cl: string;
          if (muted) {
            hgt = 2;
            cl = P.muted;
          } else {
            const env = Math.pow(1 - Math.abs(i - mid) / mid, 0.7);
            const shimmer = 0.55 + 0.45 * Math.sin(tick * 0.18 + i * 0.7);
            const idle = 3 + 2 * Math.sin(tick * 0.09 + i * 0.6);
            hgt = Math.max(2, Math.min(24, idle + amp * 22 * env * shimmer));
            cl = amp > 0.05 ? (hgt > 12 ? P.pri : P.priDim) : P.borderB;
          }
          ctx.fillStyle = cl;
          ctx.fillRect(wx0 + i * bw, wy + 24 - hgt, bw - 1, hgt);
        }
      }
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [forcedState, statusText, compact, showWave, showName]);

  return <canvas ref={canvasRef} className={cn("block h-full w-full", className)} aria-label="HUD core" />;
}
