"use client";

/**
 * JARVIS HUD palette engine (ported from Mark-LV's `apply_ui_accent`).
 *
 * The whole teal-family palette is expressed in CSS as `hsl(calc(var(--hue) + delta) S L)`.
 * Re-theming therefore only needs two custom properties on `<html>`:
 *   --hue  the accent hue in degrees (default 190 = #00d4ff)
 *   --sat  a saturation multiplier (near-grey accents desaturate the entire HUD)
 * Status colours (orange / amber / green / red) are fixed and never rotate, exactly like the
 * desktop HUD, so "failed" always reads red regardless of the chosen theme.
 */

export const DEFAULT_ACCENT = "#00d4ff";
export const BASE_HUE = 190;
const STORAGE_KEY = "ba.accent";

export interface Hsl {
  h: number;
  s: number;
  l: number;
}

export function normalizeHex(input: string): string | null {
  const value = input.trim().toLowerCase().replace(/^#/, "");
  if (/^[0-9a-f]{6}$/.test(value)) return `#${value}`;
  if (/^[0-9a-f]{3}$/.test(value)) return `#${value.split("").map((c) => c + c).join("")}`;
  return null;
}

export function hexToHsl(hex: string): Hsl {
  const value = normalizeHex(hex) ?? DEFAULT_ACCENT;
  const r = parseInt(value.slice(1, 3), 16) / 255;
  const g = parseInt(value.slice(3, 5), 16) / 255;
  const b = parseInt(value.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  let h = 0;
  let s = 0;
  if (d > 0) {
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r:
        h = ((g - b) / d + (g < b ? 6 : 0)) * 60;
        break;
      case g:
        h = ((b - r) / d + 2) * 60;
        break;
      default:
        h = ((r - g) / d + 4) * 60;
    }
  }
  return { h: Math.round(h), s: Math.round(s * 100), l: Math.round(l * 100) };
}

export function hslToHex(h: number, s: number, l: number): string {
  const sat = s / 100;
  const light = l / 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = sat * Math.min(light, 1 - light);
  const f = (n: number) => light - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  const toHex = (v: number) =>
    Math.round(v * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${toHex(f(0))}${toHex(f(8))}${toHex(f(4))}`;
}

/** Pure hue → vivid accent hex (what the hue wheel handle shows). */
export function hueToHex(hue: number): string {
  return hslToHex(((hue % 360) + 360) % 360, 100, 50);
}

/** Apply an accent colour to the document; returns the normalised hex or null when invalid. */
export function applyAccent(hex: string, { persist = true }: { persist?: boolean } = {}): string | null {
  const normalized = normalizeHex(hex);
  if (!normalized || typeof document === "undefined") return null;
  const { h, s } = hexToHsl(normalized);
  const root = document.documentElement;
  const grey = s < 8;
  root.style.setProperty("--hue", String(grey ? BASE_HUE : h));
  root.style.setProperty("--sat", grey ? "0.15" : "1");
  root.style.setProperty("--accent-hex", normalized);
  root.dataset.accent = normalized;
  if (persist) window.localStorage.setItem(STORAGE_KEY, normalized);
  return normalized;
}

export function storedAccent(): string {
  if (typeof window === "undefined") return DEFAULT_ACCENT;
  return window.localStorage.getItem(STORAGE_KEY) ?? DEFAULT_ACCENT;
}

export function currentAccent(): string {
  if (typeof document === "undefined") return DEFAULT_ACCENT;
  return document.documentElement.dataset.accent ?? storedAccent();
}

/** Read a palette colour from CSS so canvas drawing follows the live theme. */
export function cssVar(name: string, fallback = "#00d4ff"): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** Resolve the HUD colours used by canvas components (reactor core, waveform, hue wheel). */
export function hudColors() {
  const hue = Number(cssVar("--hue", String(BASE_HUE))) || BASE_HUE;
  const sat = Number(cssVar("--sat", "1")) || 1;
  const light = typeof document !== "undefined" && !document.documentElement.classList.contains("dark");
  const c = (dh: number, s: number, l: number) => hslToHex(((hue + dh) % 360 + 360) % 360, Math.min(100, s * sat), l);
  return {
    bg: light ? c(14, 40, 97) : c(14, 100, 2),
    panel: light ? c(12, 40, 99) : c(12, 90, 4),
    border: light ? c(11, 45, 78) : c(11, 69, 16),
    borderB: light ? c(9, 55, 60) : c(9, 65, 29),
    pri: light ? c(0, 100, 32) : c(0, 100, 50),
    priDim: light ? c(2, 80, 45) : c(2, 100, 30),
    ghost: light ? c(10, 60, 90) : c(10, 100, 9),
    text: light ? c(-8, 60, 18) : c(-8, 100, 78),
    textDim: light ? c(0, 30, 40) : c(0, 45, 42),
    white: light ? c(1, 80, 10) : c(1, 100, 92),
    acc: "#ff6b00",
    acc2: light ? "#b45309" : "#ffcc00",
    green: light ? "#059669" : "#00ff88",
    red: "#ff3355",
    muted: "#ff3366",
  };
}

export type HudPalette = ReturnType<typeof hudColors>;
