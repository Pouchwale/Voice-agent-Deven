"use client";

import { useId, type ReactNode } from "react";
import { CircleIcon } from "@/components/ui/Card";
import { Field, TextInput } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
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
  // Saved as 24-hour "HH:MM" (what the backend uses); picked as hour / minute / AM-PM, which is easier to read
  // than the browser's own time picker (24-hour on many Windows setups).
  const [h24, minute] = parseTime(value);
  const ampm: "AM" | "PM" = h24 >= 12 ? "PM" : "AM";
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12;
  const update = (h: number, m: number, p: "AM" | "PM") =>
    onChange(`${String((h % 12) + (p === "PM" ? 12 : 0)).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
  const minutes = MINUTES.includes(minute) ? MINUTES : [...MINUTES, minute].sort((a, b) => a - b);
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <div className="grid grid-cols-[1fr_1fr_1fr] gap-2" aria-invalid={!!error || undefined}>
        <Select
          id={id}
          aria-label="Hour"
          value={String(h12)}
          onChange={(v) => update(Number(v), minute, ampm)}
          options={HOURS.map((h) => ({ value: String(h), label: String(h) }))}
          className="tabular"
        />
        <Select
          aria-label="Minute"
          value={String(minute)}
          onChange={(v) => update(h12, Number(v), ampm)}
          options={minutes.map((m) => ({ value: String(m), label: String(m).padStart(2, "0") }))}
          className="tabular"
        />
        <Select<"AM" | "PM">
          aria-label="AM or PM"
          value={ampm}
          onChange={(v) => update(h12, minute, v)}
          options={[
            { value: "AM", label: "AM" },
            { value: "PM", label: "PM" },
          ]}
        />
      </div>
    </Field>
  );
}

const HOURS = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
const MINUTES = Array.from({ length: 12 }, (_, i) => i * 5); // 00, 05 … 55

/** "HH:MM" (24-hour) -> [hour 0-23, minute 0-59]; anything unreadable -> 09:00. */
function parseTime(value: string): [number, number] {
  const m = /^(\d{1,2}):(\d{2})/.exec(value || "");
  if (!m) return [9, 0];
  const h = Math.min(23, Number(m[1]));
  const min = Math.min(59, Number(m[2]));
  return [h, min];
}
