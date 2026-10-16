/**
 * Typed fetch wrapper for the BowserAuto API.
 *
 * - All requests are same-origin (`/api/...`), proxied to the backend by server.mjs / nginx.
 * - Authentication is cookie based (HttpOnly); mutating requests add the CSRF token that the
 *   backend returns on login (double-submit pattern).
 * - Errors are normalised into `ApiError` carrying the backend's `{error:{code,message,details}}`.
 */

import type {
  ActivityEntry,
  AgentSkill,
  AssistantResponse,
  Briefing,
  DevicePairing,
  MemoryFact,
  SystemMetrics,
  AgentRun,
  Asset,
  AuditEntry,
  AuthResponse,
  BrowserSession,
  Character,
  Dashboard,
  GeneratedFile,
  Member,
  Notification,
  LLMConnectionTest,
  OrgSettings,
  Platform,
  Project,
  ProjectDetail,
  ProjectSummary,
  Scene,
  Screenshot,
  Task,
  TaskDetail,
  TaskSchedule,
  Template,
  User,
} from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const CSRF_KEY = "ba.csrf";

export function getCsrfToken(): string {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem(CSRF_KEY) || window.localStorage.getItem(CSRF_KEY) || "";
}

export function setCsrfToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) {
    window.localStorage.setItem(CSRF_KEY, token);
  } else {
    window.localStorage.removeItem(CSRF_KEY);
    window.sessionStorage.removeItem(CSRF_KEY);
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}${path.includes("?") ? "&" : "?"}${qs}` : path;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Query;
  formData?: FormData;
  signal?: AbortSignal;
  raw?: boolean;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method || "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  if (method !== "GET") {
    headers["X-CSRF-Token"] = getCsrfToken();
  }
  const response = await fetch(withQuery(path, options.query), {
    method,
    headers,
    body,
    credentials: "same-origin",
    signal: options.signal,
    cache: "no-store",
  });
  if (response.status === 204) return undefined as T;
  if (options.raw) return response as unknown as T;
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }
  }
  if (!response.ok) {
    const envelope = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
    const detail = (data as { detail?: unknown } | null)?.detail;
    const message =
      envelope?.message ||
      (typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg).join(", ") : "") ||
      response.statusText ||
      "Request failed";
    const error = new ApiError(response.status, envelope?.code || (response.status === 401 ? "unauthorized" : "error"), message, envelope?.details ?? detail);
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("ba:unauthorized"));
    }
    throw error;
  }
  return data as T;
}

const get = <T>(path: string, query?: Query) => request<T>(path, { query });
const post = <T>(path: string, body?: unknown, query?: Query) => request<T>(path, { method: "POST", body, query });
const patch = <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body });
const del = <T = void>(path: string) => request<T>(path, { method: "DELETE" });

/** Public API surface used by the UI. Grouped by resource. */
export const api = {
  health: () => get<{ status: string; environment: string; version: string; embedded_worker?: unknown }>("/api/health"),

  auth: {
    status: () => get<{ registration_open: boolean; has_users: boolean; app_name: string; environment: string }>("/api/auth/status"),
    me: () => get<AuthResponse>("/api/auth/me"),
    login: (email: string, password: string) => post<AuthResponse>("/api/auth/login", { email, password }),
    register: (email: string, password: string, full_name: string, organization_name?: string) =>
      post<AuthResponse>("/api/auth/register", { email, password, full_name, organization_name }),
    logout: () => post<void>("/api/auth/logout"),
    updateMe: (body: Partial<Pick<User, "full_name" | "preferences">>) => patch<User>("/api/auth/me", body),
    changePassword: (current_password: string, new_password: string) => post<void>("/api/auth/change-password", { current_password, new_password }),
    switchOrganization: (orgId: string) => post<AuthResponse>(`/api/auth/switch-organization/${orgId}`),
    loginSessions: () => get<{ id: string; ip_address: string; user_agent: string; created_at: string; last_seen_at: string; expires_at: string; current: boolean }[]>("/api/auth/sessions"),
    revokeLoginSession: (id: string) => del(`/api/auth/sessions/${id}`),
    pairings: {
      create: () => post<{ id: string; code: string; expires_at: string }>("/api/auth/device-pairings"),
      claim: (pairing_id: string, code: string, device_name = "Phone browser") =>
        post<{ status: string }>("/api/auth/device-pairings/claim", { pairing_id, code, device_name }),
      poll: (pairing_id: string, code: string) =>
        post<{ status: string; authenticated: boolean; csrf_token?: string }>("/api/auth/device-pairings/poll", { pairing_id, code }),
      get: (id: string) => get<DevicePairing>(`/api/auth/device-pairings/${id}`),
      approve: (id: string) => post<DevicePairing>(`/api/auth/device-pairings/${id}/approve`),
      reject: (id: string) => post<DevicePairing>(`/api/auth/device-pairings/${id}/reject`),
    },
  },

  dashboard: () => get<Dashboard>("/api/dashboard"),

  projects: {
    list: (include_archived = false) => get<ProjectSummary[]>("/api/projects", { include_archived }),
    get: (id: string) => get<ProjectDetail>(`/api/projects/${id}`),
    create: (body: Partial<Project> & { name: string }) => post<ProjectDetail>("/api/projects", body),
    update: (id: string, body: Partial<Project>) => patch<ProjectDetail>(`/api/projects/${id}`, body),
    remove: (id: string) => del(`/api/projects/${id}`),
    analyze: (id: string, body: { script_text?: string; target_duration_seconds?: number | null; clip_duration_seconds?: number | null }) =>
      post<{ scenes: Scene[]; method: string; warnings: string[] }>(`/api/projects/${id}/analyze`, body),
    estimate: (id: string) => get<{ scenes: number; output_seconds: number; estimated_wall_clock_seconds: number; estimated_minutes: number; platform_credits_note: string; warnings: string[] }>(`/api/projects/${id}/estimate`),
    exportUrl: (id: string) => `/api/projects/${id}/export`,
    scenes: {
      list: (projectId: string) => get<Scene[]>(`/api/projects/${projectId}/scenes`),
      add: (projectId: string, body: Partial<Scene>, after?: string) => post<Scene>(`/api/projects/${projectId}/scenes`, body, { after }),
      update: (projectId: string, sceneId: string, body: Partial<Scene>) => patch<Scene>(`/api/projects/${projectId}/scenes/${sceneId}`, body),
      remove: (projectId: string, sceneId: string) => del(`/api/projects/${projectId}/scenes/${sceneId}`),
      duplicate: (projectId: string, sceneId: string) => post<Scene>(`/api/projects/${projectId}/scenes/${sceneId}/duplicate`),
      regeneratePrompt: (projectId: string, sceneId: string) => post<Scene>(`/api/projects/${projectId}/scenes/${sceneId}/regenerate-prompt`),
      reorder: (projectId: string, scene_ids: string[]) => post<Scene[]>(`/api/projects/${projectId}/scenes/reorder`, { scene_ids }),
    },
    assets: {
      list: (projectId: string) => get<Asset[]>(`/api/projects/${projectId}/assets`),
      upload: (projectId: string, file: File, kind = "reference") => {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("kind", kind);
        return request<Asset>(`/api/projects/${projectId}/assets`, { method: "POST", formData });
      },
      url: (projectId: string, assetId: string) => get<{ url: string; expires_in: number }>(`/api/projects/${projectId}/assets/${assetId}/url`),
      remove: (projectId: string, assetId: string) => del(`/api/projects/${projectId}/assets/${assetId}`),
    },
    characters: {
      list: (projectId: string) => get<Character[]>(`/api/projects/${projectId}/characters`),
      create: (projectId: string, body: Partial<Character> & { name: string }) => post<Character>(`/api/projects/${projectId}/characters`, body),
      update: (projectId: string, id: string, body: Partial<Character> & { name: string }) => patch<Character>(`/api/projects/${projectId}/characters/${id}`, body),
      remove: (projectId: string, id: string) => del(`/api/projects/${projectId}/characters/${id}`),
    },
  },

  tasks: {
    list: (query?: { status?: string; project_id?: string; limit?: number; offset?: number }) => get<{ items: Task[]; total: number }>("/api/tasks", query),
    get: (id: string) => get<TaskDetail>(`/api/tasks/${id}`),
    create: (body: Record<string, unknown>) => post<TaskDetail>("/api/tasks", body),
    update: (id: string, body: Record<string, unknown>) => patch<TaskDetail>(`/api/tasks/${id}`, body),
    remove: (id: string) => del(`/api/tasks/${id}`),
    action: (id: string, action: "start" | "pause" | "resume" | "stop" | "takeover" | "release" | "retry" | "skip" | "approve" | "reject", body?: Record<string, unknown>) =>
      post<Task>(`/api/tasks/${id}/${action}`, body ?? {}),
    reorder: (task_ids: string[]) => post<Task[]>("/api/tasks/reorder", { task_ids }),
    stopAll: () => post<{ stopped: number }>("/api/tasks/stop-all"),
    activity: (id: string, limit = 300) => get<ActivityEntry[]>(`/api/tasks/${id}/activity`, { limit }),
    screenshots: (id: string) => get<Screenshot[]>(`/api/tasks/${id}/screenshots`),
    agentRuns: (id: string) => get<AgentRun[]>(`/api/tasks/${id}/agent-runs`),
  },

  schedules: {
    list: () => get<TaskSchedule[]>("/api/schedules"),
    create: (body: {
      title: string;
      kind: string;
      instructions: string;
      platform_key: string;
      browser_session_id?: string;
      priority: number;
      run_at: string;
      repeat_interval_minutes: number | null;
      config: { agent_mode: "generic"; approval_mode: "confirm_writes" | "confirm_all"; allowed_domains: string[] };
    }) => post<TaskSchedule>("/api/schedules", body),
    update: (id: string, body: { enabled?: boolean; run_at?: string; repeat_interval_minutes?: number | null }) =>
      patch<TaskSchedule>(`/api/schedules/${id}`, body),
    remove: (id: string) => del(`/api/schedules/${id}`),
  },

  sessions: {
    list: () => get<BrowserSession[]>("/api/sessions"),
    get: (id: string) => get<BrowserSession>(`/api/sessions/${id}`),
    create: (body: { name: string; platform_key: string; start_url?: string }) => post<BrowserSession>("/api/sessions", body),
    update: (id: string, body: Partial<BrowserSession>) => patch<BrowserSession>(`/api/sessions/${id}`, body),
    remove: (id: string) => del(`/api/sessions/${id}`),
    open: (id: string, url?: string) => post<BrowserSession>(`/api/sessions/${id}/open`, url ? { url } : undefined),
    close: (id: string) => post<BrowserSession>(`/api/sessions/${id}/close`),
    takeover: (id: string) => post<BrowserSession>(`/api/sessions/${id}/takeover`),
    release: (id: string) => post<BrowserSession>(`/api/sessions/${id}/release`),
    navigate: (id: string, url: string) => post<BrowserSession>(`/api/sessions/${id}/navigate`, { url }),
    logout: (id: string) => post<BrowserSession>(`/api/sessions/${id}/logout`),
    markLogin: (id: string, logged_in: boolean) => post<BrowserSession>(`/api/sessions/${id}/mark-login`, undefined, { logged_in }),
    upload: (id: string, file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      return request<{ uploaded: boolean; filename: string }>(`/api/sessions/${id}/upload`, { method: "POST", formData });
    },
    screenshots: (id: string) => get<Screenshot[]>(`/api/sessions/${id}/screenshots`),
  },

  files: {
    list: (query?: { project_id?: string; task_id?: string; limit?: number; offset?: number }) => get<{ items: GeneratedFile[]; total: number }>("/api/files", query),
    get: (id: string) => get<GeneratedFile>(`/api/files/${id}`),
    url: (id: string, inline = false) => get<{ url: string; expires_in: number }>(`/api/files/${id}/url`, { inline }),
    remove: (id: string) => del(`/api/files/${id}`),
  },

  platforms: {
    list: () => get<Platform[]>("/api/platforms"),
    get: (key: string) => get<Platform>(`/api/platforms/${key}`),
    setEnabled: (key: string, enabled: boolean) => post<Platform>(`/api/platforms/${key}/enabled`, { enabled }),
  },

  templates: {
    list: () => get<Template[]>("/api/templates"),
    create: (body: Partial<Template> & { key: string; name: string }) => post<Template>("/api/templates", body),
    update: (id: string, body: Partial<Template>) => patch<Template>(`/api/templates/${id}`, body),
    remove: (id: string) => del(`/api/templates/${id}`),
  },

  notifications: {
    list: (limit = 50) => get<{ items: Notification[]; unread: number }>("/api/notifications", { limit }),
    markRead: (id: string) => post<void>(`/api/notifications/${id}/read`),
    markAllRead: () => post<void>("/api/notifications/read"),
  },

  activity: (query?: { limit?: number; level?: string; event_type?: string; task_id?: string; project_id?: string; before?: string }) =>
    get<ActivityEntry[]>("/api/activity", query),
  audit: (limit = 200) => get<AuditEntry[]>("/api/audit", { limit }),

  assistant: {
    command: (text: string, context: Record<string, unknown> = {}, confirmed = false) =>
      post<AssistantResponse>("/api/assistant/command", { text, context, confirmed }),
    briefing: (local_hour: number) => get<Briefing>("/api/assistant/briefing", { local_hour }),
    capabilities: () => get<{ tools: { tool: string; description: string; args: Record<string, string> }[]; pages: string[]; assistant_name: string; user_name: string }>("/api/assistant/capabilities"),
    skills: () => get<AgentSkill[]>("/api/assistant/skills"),
    memory: {
      list: () => get<MemoryFact[]>("/api/assistant/memory"),
      add: (body: { key?: string; value: string; category?: string }) => post<MemoryFact>("/api/assistant/memory", body),
      remove: (id: string) => del(`/api/assistant/memory/${id}`),
      clear: () => del("/api/assistant/memory"),
    },
    system: () => get<SystemMetrics>("/api/assistant/system"),
  },

  settings: {
    get: () => get<OrgSettings>("/api/settings"),
    update: (body: Record<string, unknown>) => patch<OrgSettings>("/api/settings", body),
    testLlm: () => post<LLMConnectionTest>("/api/settings/llm/test"),
    members: {
      list: () => get<Member[]>("/api/settings/members"),
      add: (email: string, role: string) => post<Member>("/api/settings/members", { email, role }),
      update: (id: string, role: string) => patch<Member>(`/api/settings/members/${id}`, { role }),
      remove: (id: string) => del(`/api/settings/members/${id}`),
    },
  },
};

export type Api = typeof api;
