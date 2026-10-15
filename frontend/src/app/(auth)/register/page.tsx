"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";

export default function RegisterPage() {
  const router = useRouter();
  const { apply } = useAuth();
  const [form, setForm] = React.useState({ full_name: "", email: "", password: "", organization_name: "" });
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const update = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await api.auth.register(form.email.trim(), form.password, form.full_name.trim(), form.organization_name.trim() || undefined);
      apply(response);
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Create your workspace</CardTitle>
        <CardDescription>You become the owner of a new organisation; invite teammates later from Settings.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="full_name">Your name</Label>
            <Input id="full_name" required value={form.full_name} onChange={update("full_name")} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="organization_name">Organisation (optional)</Label>
            <Input id="organization_name" placeholder="Acme Studio" value={form.organization_name} onChange={update("organization_name")} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="email" required value={form.email} onChange={update("email")} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete="new-password" minLength={10} required value={form.password} onChange={update("password")} />
            <p className="text-[11px] text-muted-foreground">At least 10 characters.</p>
          </div>
          {error ? <p className="text-xs text-red-500">{error}</p> : null}
          <Button type="submit" className="w-full" loading={busy}>
            Create account
          </Button>
        </form>
        <p className="mt-4 text-center text-xs text-muted-foreground">
          Already registered?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
