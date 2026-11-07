"use client";

import { CircleAlert, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button } from "./Button";
import { CircleIcon } from "./Card";

export function EmptyState({
  icon,
  tone = "subtle",
  title,
  body,
  action,
  className,
}: {
  icon: ReactNode;
  tone?: "subtle" | "primary" | "brand";
  title: string;
  body?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center px-6 py-12 text-center", className)}>
      <CircleIcon tone={tone} size="lg">
        {icon}
      </CircleIcon>
      <h3 className="mt-3 font-heading text-base font-semibold">{title}</h3>
      {body ? <p className="mt-1 max-w-md text-sm text-muted">{body}</p> : null}
      {action ? <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  title = "Couldn't load this",
  compact = false,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-xl border border-line bg-subtle",
        compact ? "p-3" : "p-4 sm:p-5",
        className,
      )}
    >
      <CircleAlert className="size-5 shrink-0 text-fg" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-[13px] text-subtle-fg">{errorMessage(error)}</p>
      </div>
      {onRetry ? (
        <Button size="sm" variant="secondary" icon={<RefreshCw />} onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}
