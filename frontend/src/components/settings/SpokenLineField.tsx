"use client";

import { TriangleAlert } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { TextAreaField } from "@/components/ui/Input";
import { PlayButton, type PreviewState } from "./VoicePreview";

export interface SpokenSample {
  /** e.g. "To a lead we know by name"; the first sample defaults to "Sounds like". */
  label?: string;
  text: string;
}

/**
 * A line the agent says word for word on the phone: textarea, placeholder chips, what the caller will hear
 * ("Sounds like") and a Play button that speaks the first sample in the current (unsaved) voice.
 */
export function SpokenLineField({
  id,
  label,
  hint,
  value,
  onChange,
  error,
  placeholders = [],
  samples,
  softMax,
  rows = 2,
  preview,
}: {
  /** DOM id of the textarea (other sections link straight to it). */
  id: string;
  label: string;
  hint?: ReactNode;
  value: string;
  onChange: (v: string) => void;
  error?: string;
  placeholders?: readonly string[];
  samples: SpokenSample[];
  /** Soft warning past this many characters as heard (placeholders filled in): callers start talking over it. */
  softMax?: number;
  rows?: number;
  preview: { state: PreviewState; play: (text: string) => void; stop: () => void };
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const heard = samples[0]?.text ?? "";
  const long = softMax !== undefined && heard.length > softMax;

  function insert(token: string) {
    const el = ref.current;
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    onChange(value.slice(0, start) + token + value.slice(end));
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      const caret = start + token.length;
      el.setSelectionRange(caret, caret);
    });
  }

  return (
    <div>
      <TextAreaField
        ref={ref}
        id={id}
        label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        error={error}
        rows={rows}
        aside={<span className="tabular">{value.length.toLocaleString("en-IN")} chars</span>}
        hint={
          long ? (
            <>
              {hint}
              <span className="mt-1 flex items-start gap-1.5 font-medium text-fg">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                <span>
                  Long for a greeting ({heard.length} characters as heard): callers often start talking over anything
                  longer than about {softMax}. Shorter feels quicker and more natural.
                </span>
              </span>
            </>
          ) : (
            hint
          )
        }
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {placeholders.length ? (
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label={`Insert a placeholder into ${label}`}>
            <span className="text-[12px] font-medium text-muted">Insert:</span>
            {placeholders.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => insert(p)}
                className="rounded-full border border-line bg-white px-2.5 py-0.5 font-mono text-[12px] font-medium shadow-sm transition-colors hover:bg-canvas active:translate-x-px active:translate-y-px"
              >
                {p}
              </button>
            ))}
          </div>
        ) : null}
        <div className="ml-auto">
          <PlayButton
            state={preview.state}
            onPlay={() => preview.play(heard)}
            onStop={preview.stop}
            disabled={!heard}
            what={label.toLowerCase()}
          />
        </div>
      </div>
      {heard ? (
        <div className="text-mixed mt-2 space-y-0.5 text-[13px] font-normal text-muted">
          {samples.map((s, i) => (
            <p key={i}>
              <span className="font-semibold text-fg">{s.label ?? (i === 0 ? "Sounds like" : "Also")}: </span>
              {s.text}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
