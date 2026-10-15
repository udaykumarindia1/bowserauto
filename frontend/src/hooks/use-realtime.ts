"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";

import { realtime } from "@/lib/realtime";
import { toast, useAppStore } from "@/lib/store";
import type { Notification, OrgEvent } from "@/lib/types";

/**
 * Keeps the TanStack Query cache in sync with server events and surfaces notifications.
 * Mounted once in the authenticated layout.
 */
export function useRealtimeSync(enabled: boolean) {
  const queryClient = useQueryClient();
  const setConnection = useAppStore((s) => s.setConnection);
  const pushNotification = useAppStore((s) => s.pushNotification);

  React.useEffect(() => {
    if (!enabled) return;
    const client = realtime();
    client.connect();
    const offState = client.onState(setConnection);
    const offError = client.onError((message) => toast({ title: "Browser control", description: message, variant: "warning" }));
    const pending = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      timer = null;
      for (const key of pending) queryClient.invalidateQueries({ queryKey: [key] });
      pending.clear();
    };
    const invalidate = (...keys: string[]) => {
      for (const key of keys) pending.add(key);
      if (!timer) timer = setTimeout(flush, 250); // coalesce bursts of events
    };
    const offEvent = client.onEvent((event: OrgEvent) => {
      const type = event.type;
      if (type === "notification") {
        const note = event.payload as unknown as Notification;
        pushNotification(note);
        invalidate("notifications");
        toast({ title: note.title, description: note.body, variant: note.severity === "error" ? "destructive" : note.severity === "warning" ? "warning" : note.severity === "success" ? "success" : "default" });
        return;
      }
      invalidate("activity");
      if (type.startsWith("task.") || type.startsWith("scene.") || type.startsWith("human.") || type === "download.completed" || type === "agent.action" || type === "agent.thought") {
        invalidate("tasks", "dashboard");
        if (event.task_id) invalidate(`task:${event.task_id}`);
        if (event.project_id) invalidate(`project:${event.project_id}`, "projects");
      }
      if (type.startsWith("session.") || type === "browser.status" || type === "browser.navigation") {
        invalidate("sessions");
        if (event.session_id) invalidate(`session:${event.session_id}`);
      }
      if (type.startsWith("file.") || type === "download.completed") invalidate("files", "dashboard");
      if (type.startsWith("scene.") && event.project_id) invalidate(`project:${event.project_id}`);
      if (type === "screenshot.captured" && event.task_id) invalidate(`screenshots:${event.task_id}`);
    });
    return () => {
      offState();
      offError();
      offEvent();
      if (timer) clearTimeout(timer);
    };
  }, [enabled, queryClient, setConnection, pushNotification]);
}
