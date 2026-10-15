import { Badge } from "./badge";
import { titleCase } from "@/lib/utils";

const TONES: Record<string, "success" | "warning" | "destructive" | "info" | "muted" | "secondary"> = {
  completed: "success",
  success: "success",
  verified: "success",
  ready: "success",
  logged_in: "success",
  running: "info",
  in_use: "info",
  generating: "info",
  processing: "info",
  downloading: "info",
  queued: "secondary",
  launching: "secondary",
  draft: "muted",
  pending: "muted",
  closed: "muted",
  unknown: "muted",
  paused: "warning",
  waiting_for_human: "warning",
  needs_attention: "warning",
  retrying: "warning",
  blocked: "warning",
  skipped: "muted",
  failed: "destructive",
  error: "destructive",
  cancelled: "muted",
  expired: "muted",
  logged_out: "warning",
};

export function StatusBadge({ status, pulse }: { status: string | null | undefined; pulse?: boolean }) {
  if (!status) return null;
  const tone = TONES[status] ?? "secondary";
  const live = pulse ?? ["running", "generating", "processing", "downloading", "in_use", "launching"].includes(status);
  return (
    <Badge variant={tone}>
      {live ? <span className="hud-dot animate-pulse-dot bg-current" /> : null}
      {titleCase(status)}
    </Badge>
  );
}
