"use client";

import { create } from "zustand";

import type { AuthResponse, Notification } from "./types";
import type { ConnectionState } from "./realtime";

export type Theme = "dark" | "light";

interface Toast {
  id: number;
  title: string;
  description?: string;
  variant?: "default" | "success" | "warning" | "destructive";
}

interface AppState {
  auth: AuthResponse | null;
  authChecked: boolean;
  setAuth: (auth: AuthResponse | null) => void;
  connection: ConnectionState;
  setConnection: (state: ConnectionState) => void;
  theme: Theme;
  setTheme: (theme: Theme) => void;
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  toasts: Toast[];
  toast: (toast: Omit<Toast, "id">) => void;
  dismissToast: (id: number) => void;
  liveNotifications: Notification[];
  pushNotification: (notification: Notification) => void;
}

let toastSeq = 1;

export const useAppStore = create<AppState>((set) => ({
  auth: null,
  authChecked: false,
  setAuth: (auth) => set({ auth, authChecked: true }),
  connection: "idle",
  setConnection: (connection) => set({ connection }),
  theme: "dark",
  setTheme: (theme) => {
    if (typeof document !== "undefined") {
      document.documentElement.classList.toggle("dark", theme === "dark");
      window.localStorage.setItem("ba.theme", theme);
    }
    set({ theme });
  },
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  toasts: [],
  toast: (toast) => {
    const id = toastSeq++;
    set((s) => ({ toasts: [...s.toasts.slice(-4), { ...toast, id }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), toast.variant === "destructive" ? 9000 : 5000);
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  liveNotifications: [],
  pushNotification: (notification) => set((s) => ({ liveNotifications: [notification, ...s.liveNotifications].slice(0, 50) })),
}));

export const toast = (t: Omit<Toast, "id">) => useAppStore.getState().toast(t);

export function canWrite(role: string | undefined): boolean {
  return role === "owner" || role === "admin" || role === "member";
}

export function isAdmin(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}
