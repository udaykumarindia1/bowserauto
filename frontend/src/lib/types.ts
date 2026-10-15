/** Shared API types (mirror backend/app/schemas). */

export type Role = "owner" | "admin" | "member" | "viewer";

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_superuser: boolean;
  current_organization_id: string | null;
  created_at: string;
  last_login_at: string | null;
  preferences: Record<string, unknown>;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  settings: Record<string, unknown>;
  limits: Record<string, number>;
  created_at: string;
}

export interface AuthResponse {
  user: User;
  organization: Organization;
  role: Role;
  csrf_token: string;
  organizations: { organization: Organization; role: Role }[];
}

export interface OutputSettings {
  resolution: string;
  aspect_ratio: string;
  clip_duration_seconds: number;
  filename_pattern: string;
  output_folder: string;
  total_duration_seconds: number | null;
  parallel_generation: boolean;
}

export interface Project {
  id: string;
  organization_id: string;
  name: string;
  description: string;
  template_key: string;
  status: string;
  script_text: string;
  visual_style: string;
  platform_key: string;
  browser_session_id: string | null;
  output_settings: Partial<OutputSettings> & Record<string, unknown>;
  folder_path: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary extends Project {
  scene_count: number;
  completed_scenes: number;
  active_task_id: string | null;
  active_task_status: string | null;
}

export interface Character {
  id: string;
  project_id: string;
  name: string;
  description: string;
  voice_style: string;
  reference_asset_id: string | null;
  created_at: string;
}

export interface Asset {
  id: string;
  project_id: string | null;
  kind: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export type SceneStatus = "draft" | "queued" | "generating" | "processing" | "downloading" | "completed" | "failed" | "skipped" | "waiting_for_human";

export interface Scene {
  id: string;
  project_id: string;
  index: number;
  code: string;
  title: string;
  duration_seconds: number;
  characters: string[];
  location: string;
  visual: string;
  camera: string;
  dialogue: string;
  narration: string;
  prompt: string;
  negative_prompt: string;
  platform_key: string | null;
  reference_asset_ids: string[];
  status: SceneStatus;
  retry_count: number;
  last_error: string;
  generated_file_id: string | null;
  generation_metadata: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

export interface ProjectDetail extends Project {
  characters: Character[];
  scenes: Scene[];
  stats: Record<string, number>;
}

export type TaskStatus =
  | "draft"
  | "queued"
  | "running"
  | "paused"
  | "waiting_for_human"
  | "needs_attention"
  | "completed"
  | "failed"
  | "cancelled";

export interface TaskProgress {
  total: number;
  completed: number;
  failed: number;
  skipped: number;
  done?: number;
  percent: number;
  current_scene_id: string | null;
  current_scene_code: string | null;
  current_scene_index?: number | null;
}

export interface TaskAttention {
  kind?: "login" | "verification" | "error" | "blocked" | string;
  reason?: string;
  scene_id?: string | null;
  scene_code?: string | null;
  options?: string[];
  since?: string;
}

export interface TaskConfig {
  max_retries: number;
  generation_timeout_seconds: number;
  action_timeout_seconds: number;
  parallel_generation: boolean;
  scene_ids: string[] | null;
  retry_failed_only: boolean;
  headless: boolean | null;
  agent_mode: "auto" | "adapter" | "generic";
  mock_options: Record<string, unknown>;
}

export interface Task {
  id: string;
  organization_id: string;
  project_id: string | null;
  title: string;
  kind: string;
  instructions: string;
  status: TaskStatus;
  control: string;
  human_control: boolean;
  platform_key: string;
  browser_session_id: string | null;
  config: Partial<TaskConfig> & Record<string, unknown>;
  progress: Partial<TaskProgress>;
  attention: TaskAttention;
  current_action: string;
  priority: number;
  retry_count: number;
  worker_id: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  paused_at: string | null;
  error: string;
  result: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface TaskStep {
  id: string;
  scene_id: string | null;
  index: number;
  name: string;
  status: string;
  attempt: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  result: Record<string, unknown>;
  error: string;
}

export interface TaskDetail extends Task {
  steps: TaskStep[];
  scenes: Scene[];
  project_name: string | null;
  session_name: string | null;
}

export type SessionStatus = "closed" | "launching" | "ready" | "in_use" | "error" | "expired";

export interface BrowserSession {
  id: string;
  organization_id: string;
  name: string;
  platform_key: string;
  browser_type: string;
  status: SessionStatus;
  desired_state: string;
  interactive: boolean;
  login_status: "unknown" | "logged_in" | "logged_out";
  worker_id: string | null;
  locked_by_task_id: string | null;
  current_url: string;
  current_title: string;
  start_url: string;
  last_used_at: string | null;
  last_heartbeat_at: string | null;
  expires_at: string | null;
  error: string;
  created_at: string;
  updated_at: string;
}

export interface Platform {
  key: string;
  name: string;
  category: string;
  base_url: string;
  adapter_available: boolean;
  enabled: boolean;
  capabilities: Record<string, unknown>;
  supported_actions: string[];
  description: string;
  requires_login: boolean;
  notes: string;
  start_url?: string;
  login_hint?: string;
  selectors?: Record<string, unknown>;
}

export interface Template {
  id: string;
  organization_id: string | null;
  key: string;
  name: string;
  description: string;
  kind: string;
  icon: string;
  config: Record<string, unknown>;
  is_builtin: boolean;
  created_at: string;
}

export interface GeneratedFile {
  id: string;
  project_id: string | null;
  task_id: string | null;
  scene_id: string | null;
  filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  platform_key: string;
  prompt: string;
  generation_seconds: number | null;
  status: string;
  retry_count: number;
  created_at: string;
  metadata: Record<string, unknown>;
  download: { url: string; expires_in: number };
  preview: { url: string; expires_in: number };
}

export interface Screenshot {
  id: string;
  task_id: string | null;
  browser_session_id: string | null;
  scene_id: string | null;
  stage: string;
  url: string;
  title: string;
  size_bytes: number;
  created_at: string;
  image: { url: string; expires_in: number };
}

export interface ActivityEntry {
  id: string;
  task_id: string | null;
  project_id: string | null;
  browser_session_id: string | null;
  scene_id: string | null;
  level: "debug" | "info" | "warning" | "error" | "success" | string;
  event_type: string;
  message: string;
  data: Record<string, unknown>;
  duration_ms: number | null;
  created_at: string;
}

export interface AgentAction {
  id: string;
  agent_run_id: string;
  index: number;
  tool: string;
  arguments: Record<string, unknown>;
  reasoning: string;
  result: Record<string, unknown>;
  success: boolean;
  duration_ms: number;
  error: string;
  created_at: string;
}

export interface AgentRun {
  id: string;
  task_id: string | null;
  scene_id: string | null;
  goal: string;
  mode: string;
  model: string;
  status: string;
  summary?: string;
  error?: string;
  started_at?: string | null;
  finished_at?: string | null;
  actions: AgentAction[];
}

export interface Notification {
  id: string;
  type: string;
  severity: "info" | "success" | "warning" | "error" | string;
  title: string;
  body: string;
  data: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
}

export interface Dashboard {
  tasks: { active: number; queued: number; running: number; needs_attention: number; completed: number; failed: number; total: number };
  projects: { active: number };
  scenes: { total: number; completed: number; failed: number; pending: number };
  files: { count: number; bytes: number };
  sessions: { total: number; ready: number; logged_in: number; items: Pick<BrowserSession, "id" | "name" | "status" | "platform_key" | "login_status" | "current_url">[] };
  usage_week: { generation_seconds: number; generation_minutes: number; clips_generated: number; tasks_failed: number };
  limits: Record<string, number>;
  storage: { bytes: number; mb: number };
  active_tasks: Task[];
  recent_tasks: Task[];
  recent_activity: ActivityEntry[];
  recent_files: GeneratedFile[];
  system: { bus: string; environment: string; mock_platform: boolean };
}

export interface OrgSettings {
  organization: Organization;
  llm: { provider: string; model: string | null; base_url: string | null; has_api_key: boolean; effective_provider?: string };
  limits: Record<string, number>;
  effective_limits: Record<string, number>;
  system: Record<string, unknown>;
}

export interface Member {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: Role;
  created_at: string;
}

export interface AuditEntry {
  id: string;
  user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  ip_address: string;
  data: Record<string, unknown>;
  created_at: string;
}

/** Realtime messages ----------------------------------------------------------------- */
export interface FrameMessage {
  type: "frame";
  session_id: string;
  seq: number;
  data: string; // base64 jpeg
  width: number;
  height: number;
  page_scale: number;
  scroll_x: number;
  scroll_y: number;
  ts: number;
}

export interface SessionStatusMessage {
  type: "status";
  session_id: string;
  status: SessionStatus;
  url: string;
  title: string;
  tabs: { index: number; url: string; title: string; active: boolean }[];
  interactive: boolean;
  task_id: string | null;
  action: string;
  file_chooser_pending: boolean;
  viewport: { width: number; height: number };
  ts: number;
}

export interface SessionActionMessage {
  type: "action";
  session_id: string;
  action: string;
  kind: string;
  detail: Record<string, unknown>;
  ts: number;
}

export interface OrgEvent {
  type: string;
  organization_id: string;
  ts: string;
  task_id: string | null;
  project_id: string | null;
  session_id: string | null;
  scene_id: string | null;
  payload: Record<string, unknown> & { message?: string; level?: string; log_id?: string };
}

export type RealtimeMessage =
  | FrameMessage
  | SessionStatusMessage
  | SessionActionMessage
  | OrgEvent
  | { type: "hello"; user_id: string; organization_id: string; role: Role }
  | { type: "pong"; ts: number }
  | { type: "subscribed"; session_id: string }
  | { type: "error"; message: string };

// --------------------------------------------------------------------------- JARVIS console
export interface AssistantResponse {
  reply: string;
  intent: string;
  ok: boolean;
  actions: { type: string; [key: string]: unknown }[];
  navigate: string | null;
  confirm: { title: string; detail: string; command: string } | null;
  client: { action: string; value: string } | null;
  data: Record<string, unknown>;
}

export interface Briefing {
  text: string;
  status: Record<string, unknown>;
  assistant_name: string;
  user_name: string;
}

export interface MemoryFact {
  id: string;
  key: string;
  value: string;
  category: string;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface SystemMetrics {
  cpu_percent: number | null;
  cpu_count: number | null;
  load: number[] | null;
  memory: { total_bytes: number | null; used_bytes: number | null; percent: number | null };
  network: { rx_bps: number | null; tx_bps: number | null };
  disk: { total_bytes: number | null; used_bytes: number | null; percent: number | null };
  temperature_c: number | null;
  gpu_percent: number | null;
  uptime_seconds: number | null;
  process_uptime_seconds: number;
  process_count: number | null;
  os: string;
  python: string;
  ts: number;
  environment: string;
  browser_backend: string;
}
