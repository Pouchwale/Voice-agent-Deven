import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

export type Tone = "white" | "tint" | "subtle";

const TONE: Record<Tone, string> = {
  white: "bg-surface border-line",
  tint: "bg-primary-tint border-primary-tint-strong",
  subtle: "bg-canvas border-line",
};

export function Card({
  tone = "white",
  padded = true,
  className,
  children,
  ...rest
}: {
  tone?: Tone;
  padded?: boolean;
  className?: string;
  children?: ReactNode;
} & HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("rounded-xl border", TONE[tone], padded && "p-4 sm:p-5", className)} {...rest}>
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  actions,
  icon,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-4 flex flex-wrap items-start justify-between gap-3", className)}>
      <div className="flex min-w-0 items-start gap-3">
        {icon}
        <div className="min-w-0">
          <h2 className="font-heading text-base font-semibold">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-[13px] text-muted">{subtitle}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Small neutral icon tile. */
export function CircleIcon({
  tone = "subtle",
  size = "md",
  children,
  className,
}: {
  tone?: "subtle" | "primary" | "brand";
  size?: "sm" | "md" | "lg";
  children: ReactNode;
  className?: string;
}) {
  const dims = size === "sm" ? "size-8 [&_svg]:size-4" : size === "lg" ? "size-12 [&_svg]:size-6" : "size-10 [&_svg]:size-5";
  const toneCls =
    tone === "primary" ? "bg-primary-tint text-primary" : tone === "brand" ? "bg-brand text-white" : "bg-subtle text-subtle-fg";
  return (
    <span className={cn("inline-flex shrink-0 items-center justify-center rounded-lg", toneCls, dims, className)} aria-hidden>
      {children}
    </span>
  );
}
