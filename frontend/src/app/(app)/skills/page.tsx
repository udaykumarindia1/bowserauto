"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Search, ShieldCheck, Sparkles } from "lucide-react";

import { HudPanel, HudPill } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorState, PageHeader, Skeleton } from "@/components/ui/misc";
import { api } from "@/lib/api";
import type { AgentSkill } from "@/lib/types";
import { cn, titleCase } from "@/lib/utils";

const CATEGORIES = ["all", "browser", "filesystem", "task"] as const;

function riskTone(risk: AgentSkill["risk"]): "green" | "amber" | "orange" | "dim" {
  if (risk === "read-only") return "green";
  if (risk === "write") return "amber";
  if (risk === "state-change") return "orange";
  return "dim";
}

function SkillCard({ skill }: { skill: AgentSkill }) {
  const properties = Object.entries(skill.parameters.properties ?? {});
  const required = new Set(skill.parameters.required ?? []);
  return (
    <HudPanel
      corners
      className="flex h-full flex-col"
      title={skill.name.toUpperCase()}
      actions={<HudPill tone={riskTone(skill.risk)}>{skill.risk.replace("-", " ")}</HudPill>}
    >
      <p className="min-h-10 font-sans text-xs leading-relaxed text-muted-foreground">{skill.description}</p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        <HudPill tone="dim">{titleCase(skill.category)}</HudPill>
        <HudPill tone="dim">Approval · {skill.approval}</HudPill>
      </div>
      <div className="mt-4 border-t border-border/70 pt-3">
        <p className="hud-label mb-2">Inputs</p>
        {properties.length ? (
          <ul className="space-y-1.5 font-sans text-[11px]">
            {properties.map(([name, schema]) => (
              <li key={name} className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                <span className="min-w-0">
                  <code className="text-primary">{name}</code>
                  {schema.description ? <span className="ml-1 text-muted-foreground">{schema.description}</span> : null}
                </span>
                <span className="text-right text-muted-foreground">{schema.type ?? "any"}{required.has(name) ? " · required" : ""}</span>
              </li>
            ))}
          </ul>
        ) : <p className="font-sans text-[11px] text-muted-foreground">No input parameters.</p>}
      </div>
    </HudPanel>
  );
}

export default function SkillsPage() {
  const skills = useQuery({ queryKey: ["assistant-skills"], queryFn: api.assistant.skills });
  const [search, setSearch] = React.useState("");
  const [category, setCategory] = React.useState<(typeof CATEGORIES)[number]>("all");
  const items = (skills.data ?? []).filter((skill) => {
    const matchesCategory = category === "all" || skill.category === category;
    const query = search.trim().toLowerCase();
    const matchesSearch = !query || `${skill.name} ${skill.description} ${skill.category}`.toLowerCase().includes(query);
    return matchesCategory && matchesSearch;
  });

  if (skills.isError) return <div className="p-6"><ErrorState error={skills.error} retry={() => skills.refetch()} /></div>;

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <PageHeader
        eyebrow="Assistant capability registry"
        title="Skills"
        description="The agent's available browser, file and task-control actions, with their inputs and approval policy."
        actions={<Link href="/new-task"><Button size="sm">Run a task <ArrowRight /></Button></Link>}
      />
      <HudPanel corners className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input className="pl-8" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search tools and descriptions" aria-label="Search skills" />
        </div>
        <div className="flex flex-wrap gap-1.5" aria-label="Skill category filter">
          {CATEGORIES.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setCategory(value)}
              aria-pressed={category === value}
              className={cn("hud-pill cursor-pointer border transition-colors", category === value ? "border-primary/40 text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}
            >
              {value === "all" ? "All" : titleCase(value)}
            </button>
          ))}
        </div>
      </HudPanel>
      <div className="flex flex-wrap items-center gap-2">
        <HudPill tone="green"><Sparkles className="h-3 w-3" /> {skills.data?.length ?? 0} registered skills</HudPill>
        <HudPill tone="dim"><ShieldCheck className="h-3 w-3" /> Built-in handlers only · no arbitrary plugin code</HudPill>
      </div>
      {skills.isLoading ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-56" />)}</div>
      ) : items.length ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((skill) => <SkillCard key={skill.name} skill={skill} />)}
        </div>
      ) : (
        <HudPanel title="NO MATCHES" corners>
          <p className="font-sans text-xs text-muted-foreground">No registered skill matches that filter. Try a different category or search phrase.</p>
        </HudPanel>
      )}
      <p className="font-sans text-[10px] leading-relaxed text-muted-foreground">
        Approval labels describe the generic-agent policy. Platform adapters also pause through the same approval controller before browser writes; confirm_all additionally gates browser state changes. Review each target and keep external-site tasks visible.
      </p>
    </div>
  );
}
