"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { CountUp } from "@/components/ui/CountUp";
import { cn } from "@/lib/cn";

/** Plain stat card: label, value, caption. `highlight` tints the value (e.g. hot leads in brand blue). */
export function KpiTile({
  label,
  value,
  format,
  caption,
  icon,
  href,
  suffix,
  highlight,
  indicator,
}: {
  label: string;
  value: number | null;
  format?: (n: number) => string;
  caption?: ReactNode;
  icon: ReactNode;
  href?: string;
  suffix?: ReactNode;
  highlight?: "brand" | "primary";
  /** Small element next to the label (e.g. a live dot). */
  indicator?: ReactNode;
}) {
  const hasValue = value !== null && value !== undefined && Number.isFinite(value);
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-[13px] font-medium text-muted">
          {indicator}
          {label}
        </p>
        <span className="text-muted [&_svg]:size-4" aria-hidden>
          {icon}
        </span>
      </div>
      <p
        className={cn(
          "mt-2 flex items-baseline gap-1 font-heading text-[28px] leading-none font-semibold tracking-tight",
          highlight === "brand" && "text-brand",
          highlight === "primary" && "text-primary",
        )}
      >
        {/* null / NaN / Infinity (e.g. a rate over zero calls) render as a dash, never "NaN%" */}
        {!hasValue ? <span aria-label="No data">—</span> : <CountUp value={value} format={format} />}
        {hasValue && suffix ? <span className="text-base font-medium text-muted">{suffix}</span> : null}
      </p>
      {caption ? <p className="mt-2 text-[13px] text-muted">{caption}</p> : null}
    </>
  );
  const cls = cn("block h-full rounded-xl border border-line bg-white p-4 transition-colors", href && "hover:border-primary-tint-strong hover:bg-canvas");
  return href ? (
    <Link href={href} className={cls}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}
