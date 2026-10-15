"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Activity, Eye, EyeOff, KeyRound, Link2, Save, ShieldCheck, Sparkles, Trash2, Wifi } from "lucide-react";

import { HudPanel, HudPill } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { api } from "@/lib/api";
import { toast } from "@/lib/store";
import type { LLMConnectionTest, OrgSettings } from "@/lib/types";
import { cn } from "@/lib/utils";

type LLMForm = { provider: string; model: string; base_url: string; api_key: string; clear: boolean };

function initialForm(settings: OrgSettings): LLMForm {
  return {
    provider: settings.llm.provider || "inherit",
    model: settings.llm.model ?? "",
    base_url: settings.llm.base_url ?? "",
    api_key: "",
    clear: false,
  };
}

function ProviderStatus({ settings }: { settings: OrgSettings }) {
  const configured = Boolean(settings.llm.configured);
  const status = settings.llm.status;
  const label = configured ? "Connected" : settings.llm.effective_provider === "none" ? "Heuristic mode" : "Needs setup";
  return (
    <div className="flex flex-wrap items-center gap-2">
      <HudPill tone={configured ? "green" : settings.llm.effective_provider === "none" ? "dim" : "amber"}>
        {configured ? "AI READY" : label.toUpperCase()}
      </HudPill>
      <span className="text-xs text-muted-foreground">
        {configured
          ? `${status?.provider ?? settings.llm.effective_provider} · ${status?.model ?? settings.llm.model ?? "model configured"}`
          : status?.message ?? (settings.llm.effective_provider === "none" ? "Rule-based browser planner is active." : "Configure a provider and model to enable AI planning.")}
      </span>
    </div>
  );
}

function TestResult({ result }: { result: LLMConnectionTest }) {
  return (
    <div className={cn("flex items-start gap-2 rounded-xl border p-3 text-sm", result.ok ? "border-hud-green/30 bg-hud-green/5" : "border-hud-red/30 bg-hud-red/5")} role="status" aria-live="polite">
      <span className={cn("mt-0.5", result.ok ? "text-hud-green" : "text-hud-red")}><Activity className="h-4 w-4" /></span>
      <div className="min-w-0 flex-1">
        <p className={cn("font-medium", result.ok ? "text-hud-green" : "text-hud-red")}>{result.ok ? "Provider reachable" : "Connection test failed"}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{result.message}</p>
      </div>
      {result.ok && <span className="shrink-0 font-sans text-xs text-muted-foreground">{result.latency_ms} ms</span>}
    </div>
  );
}

export function LLMSettingsPanel({ settings }: { settings: OrgSettings }) {
  const queryClient = useQueryClient();
  const providers = settings.llm.providers ?? [];
  const savedProvider = settings.llm.provider || "inherit";
  const savedModel = settings.llm.model;
  const savedBaseUrl = settings.llm.base_url;
  const savedHasApiKey = settings.llm.has_api_key;
  const [form, setForm] = React.useState<LLMForm>(() => initialForm(settings));
  const [showKey, setShowKey] = React.useState(false);
  const [testResult, setTestResult] = React.useState<LLMConnectionTest | null>(null);

  React.useEffect(() => {
    setForm({
      provider: savedProvider,
      model: savedModel ?? "",
      base_url: savedBaseUrl ?? "",
      api_key: "",
      clear: false,
    });
    setTestResult(null);
  }, [savedProvider, savedModel, savedBaseUrl, savedHasApiKey]);

  const currentSpec = providers.find((item) => item.id === form.provider) ?? null;
  const isDirty = form.provider !== savedProvider
    || form.model !== (settings.llm.model ?? "")
    || form.base_url !== (settings.llm.base_url ?? "")
    || Boolean(form.api_key.trim())
    || form.clear;
  const isServerManaged = form.provider === "inherit" || form.provider === "none";

  const save = useMutation({
    mutationFn: () => api.settings.update({
      llm_provider: form.provider,
      llm_model: form.model.trim(),
      llm_base_url: form.base_url.trim(),
      llm_api_key: form.api_key.trim() || undefined,
      clear_llm_api_key: form.clear,
    }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["settings"], updated);
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      setForm((value) => ({ ...value, api_key: "", clear: false }));
      setTestResult(null);
      toast({ title: "AI provider settings saved", variant: "success" });
    },
    onError: (error: Error) => toast({ title: "Could not save AI settings", description: error.message, variant: "destructive" }),
  });

  const test = useMutation({
    mutationFn: api.settings.testLlm,
    onSuccess: (result) => {
      setTestResult(result);
      if (result.ok) toast({ title: "AI provider connected", description: `${result.provider} · ${result.latency_ms} ms`, variant: "success" });
      else toast({ title: "AI provider test failed", description: result.message, variant: "warning" });
    },
    onError: (error: Error) => toast({ title: "Connection test failed", description: error.message, variant: "destructive" }),
  });

  const chooseProvider = (provider: string) => {
    const selected = providers.find((item) => item.id === provider);
    setForm({
      provider,
      model: selected?.default_model ?? "",
      base_url: selected?.default_base_url ?? "",
      api_key: "",
      clear: false,
    });
    setTestResult(null);
  };

  return (
    <HudPanel title="AI PROVIDER" corners className="lg:col-span-2" bodyClassName="space-y-5">
      <div className="flex flex-col justify-between gap-3 border-b border-border/70 pb-4 sm:flex-row sm:items-center">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-base font-semibold text-foreground"><Sparkles className="h-4 w-4 text-primary" /> Agent intelligence</div>
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">Connect a hosted or local model. The browser agent uses structured action plans and falls back to its deterministic planner if the provider is unavailable.</p>
        </div>
        <ProviderStatus settings={settings} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(220px,0.75fr)_minmax(0,1.4fr)]">
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="llm-provider">Provider</Label>
            <Select id="llm-provider" value={form.provider} onChange={(event) => chooseProvider(event.target.value)}>
              <option value="inherit">Use server default ({settings.llm.effective_provider ?? "none"})</option>
              {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.label}</option>)}
            </Select>
          </div>
          <div className="rounded-xl border border-border/70 bg-secondary/40 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-foreground">{currentSpec?.label ?? "Server default"}</span>
              {currentSpec?.requires_api_key ? <HudPill tone="amber">API key</HudPill> : <HudPill tone="dim">Key optional</HudPill>}
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground">{currentSpec?.description ?? "Inherit the provider configured by the server environment."}</p>
            {form.provider === "ollama" ? (
              <p className="mt-3 rounded-lg bg-background/70 p-2.5 text-xs leading-relaxed text-muted-foreground">
                Pull the model on the Ollama host first, for example <code className="rounded bg-primary/10 px-1 text-primary">ollama pull qwen3</code>. In Docker, the backend/worker must be able to reach the Ollama host. Production deployments must allowlist private service hosts in <code className="text-primary">LLM_ALLOWED_HOSTS</code>.
              </p>
            ) : null}
          </div>
        </div>

        <div className="grid content-start gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="llm-model">Model ID</Label>
            <Input
              id="llm-model"
              value={form.model}
              onChange={(event) => setForm({ ...form, model: event.target.value })}
              placeholder={currentSpec?.model_hint ?? "Provider model ID"}
              disabled={isServerManaged}
              autoComplete="off"
              spellCheck={false}
            />
            <p className="text-[11px] text-muted-foreground">Model names vary by provider and account; use the exact model/deployment ID.</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="llm-base-url">API base URL</Label>
            <div className="relative">
              <Link2 className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="llm-base-url"
                value={form.base_url}
                onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                placeholder={currentSpec?.base_url_hint ?? "https://…"}
                disabled={isServerManaged}
                className="pl-9"
                autoComplete="url"
                spellCheck={false}
              />
            </div>
            <p className="text-[11px] text-muted-foreground">Use the API base, not a full chat-completions request URL.</p>
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor="llm-api-key">Provider API key</Label>
              {settings.llm.has_api_key && <span className="inline-flex items-center gap-1 text-[11px] text-hud-green"><ShieldCheck className="h-3.5 w-3.5" /> Encrypted key saved</span>}
            </div>
            <div className="flex gap-2">
              <div className="relative min-w-0 flex-1">
                <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="llm-api-key"
                  type={showKey ? "text" : "password"}
                  value={form.api_key}
                  onChange={(event) => setForm({ ...form, api_key: event.target.value, clear: false })}
                  placeholder={settings.llm.has_api_key ? "Saved key is hidden · leave blank to keep it" : currentSpec?.requires_api_key ? "Paste the provider key" : "Optional for local or unauthenticated endpoints"}
                  disabled={isServerManaged}
                  autoComplete="new-password"
                  className="pl-9 pr-10"
                />
                <button type="button" onClick={() => setShowKey((visible) => !visible)} disabled={isServerManaged} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:text-foreground disabled:opacity-40" aria-label={showKey ? "Hide API key" : "Show API key"}>
                  {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
              {settings.llm.has_api_key ? (
                <Button type="button" size="icon" variant={form.clear ? "destructive" : "outline"} title={form.clear ? "Keep stored key" : "Remove stored key"} aria-label={form.clear ? "Keep stored key" : "Remove stored key"} onClick={() => setForm({ ...form, api_key: "", clear: !form.clear })}>
                  <Trash2 />
                </Button>
              ) : null}
            </div>
            <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground"><ShieldCheck className="h-3.5 w-3.5 shrink-0 text-hud-green" /> Secrets are encrypted at rest and never returned to the browser. A saved key is reused until you replace or clear it.</p>
            {form.clear && <p className="text-xs text-hud-amber">The saved API key will be removed when you save.</p>}
          </div>
        </div>
      </div>

      {testResult && <TestResult result={testResult} />}

      <div className="flex flex-col gap-3 border-t border-border/70 pt-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
          When AI planning is active, page text is sent to the selected provider. Browser screenshots are {Boolean(settings.system.agent_use_vision) ? "also sent" : "not sent"} (server setting <code className="text-primary">AGENT_USE_VISION</code>). Use a provider you trust; a local Ollama deployment keeps inference on your infrastructure.
        </p>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button type="button" variant="outline" disabled={!settings.llm.configured || isDirty || test.isPending || save.isPending} loading={test.isPending} onClick={() => test.mutate()}>
            <Wifi /> Test connection
          </Button>
          <Button type="button" disabled={!isDirty || save.isPending} loading={save.isPending} onClick={() => save.mutate()}>
            <Save /> Save AI settings
          </Button>
        </div>
      </div>

      {currentSpec?.kind === "openai_compatible" && currentSpec.id === "openai_compatible" ? (
        <div className="flex items-start gap-2 rounded-xl border border-primary/15 bg-primary/5 p-3 text-xs text-muted-foreground">
          <Activity className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <p>For OpenAI-compatible providers, BowserAuto sends standard chat-completions requests and retries a bounded number of times only when the endpoint explicitly rejects an optional request parameter.</p>
        </div>
      ) : null}
    </HudPanel>
  );
}
