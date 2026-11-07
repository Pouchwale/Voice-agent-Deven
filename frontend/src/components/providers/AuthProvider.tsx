"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { RemoteDisabledNotice } from "@/components/auth/AuthLayout";
import { api } from "@/lib/api";
import { isLoginPath, loginUrlForCurrentPage, setUnauthorizedHandler } from "@/lib/auth";

/**
 * Global 401 handling: any API 401 (outside /api/auth/*) or a 1008 close of /ws/live sends the user to
 * /login?next=<current page> - unless the server has no DASHBOARD_PASSWORD for remote use, in which case a
 * clear notice replaces the app. Never redirects from /login itself, and only once per burst of 401s.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [remoteDisabled, setRemoteDisabled] = useState(false);
  const handling = useRef(false);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (handling.current) return;
      if (isLoginPath(window.location.pathname)) return; // the login page handles its own errors
      handling.current = true;
      void (async () => {
        try {
          const status = await api.auth.status();
          if (!status.password_set && !status.local) {
            setRemoteDisabled(true); // stays "handling": nothing else to do until the server is configured
            return;
          }
          if (status.local || status.authenticated) {
            handling.current = false; // a stale 401 (e.g. it raced a login) - no redirect
            return;
          }
        } catch {
          /* status unreachable: go to the login page, which shows its own errors */
        }
        router.replace(loginUrlForCurrentPage());
        // Allow later redirects once this navigation has settled (avoids a storm from parallel 401s).
        setTimeout(() => {
          handling.current = false;
        }, 1500);
      })();
    });
    return () => setUnauthorizedHandler(null);
  }, [router]);

  if (remoteDisabled) return <RemoteDisabledNotice />;
  return <>{children}</>;
}
