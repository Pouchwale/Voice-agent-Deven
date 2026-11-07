"use client";

import { ChevronDown } from "lucide-react";
import { useId, type ReactNode, type SelectHTMLAttributes } from "react";
import { cn } from "@/lib/cn";
import { Field } from "./Input";

export interface Option<V extends string = string> {
  value: V;
  label: string;
}

export function Select<V extends string = string>({
  options,
  value,
  onChange,
  className,
  pill = false,
  ...rest
}: Omit<SelectHTMLAttributes<HTMLSelectElement>, "onChange" | "value"> & {
  options: Option<V>[];
  value: V;
  onChange: (v: V) => void;
  pill?: boolean;
}) {
  return (
    <div className={cn("relative", className)}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as V)}
        className={cn("field h-9 cursor-pointer appearance-none !py-0 pr-9", pill && "")}
        {...rest}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2" aria-hidden />
    </div>
  );
}

export function SelectField<V extends string = string>({
  label,
  hint,
  error,
  className,
  ...rest
}: Parameters<typeof Select<V>>[0] & { label: ReactNode; hint?: ReactNode; error?: string | null }) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id} className={className}>
      <Select<V> id={id} {...rest} />
    </Field>
  );
}

/** Segmented pill control (radio group). */
export function Segmented<V extends string>({
  options,
  value,
  onChange,
  label,
  size = "md",
  className,
}: {
  options: Array<Option<V> & { icon?: ReactNode; activeClass?: string }>;
  value: V;
  onChange: (v: V) => void;
  label: string;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn("inline-flex flex-wrap items-center gap-0.5 rounded-lg border border-line bg-white p-0.5", className)}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border font-medium transition-colors [&_svg]:size-3.5",
              size === "sm" ? "h-7 px-2.5 text-[13px]" : "h-8 px-3 text-[13px]",
              active ? (o.activeClass ?? "border-primary-tint bg-primary-tint text-primary") : "border-transparent text-muted hover:bg-canvas hover:text-fg",
            )}
          >
            {o.icon}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
