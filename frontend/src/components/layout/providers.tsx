"use client";

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { useAppStore } from "@/lib/store";
import { applyAccent, storedAccent } from "@/lib/theme";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 5_000, refetchOnWindowFocus: true, retry: (count, error) => !(error as { status?: number })?.status && count < 2 },
          mutations: { retry: 0 },
        },
      }),
  );
  const setTheme = useAppStore((s) => s.setTheme);
  React.useEffect(() => {
    const stored = window.localStorage.getItem("ba.theme");
    // The HUD defaults to the night palette like the desktop app; "light" is an explicit choice.
    const theme = stored === "light" || stored === "dark" ? stored : "dark";
    setTheme(theme);
    applyAccent(storedAccent(), { persist: false });
  }, [setTheme]);
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={300}>
        {children}
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
