"use client";

import Link from "next/link";
import { LoaderCircle } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import { cn } from "@/lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "navy";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-primary text-white hover:bg-primary-hover",
  secondary: "bg-white text-fg border-line hover:bg-canvas",
  ghost: "bg-transparent text-fg hover:bg-subtle",
  // destructive actions: outlined neutral (no colour as the signal); deletes also confirm
  danger: "bg-white text-fg border-fg hover:bg-subtle",
  navy: "bg-navy text-white hover:bg-fg",
};

const SIZE: Record<ButtonSize, string> = {
  sm: "!h-8 !px-3 text-[13px] [&_svg]:size-3.5",
  md: "[&_svg]:size-4",
  lg: "!h-10 !px-4 [&_svg]:size-4",
};

export function buttonClass({
  variant = "secondary",
  size = "md",
  block = false,
  className,
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  block?: boolean;
  className?: string;
} = {}): string {
  return cn("btn", VARIANT[variant], SIZE[size], block && "w-full", className);
}

type CommonProps = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  block?: boolean;
  icon?: ReactNode;
  iconRight?: ReactNode;
  loading?: boolean;
  className?: string;
  children?: ReactNode;
};

export type ButtonProps = CommonProps &
  Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className" | "children"> & { ref?: Ref<HTMLButtonElement> };

export function Button({
  variant,
  size,
  block,
  icon,
  iconRight,
  loading,
  className,
  children,
  disabled,
  type = "button",
  ref,
  ...rest
}: ButtonProps) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={buttonClass({ variant, size, block, className })}
      {...rest}
    >
      {loading ? <LoaderCircle className="spin" aria-hidden /> : icon}
      {children}
      {iconRight}
    </button>
  );
}

export function ButtonLink({
  href,
  variant,
  size,
  block,
  icon,
  iconRight,
  className,
  children,
  external,
  download,
  onClick,
}: CommonProps & { href: string; external?: boolean; download?: boolean; onClick?: () => void }) {
  const cls = buttonClass({ variant, size, block, className });
  if (external || download) {
    return (
      <a
        href={href}
        className={cls}
        onClick={onClick}
        download={download || undefined}
        target={external && !download ? "_blank" : undefined}
        rel={external ? "noopener noreferrer" : undefined}
      >
        {icon}
        {children}
        {iconRight}
      </a>
    );
  }
  return (
    <Link href={href} className={cls} onClick={onClick}>
      {icon}
      {children}
      {iconRight}
    </Link>
  );
}

/** Square icon-only button. `label` is required for screen readers. */
export function IconButton({
  label,
  icon,
  variant = "ghost",
  size = "md",
  className,
  type = "button",
  ref,
  ...rest
}: {
  label: string;
  icon: ReactNode;
  variant?: ButtonVariant;
  size?: "sm" | "md" | "lg";
  className?: string;
  ref?: Ref<HTMLButtonElement>;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className" | "children">) {
  const dims =
    size === "sm" ? "!h-8 !w-8 [&_svg]:size-4" : size === "lg" ? "!h-10 !w-10 [&_svg]:size-5" : "!h-9 !w-9 [&_svg]:size-4";
  const v = variant === "danger" ? "bg-transparent text-muted hover:bg-subtle hover:text-fg" : VARIANT[variant];
  return (
    <button ref={ref} type={type} aria-label={label} title={label} className={cn("btn !p-0", v, dims, className)} {...rest}>
      {icon}
    </button>
  );
}
