"use client";

import { LogOut } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { api, keys } from "@/lib/api";
import { LOGIN_PATH, type AuthStatus } from "@/lib/auth";

/** Sidebar footer "Sign out" - only for remote sessions (local use has no login). */
export function SignOutButton() {
  const { data } = useSWR<AuthStatus>(keys.authStatus, { dedupingInterval: 30_000, revalidateOnFocus: true });
  const [busy, setBusy] = useState(false);

  if (!data || data.local || !data.authenticated) return null;

  async function signOut() {
    setBusy(true);
    try {
      await api.auth.logout();
    } catch {
      /* even if the request fails, leave: the login page re-checks the session */
    }
    // Full navigation so caches and sockets don't outlive the session.
    window.location.assign(LOGIN_PATH);
  }

  return (
    <div className="border-t border-line p-3">
      <button
        type="button"
        onClick={() => void signOut()}
        disabled={busy}
        className="flex h-9 w-full items-center gap-3 rounded-lg px-3 text-sm text-subtle-fg transition-colors hover:bg-canvas hover:text-fg disabled:opacity-60"
      >
        <LogOut className="size-4" aria-hidden />
        {busy ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}
