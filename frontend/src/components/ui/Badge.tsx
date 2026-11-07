import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type ChipColor = "neutral" | "white" | "primary" | "primary-solid" | "brand" | "navy" | "dark";

const COLOR: Record<ChipColor, string> = {
  neutral: "bg-subtle text-subtle-fg border-subtle",
  white: "bg-white text-muted border-line",
  primary: "bg-primary-tint text-primary border-primary-tint-strong",
  "primary-solid": "bg-primary text-white border-primary",
  brand: "bg-brand text-white border-brand",
  navy: "bg-navy text-white border-navy",
  dark: "bg-fg text-white border-fg",
};

/** Small rounded-full label chip. */
export function Chip({
  color = "neutral",
  custom,
  size = "md",
  icon,
  children,
  className,
  title,
}: {
  color?: ChipColor;
  /** Raw colour classes (overrides `color`), e.g. interest tier styles. */
  custom?: string;
  size?: "xs" | "sm" | "md";
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  const sz =
    size === "xs"
      ? "text-xs px-2 py-px gap-1 [&_svg]:size-3"
      : size === "sm"
        ? "text-xs px-2 py-0.5 gap-1 [&_svg]:size-3"
        : "text-[13px] px-2.5 py-0.5 gap-1.5 [&_svg]:size-3.5";
  const colorCls = custom ?? COLOR[color];
  return (
    <span
      title={title}
      className={cn("inline-flex items-center whitespace-nowrap rounded-full border font-medium leading-5", colorCls, sz, className)}
    >
      {icon}
      {children}
    </span>
  );
}

/** Small coloured dot used inside chips / status lines. */
export function Dot({ className, live }: { className?: string; live?: boolean }) {
  return <span className={cn("inline-block size-1.5 shrink-0 rounded-full", live && "live-dot", className ?? "bg-muted")} aria-hidden />;
}
