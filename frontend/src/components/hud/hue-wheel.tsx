"use client";

import { useEffect, useRef, useState } from "react";

import { hexToHsl, hueToHex } from "@/lib/theme";
import { cn } from "@/lib/utils";

/**
 * Conic hue wheel (Mark-LV HueWheel): drag the handle around the ring to pick any hue; the
 * centre disc previews the colour. `onPick` fires continuously while dragging (live retint),
 * `onCommit` once on release.
 */
export function HueWheel({ value, onPick, onCommit, size = 160, className }: { value: string; onPick: (hex: string) => void; onCommit: (hex: string) => void; size?: number; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hue, setHue] = useState(() => hexToHsl(value).h);
  const dragging = useRef(false);

  useEffect(() => {
    setHue(hexToHsl(value).h);
  }, [value]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    const cx = size / 2;
    const cy = size / 2;
    const outer = size / 2 - 4;
    const ringW = size * 0.14;
    // ring
    for (let i = 0; i < 360; i++) {
      const a0 = ((i - 90) * Math.PI) / 180;
      const a1 = ((i + 1.5 - 90) * Math.PI) / 180;
      ctx.beginPath();
      ctx.arc(cx, cy, outer - ringW / 2, a0, a1);
      ctx.lineWidth = ringW;
      ctx.strokeStyle = `hsl(${i} 100% 50%)`;
      ctx.stroke();
    }
    // inner preview disc
    ctx.beginPath();
    ctx.arc(cx, cy, outer - ringW - 10, 0, Math.PI * 2);
    ctx.fillStyle = hueToHex(hue);
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.stroke();
    // handle
    const ang = ((hue - 90) * Math.PI) / 180;
    const hr = outer - ringW / 2;
    ctx.beginPath();
    ctx.arc(cx + Math.cos(ang) * hr, cy + Math.sin(ang) * hr, ringW * 0.42, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#00060a";
    ctx.stroke();
  }, [hue, size]);

  const hueFromEvent = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left - rect.width / 2;
    const y = e.clientY - rect.top - rect.height / 2;
    const deg = (Math.atan2(y, x) * 180) / Math.PI + 90;
    return ((Math.round(deg) % 360) + 360) % 360;
  };

  return (
    <canvas
      ref={canvasRef}
      role="slider"
      aria-label="Hue"
      aria-valuemin={0}
      aria-valuemax={359}
      aria-valuenow={hue}
      tabIndex={0}
      style={{ width: size, height: size, touchAction: "none" }}
      className={cn("cursor-crosshair select-none", className)}
      onPointerDown={(e) => {
        dragging.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        const h = hueFromEvent(e);
        setHue(h);
        onPick(hueToHex(h));
      }}
      onPointerMove={(e) => {
        if (!dragging.current) return;
        const h = hueFromEvent(e);
        setHue(h);
        onPick(hueToHex(h));
      }}
      onPointerUp={(e) => {
        if (!dragging.current) return;
        dragging.current = false;
        const h = hueFromEvent(e);
        setHue(h);
        onCommit(hueToHex(h));
      }}
      onKeyDown={(e) => {
        const delta = e.key === "ArrowRight" || e.key === "ArrowUp" ? 5 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -5 : 0;
        if (!delta) return;
        e.preventDefault();
        const h = (((hue + delta) % 360) + 360) % 360;
        setHue(h);
        onPick(hueToHex(h));
        onCommit(hueToHex(h));
      }}
    />
  );
}
