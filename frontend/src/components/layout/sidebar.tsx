"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, CalendarClock, FolderKanban, Globe, LayoutDashboard, LayoutTemplate, ListTodo, Plug, Settings, Sparkles, Video, Wand2 } from "lucide-react";

import { SysMonitor } from "@/components/hud/sys-monitor";
import { useJarvis } from "@/lib/jarvis-store";
import { cn } from "@/lib/utils";

export const NAV = [
  { href: "/dashboard", label: "Command Center", short: "CMD", icon: LayoutDashboard },
  { href: "/new-task", label: "New Task", short: "NEW", icon: Wand2 },
  { href: "/projects", label: "Projects", short: "PRJ", icon: FolderKanban },
  { href: "/sessions", label: "Browser Sessions", short: "BRW", icon: Globe },
  { href: "/tasks", label: "Tasks", short: "TSK", icon: ListTodo },
  { href: "/schedules", label: "Schedules", short: "SCH", icon: CalendarClock },
  { href: "/files", label: "Generated Files", short: "FIL", icon: Video },
  { href: "/platforms", label: "Platforms", short: "PLG", icon: Plug },
  { href: "/skills", label: "Agent Skills", short: "SKL", icon: Sparkles },
  { href: "/templates", label: "Templates", short: "TPL", icon: LayoutTemplate },
  { href: "/activity", label: "Activity Logs", short: "LOG", icon: Activity },
  { href: "/settings", label: "Settings", short: "SET", icon: Settings },
];

/** Left HUD column: navigation + SYS MONITOR (ported from Mark-LV's left panel). */
export function Sidebar({ attention = 0 }: { attention?: number }) {
  const pathname = usePathname();
  const monitorOpen = useJarvis((s) => s.monitorOpen);
  return (
    <aside className={cn("flex h-full shrink-0 flex-col border-r border-border/70 bg-card/65 transition-[width] duration-200", monitorOpen ? "w-56" : "w-16")} aria-label="Navigation">
      <nav className="shrink-0 space-y-1 p-2">
        {NAV.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              title={item.label}
              className={cn(
                "group relative flex items-center gap-2 rounded-xl border border-transparent px-2.5 py-2.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
                active && "border-primary/15 bg-primary/10 font-semibold text-primary",
                !monitorOpen && "justify-center px-0",
              )}
            >
              <item.icon className="h-3.5 w-3.5 shrink-0" />
              {monitorOpen ? <span className="flex-1 truncate">{item.label}</span> : null}
              {item.href === "/tasks" && attention > 0 ? (
                <span className={cn("bg-hud-amber px-1 text-[9px] font-bold text-black", !monitorOpen && "absolute ml-6 -mt-4")}>{attention}</span>
              ) : null}
            </Link>
          );
        })}
      </nav>
      {monitorOpen ? (
        <div className="min-h-0 flex-1 overflow-y-auto border-t border-border p-2.5">
          <SysMonitor className="min-h-full" />
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-end gap-1 border-t border-border p-2">
          <span className="[writing-mode:vertical-rl] rotate-180 font-sans text-[9px] uppercase tracking-[0.3em] text-muted-foreground">sys monitor hidden</span>
        </div>
      )}
    </aside>
  );
}
