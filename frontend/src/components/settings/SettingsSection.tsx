"use client";

import { useId, type ReactNode } from "react";
import { CircleIcon } from "@/components/ui/Card";
import { Field, TextInput } from "@/components/ui/Input";
import { cn } from "@/lib/cn";

export type SectionTone = "subtle" | "primary" | "brand";

/** Card section with an anchor id (used by the section nav). */
export function SettingsSection({
  id,
  title,
  subtitle,
  icon,
  tone = "subtle",
  actions,
  children,
  className,
}: {
  id: string;
  title: string;
  subtitle?: ReactNode;
  icon: ReactNode;
  tone?: SectionTone;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const headingId = `${id}-heading`;
  return (
    <section
      id={id}
      aria-labelledby={headingId}
      data-anim
      className={cn("scroll-mt-24 rounded-xl border border-line bg-white p-4 sm:p-6", className)}
    >
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <CircleIcon tone={tone} size="sm">{icon}</CircleIcon>
          <div className="min-w-0">
            <h2 id={headingId} className="font-heading text-base font-semibold">
              {title}
            </h2>
            {subtitle ? <p className="mt-0.5 text-[13px] text-muted">{subtitle}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      <div className="space-y-5">{children}</div>
    </section>
  );
}

/** Number input with a unit suffix. Empty input becomes NaN (flagged by validation). */
export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  unit,
  hint,
  error,
  className,
}: {
  label: ReactNode;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  hint?: ReactNode;
  error?: string;
  className?: string;
}) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id} className={className}>
      <div className="relative">
        <TextInput
          id={id}
          type="number"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          value={Number.isFinite(value) ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? Number.NaN : Number(e.target.value))}
          invalid={!!error}
          className={cn("tabular", unit && "pr-16")}
        />
        {unit ? (
          <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 rounded-full border border-line bg-subtle px-2 text-xs font-semibold">
            {unit}
          </span>
        ) : null}
      </div>
    </Field>
  );
}

export function TimeField({
  label,
  value,
  onChange,
  hint,
  error,
}: {
  label: ReactNode;
  value: string;
  onChange: (v: string) => void;
  hint?: ReactNode;
  error?: string;
}) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <TextInput
        id={id}
        type="time"
        step={60}
        value={value}
        onChange={(e) => onChange(e.target.value.slice(0, 5))}
        invalid={!!error}
        className="tabular"
      />
    </Field>
  );
}
