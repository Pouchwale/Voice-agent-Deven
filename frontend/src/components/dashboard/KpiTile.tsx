"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { CountUp } from "@/components/ui/CountUp";
import { cn } from "@/lib/cn";

/**
 * Plain stat card: label, value, caption. `highlight` tints the value (e.g. hot leads in brand blue).
 * `href` makes it a link; `onClick` (without href) makes it a button, e.g. to switch a table filter on the same page.
 */
export function KpiTile({
  label,
  value,
  format,
  caption,
  icon,
  href,
  onClick,
  suffix,
  highlight,
  indicator,
  className,
}: {
  label: string;
  value: number | null;
  format?: (n: number) => string;
  caption?: ReactNode;
  icon: ReactNode;
  href?: string;
  onClick?: () => void;
  suffix?: ReactNode;
  highlight?: "brand" | "primary";
  /** Small element next to the label (e.g. a live dot). */
  indicator?: ReactNode;
  /** Grid placement, e.g. "col-span-2". */
  className?: string;
}) {
  const hasValue = value !== null && value !== undefined && Number.isFinite(value);
  // Spans (not p/div) so the same body is valid inside a link, a div or a button.
  const body = (
    <>
      <span className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[13px] font-medium text-muted">
          {indicator}
          {label}
        </span>
        <span className="text-muted [&_svg]:size-4" aria-hidden>
          {icon}
        </span>
      </span>
      <span
        className={cn(
          "mt-2 flex items-baseline gap-1 font-heading text-[28px] leading-none font-semibold tracking-tight",
          highlight === "brand" && "text-brand",
          highlight === "primary" && "text-primary",
        )}
      >
        {/* null / NaN / Infinity (e.g. a rate over zero calls) render as a dash, never "NaN%" */}
        {!hasValue ? <span aria-label="No data">—</span> : <CountUp value={value} format={format} />}
        {hasValue && suffix ? <span className="text-base font-medium text-muted">{suffix}</span> : null}
      </span>
      {caption ? <span className="mt-2 block text-[13px] text-muted">{caption}</span> : null}
    </>
  );
  const cls = cn(
    "block h-full rounded-xl border border-line bg-white p-4 transition-colors",
    (href || onClick) && "hover:border-primary-tint-strong hover:bg-canvas",
    className,
  );
  if (href) {
    return (
      <Link href={href} className={cls}>
        {body}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={cn(cls, "w-full text-left")}>
        {body}
      </button>
    );
  }
  return <div className={cls}>{body}</div>;
}
