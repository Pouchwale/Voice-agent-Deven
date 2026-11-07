"use client";

import { CircleAlert, Search, X } from "lucide-react";
import { useId, type InputHTMLAttributes, type ReactNode, type Ref, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

/** Label + hint + error wrapper. Pass the generated id to the control. */
export function Field({
  label,
  hint,
  error,
  htmlFor,
  children,
  className,
  aside,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
  aside?: ReactNode;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <div className="flex items-end justify-between gap-2">
        <label htmlFor={htmlFor} className="field-label">
          {label}
        </label>
        {aside ? <div className="mb-1.5 text-xs font-medium text-muted">{aside}</div> : null}
      </div>
      {children}
      {error ? (
        <p className="mt-1.5 flex items-start gap-1.5 text-[13px] font-medium text-fg" role="alert">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {error}
        </p>
      ) : hint ? (
        <p className="mt-1.5 text-[13px] font-normal text-muted">{hint}</p>
      ) : null}
    </div>
  );
}

export function TextInput({
  className,
  invalid,
  ref,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean; ref?: Ref<HTMLInputElement> }) {
  return <input ref={ref} className={cn("field", className)} aria-invalid={invalid || undefined} {...rest} />;
}

export function TextField({
  label,
  hint,
  error,
  className,
  inputClassName,
  ref,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  inputClassName?: string;
  ref?: Ref<HTMLInputElement>;
}) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} htmlFor={rest.id ?? id} className={className}>
      <TextInput ref={ref} id={rest.id ?? id} invalid={!!error} className={inputClassName} {...rest} />
    </Field>
  );
}

export function TextArea({
  className,
  invalid,
  ref,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { invalid?: boolean; ref?: Ref<HTMLTextAreaElement> }) {
  return (
    <textarea
      ref={ref}
      className={cn("field text-mixed min-h-[96px] resize-y leading-relaxed", className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  );
}

export function TextAreaField({
  label,
  hint,
  error,
  className,
  inputClassName,
  aside,
  ref,
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  inputClassName?: string;
  aside?: ReactNode;
  ref?: Ref<HTMLTextAreaElement>;
}) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} htmlFor={rest.id ?? id} className={className} aside={aside}>
      <TextArea ref={ref} id={rest.id ?? id} invalid={!!error} className={inputClassName} {...rest} />
    </Field>
  );
}

/** Search box with icon + clear button. */
export function SearchInput({
  value,
  onChange,
  placeholder = "Search…",
  label = "Search",
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  label?: string;
  className?: string;
}) {
  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
      <input
        type="search"
        aria-label={label}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="field h-9 !py-0 pr-9 pl-9 [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute top-1/2 right-2 -translate-y-1/2 rounded-md p-1 text-muted hover:bg-subtle [&_svg]:size-4"
        >
          <X aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
