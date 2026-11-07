"use client";

import { useSearchParams } from "next/navigation";
import { CircleAlert, LogIn } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Skeleton";
import { api, ApiError, errorMessage } from "@/lib/api";
import { REMOTE_DISABLED_MESSAGE, safeNextPath } from "@/lib/auth";
import { AuthLayout, RemoteDisabledNotice } from "./AuthLayout";

type Phase = "checking" | "form" | "remote_disabled";

function loginError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return err.detail || "Wrong password.";
    if (err.status === 429) return err.detail || "Too many attempts. Wait a minute and try again.";
    if (err.status === 400) return err.detail || REMOTE_DISABLED_MESSAGE;
  }
  return errorMessage(err);
}

export function LoginView() {
  const params = useSearchParams();
  const next = safeNextPath(params.get("next"));
  const [phase, setPhase] = useState<Phase>("checking");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Already allowed (local use or a live session)? Go straight back. No password configured remotely? Say so.
  useEffect(() => {
    let cancelled = false;
    api.auth
      .status()
      .then((s) => {
        if (cancelled) return;
        if (s.local || s.authenticated) window.location.replace(next);
        else if (!s.password_set) setPhase("remote_disabled");
        else setPhase("form");
      })
      .catch(() => {
        if (!cancelled) setPhase("form"); // backend unreachable: show the form; submitting reports the error
      });
    return () => {
      cancelled = true;
    };
  }, [next]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!password) {
      setError("Enter the dashboard password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.auth.login(password);
      // Full navigation so every socket and cache starts fresh with the new session cookie.
      window.location.assign(next);
    } catch (err) {
      setError(loginError(err));
      setBusy(false);
    }
  }

  if (phase === "remote_disabled") return <RemoteDisabledNotice />;

  return (
    <AuthLayout title="Sign in" subtitle="Enter the dashboard password to continue.">
      {phase === "checking" ? (
        <div className="flex justify-center py-6">
          <Spinner label="Checking session" />
        </div>
      ) : (
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <TextField
            label="Password"
            type="password"
            name="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              if (error) setError(null);
            }}
            aria-describedby={error ? "login-error" : undefined}
          />
          {error ? (
            <p id="login-error" role="alert" className="flex items-start gap-2 rounded-lg border border-line bg-subtle px-3 py-2 text-[13px] text-fg">
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              {error}
            </p>
          ) : null}
          <Button type="submit" variant="primary" block loading={busy} icon={<LogIn />}>
            Sign in
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}
