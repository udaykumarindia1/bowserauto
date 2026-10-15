import { HudCore } from "@/components/hud/hud-core";

/** Authentication frame: the reactor core on the left, the form on the right (Mark-LV SetupOverlay feel). */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="hud-grid relative flex min-h-screen items-center justify-center bg-background p-4">
      <div className="hud-corners grid w-full max-w-4xl border border-border bg-card/80 md:grid-cols-[1fr_minmax(0,420px)]">
        <div className="relative hidden min-h-[460px] flex-col items-center justify-center border-r border-border p-6 md:flex">
          <div className="h-72 w-72">
            <HudCore state="INITIALISING" compact />
          </div>
          <p className="hud-label mt-4 text-center">◈ Initialisation required</p>
          <p className="mt-1 max-w-xs text-center text-[11px] text-muted-foreground">Sign in to bring the workspace online. Platform logins happen inside your persistent browser sessions and are never stored here.</p>
        </div>
        <div className="p-6">
          <div className="mb-5">
            <p className="hud-glow font-mono text-2xl font-bold tracking-[0.35em] text-primary">J.A.R.V.I.S</p>
            <p className="hud-label mt-1">Browser automation workspace</p>
          </div>
          {children}
        </div>
      </div>
    </div>
  );
}
