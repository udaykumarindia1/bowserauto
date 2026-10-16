"use client";

import * as React from "react";
import Link from "next/link";
import { LoaderCircle, ShieldCheck, Smartphone } from "lucide-react";

import { HudPanel, HudPill } from "@/components/hud/hud-primitives";
import { Button } from "@/components/ui/button";
import { api, ApiError, setCsrfToken } from "@/lib/api";

type PairState = "starting" | "claiming" | "waiting" | "approved" | "complete" | "expired" | "rejected" | "error";
const PAIRING_STORAGE_KEY = "ba.phonePairing";

function parseSavedPair(): { id: string; code: string } | null {
  try {
    const value = window.sessionStorage.getItem(PAIRING_STORAGE_KEY);
    if (!value) return null;
    const pair = JSON.parse(value) as { id?: unknown; code?: unknown };
    return typeof pair.id === "string" && typeof pair.code === "string" ? { id: pair.id, code: pair.code } : null;
  } catch {
    return null;
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export default function PairPhonePage() {
  const [state, setState] = React.useState<PairState>("starting");
  const [message, setMessage] = React.useState("Reading the temporary pairing request…");
  React.useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const fromQr = { id: fragment.get("id") ?? "", code: fragment.get("code") ?? "" };
    const pair = fromQr.id && fromQr.code ? fromQr : parseSavedPair();
    // The bearer code stays in the URL fragment only long enough to read it. Fragments are not
    // sent in HTTP requests; remove it from browser history before making any network request.
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    if (!pair) {
      setState("error");
      setMessage("No valid pairing request was found. Generate a fresh QR code in Settings → Devices.");
      return;
    }
    window.sessionStorage.setItem(PAIRING_STORAGE_KEY, JSON.stringify(pair));

    let active = true;
    const finish = (next: PairState, text: string) => {
      if (!active) return;
      setState(next);
      setMessage(text);
      if (["expired", "rejected", "complete", "error"].includes(next)) {
        window.sessionStorage.removeItem(PAIRING_STORAGE_KEY);
      }
    };

    const run = async () => {
      try {
        setState("claiming");
        setMessage("Sending a pairing request. Confirm it on your signed-in device.");
        const claim = await api.auth.pairings.claim(pair.id, pair.code, "Phone browser");
        if (!active) return;
        if (claim.status === "expired") return finish("expired", "This QR code has expired. Generate a new one from Settings → Devices.");
        if (claim.status === "rejected") return finish("rejected", "This pairing request was rejected on the signed-in device.");
        if (claim.status === "completed") return finish("error", "This QR code was already used. Generate a fresh pairing request.");

        setState("waiting");
        setMessage("Request sent. Keep this page open while you approve the phone on your signed-in device.");
        const deadline = Date.now() + 150_000;
        while (active && Date.now() < deadline) {
          try {
            const result = await api.auth.pairings.poll(pair.id, pair.code);
            if (!active) return;
            if (result.status === "completed") {
              if (!result.authenticated || !result.csrf_token) {
                return finish("error", "This QR code was already used by another device. Start a new pairing request.");
              }
              setCsrfToken(result.csrf_token);
              setState("complete");
              setMessage("Phone paired. Opening your secure workspace…");
              window.sessionStorage.removeItem(PAIRING_STORAGE_KEY);
              window.location.replace("/dashboard");
              return;
            }
            if (result.status === "expired") return finish("expired", "This pairing request expired. Generate a new QR code.");
            if (result.status === "rejected") return finish("rejected", "This pairing request was rejected on the signed-in device.");
            setState(result.status === "approved" ? "approved" : "waiting");
            setMessage(result.status === "approved" ? "Approved. Completing secure sign-in…" : "Request sent. Waiting for approval on your signed-in device.");
          } catch (error) {
            if (error instanceof ApiError && [401, 404, 410].includes(error.status)) {
              return finish("expired", "The pairing code is invalid or has expired. Generate a new QR code.");
            }
            setMessage("Connection interrupted. Retrying the pairing status check…");
          }
          await delay(1800);
        }
        if (active) finish("expired", "Pairing timed out. Generate a fresh QR code and scan again.");
      } catch (error) {
        if (!active) return;
        setState("error");
        setMessage(error instanceof Error ? error.message : "Pairing could not be started. Generate a new QR code.");
        window.sessionStorage.removeItem(PAIRING_STORAGE_KEY);
      }
    };

    void run();
    return () => { active = false; };
  }, []);

  const pending = ["starting", "claiming", "waiting", "approved"].includes(state);
  const tone = state === "complete" ? "green" : state === "approved" ? "amber" : state === "expired" || state === "rejected" || state === "error" ? "red" : "dim";
  return (
    <main className="flex min-h-screen items-center justify-center p-4 sm:p-8">
      <HudPanel corners title="AVINASH · SECURE DEVICE PAIRING" className="w-full max-w-xl">
        <div className="flex flex-col items-center gap-4 py-6 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-primary/25 bg-primary/10 text-primary">
            {pending ? <LoaderCircle className="h-6 w-6 animate-spin" /> : state === "complete" ? <ShieldCheck className="h-6 w-6" /> : <Smartphone className="h-6 w-6" />}
          </div>
          <HudPill tone={tone}>{state.replace("-", " ")}</HudPill>
          <p className="max-w-md font-sans text-sm leading-relaxed text-muted-foreground">{message}</p>
          {state === "waiting" || state === "approved" ? <p className="font-sans text-[10px] text-muted-foreground">This temporary code is stored only in this tab and expires shortly.</p> : null}
          {!pending && state !== "complete" ? <Link href="/login"><Button size="sm" variant="outline">Return to sign in</Button></Link> : null}
        </div>
      </HudPanel>
    </main>
  );
}
