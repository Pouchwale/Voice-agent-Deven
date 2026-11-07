import Image from "next/image";
import { LockKeyhole } from "lucide-react";
import type { ReactNode } from "react";
import { REMOTE_DISABLED_MESSAGE } from "@/lib/auth";

/** Centred card with the logo, used by the login page and the "remote access disabled" notice. */
export function AuthLayout({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          {/* Logo: white wordmark on its logo-blue tile, natural aspect ratio. */}
          <div className="inline-block bg-brand px-3 py-2.5">
            <Image src="/pouchwale_logo.webp" alt="PouchWale" width={1136} height={480} priority style={{ width: 120, height: "auto", display: "block" }} />
          </div>
          <p className="mt-3 font-heading text-sm font-semibold">Voice Agent</p>
        </div>
        <div className="rounded-xl border border-line bg-white p-6">
          <h1 className="font-heading text-xl font-semibold tracking-tight">{title}</h1>
          {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
          <div className="mt-5">{children}</div>
        </div>
      </div>
    </div>
  );
}

export function RemoteDisabledNotice() {
  return (
    <AuthLayout title="Remote access is disabled">
      <div role="alert" className="flex items-start gap-3 rounded-lg border border-line bg-subtle p-3 text-sm text-fg">
        <LockKeyhole className="mt-0.5 size-4 shrink-0" aria-hidden />
        <div>
          <p className="font-medium">{REMOTE_DISABLED_MESSAGE}</p>
          <p className="mt-1 text-[13px] text-subtle-fg">
            This dashboard is being opened from another computer, and no dashboard password is configured on the server. Set
            DASHBOARD_PASSWORD, restart the backend, then reload this page. On the server itself (localhost) no login is needed.
          </p>
        </div>
      </div>
    </AuthLayout>
  );
}
