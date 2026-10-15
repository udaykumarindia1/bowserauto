"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import { api, setCsrfToken } from "@/lib/api";
import { realtime } from "@/lib/realtime";
import { useAppStore } from "@/lib/store";
import type { AuthResponse } from "@/lib/types";

export function useAuth() {
  const auth = useAppStore((s) => s.auth);
  const authChecked = useAppStore((s) => s.authChecked);
  const setAuth = useAppStore((s) => s.setAuth);
  const router = useRouter();
  const queryClient = useQueryClient();

  const apply = React.useCallback(
    (response: AuthResponse | null) => {
      setCsrfToken(response?.csrf_token ?? null);
      setAuth(response);
      if (!response) {
        realtime().disconnect();
        queryClient.clear();
      }
    },
    [setAuth, queryClient],
  );

  const refresh = React.useCallback(async () => {
    try {
      const me = await api.auth.me();
      apply(me);
      return me;
    } catch {
      apply(null);
      return null;
    }
  }, [apply]);

  const logout = React.useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      apply(null);
      router.replace("/login");
    }
  }, [apply, router]);

  const switchOrganization = React.useCallback(
    async (orgId: string) => {
      const response = await api.auth.switchOrganization(orgId);
      apply(response);
      queryClient.clear();
      realtime().disconnect();
      realtime().connect();
      return response;
    },
    [apply, queryClient],
  );

  return { auth, authChecked, apply, refresh, logout, switchOrganization, role: auth?.role, user: auth?.user, organization: auth?.organization };
}
