"use client";

import { useQuery } from "@tanstack/react-query";

import { HudBadge, MetricBar } from "@/components/hud/hud-primitives";
import { api } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import { useJarvis } from "@/lib/jarvis-store";
import { formatBytes, formatDuration } from "@/lib/utils";
import { cn } from "@/lib/utils";

const APP_VERSION = "v1.0";

function rate(bps: number | null | undefined): string {
  if (bps == null) return "--";
  if (bps < 1024) return `${Math.round(bps)} B/s`;
  if (bps < 1024 * 1024) return `${(bps / 1024).toFixed(0)} KB/s`;
  return `${(bps / 1024 / 1024).toFixed(1)} MB/s`;
}

/** "◈ SYS MONITOR" — host telemetry bars + runtime badges (ported from the Mark-LV left panel). */
export function SysMonitor({ className }: { className?: string }) {
  const connection = useAppStore((s) => s.connection);
  const hudState = useJarvis((s) => s.state);
  const { data, isError } = useQuery({ queryKey: ["system-metrics"], queryFn: api.assistant.system, refetchInterval: 3000, staleTime: 2500 });
  const { data: dashboard } = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 15000 });

  const net = data ? ((data.network.rx_bps ?? 0) + (data.network.tx_bps ?? 0)) : null;
  const netPct = net == null ? null : Math.min(100, (net / (5 * 1024 * 1024)) * 100);
  const temp = data?.temperature_c ?? null;
  const running = dashboard?.tasks.running ?? 0;
  const workersMax = dashboard?.limits?.max_concurrent_workers ?? 1;

  return (
    <div className={cn("flex flex-col gap-2.5", className)}>
      <div className="hud-section">SYS MONITOR</div>
      <MetricBar label="CPU" value={data?.cpu_percent ?? null} tone="primary" />
      <MetricBar label="MEM" value={data?.memory.percent ?? null} tone="amber" display={data?.memory.used_bytes != null ? `${Math.round(data.memory.percent ?? 0)}% · ${formatBytes(data.memory.used_bytes)}` : undefined} />
      <MetricBar label="NET" value={netPct} tone="green" display={rate(net)} />
      <MetricBar label="DISK" value={data?.disk.percent ?? null} tone="orange" />
      <MetricBar label="TMP" value={temp} tone="red" display={temp == null ? "--" : `${temp.toFixed(0)}°C`} max={100} />
      <MetricBar label="WRK" value={workersMax ? (running / workersMax) * 100 : 0} tone="primary" display={`${running}/${workersMax}`} />

      <div className="space-y-1 border border-border bg-hud-panel2 px-2 py-1.5 font-sans text-[10px] font-bold tracking-[0.1em]">
        <div className="text-hud-green">UP&nbsp;&nbsp;{data?.uptime_seconds != null ? formatDuration(data.uptime_seconds) : "--:--"}</div>
        <div className="text-hud-med">API&nbsp;{data ? formatDuration(data.process_uptime_seconds) : "--"}</div>
        <div className="text-hud-med">PROC&nbsp;{data?.process_count ?? "--"}</div>
        <div className="text-hud-amber">OS&nbsp;&nbsp;{(data?.os ?? "--").toUpperCase()}</div>
        <div className="text-muted-foreground">ENV&nbsp;{(data?.environment ?? "--").toUpperCase()}</div>
      </div>

      <div className="mt-auto grid gap-1.5">
        <HudBadge top="AI CORE" bottom={hudState === "OFFLINE" ? "OFFLINE" : "ACTIVE"} tone={hudState === "OFFLINE" ? "red" : "green"} />
        <HudBadge top="LINK" bottom={connection === "open" ? "SECURE" : connection.toUpperCase()} tone={connection === "open" ? "primary" : "amber"} />
        <HudBadge top="BROWSER" bottom={(data?.browser_backend ?? (isError ? "ERR" : "--")).toUpperCase()} tone="dim" />
        <HudBadge top="PROTOCOL" bottom={APP_VERSION} tone="dim" />
      </div>
    </div>
  );
}
