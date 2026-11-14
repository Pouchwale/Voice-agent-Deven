"use client";

import { BookOpen, Flame, MessageSquare, ShieldCheck } from "lucide-react";
import { useRef } from "react";
import { TextAreaField } from "@/components/ui/Input";
import { fillPlaceholders, PLACEHOLDERS, type SectionProps } from "./model";
import { SettingsSection } from "./SettingsSection";

function CharCount({ value }: { value: string }) {
  return <span className="tabular">{value.length.toLocaleString("en-IN")} chars</span>;
}

export function CallScriptSection({ form, set, errors }: SectionProps) {
  const openingRef = useRef<HTMLTextAreaElement>(null);

  function insertPlaceholder(token: string) {
    const el = openingRef.current;
    const value = form.opening_line;
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    const next = value.slice(0, start) + token + value.slice(end);
    set("opening_line", next);
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      const caret = start + token.length;
      el.setSelectionRange(caret, caret);
    });
  }

  return (
    <SettingsSection
      id="script"
      title="Call script"
      subtitle="Outbound calls: how the agent opens, why it is calling, and how it wraps up. Inbound calls are set up under Calling."
      icon={<MessageSquare />}
      tone="subtle"
    >
      <div>
        <TextAreaField
          ref={openingRef}
          id="opening_line"
          label="Opening line"
          value={form.opening_line}
          onChange={(e) => set("opening_line", e.target.value)}
          error={errors.opening_line}
          rows={3}
          aside={<CharCount value={form.opening_line} />}
        />
        <div className="mt-2 flex flex-wrap items-center gap-2" role="group" aria-label="Insert placeholder">
          <span className="text-[12px] font-medium text-muted">Insert:</span>
          {PLACEHOLDERS.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => insertPlaceholder(p)}
              className="rounded-full border border-line bg-white px-2.5 py-0.5 font-mono text-[12px] font-medium shadow-sm transition-colors hover:bg-canvas active:translate-x-px active:translate-y-px"
            >
              {p}
            </button>
          ))}
        </div>
        {form.opening_line.trim() ? (
          <p className="text-mixed mt-2 text-[13px] font-normal text-muted">
            <span className="font-semibold text-fg">Sounds like: </span>
            {fillPlaceholders(form.opening_line, form)}
          </p>
        ) : null}
      </div>

      <TextAreaField
        label="Call purpose"
        hint="One or two lines: what the agent is trying to achieve on this call."
        value={form.call_purpose}
        onChange={(e) => set("call_purpose", e.target.value)}
        rows={3}
        aside={<CharCount value={form.call_purpose} />}
      />

      <TextAreaField
        id="closing_line"
        label="Closing line"
        hint="Said when an outbound call reaches the max call length (inbound calls have their own under Calling)."
        value={form.closing_line}
        onChange={(e) => set("closing_line", e.target.value)}
        rows={2}
        aside={<CharCount value={form.closing_line} />}
      />
    </SettingsSection>
  );
}

export function KnowledgeBaseSection({ form, set }: SectionProps) {
  return (
    <SettingsSection
      id="knowledge"
      title="Knowledge base"
      subtitle="Facts the agent can use: products, minimum order quantities, lead times, pricing guidance, contact details."
      icon={<BookOpen />}
      tone="subtle"
    >
      <TextAreaField
        label="Knowledge base"
        value={form.knowledge_base}
        onChange={(e) => set("knowledge_base", e.target.value)}
        rows={14}
        inputClassName="min-h-[280px] font-mono !text-[13px]"
        aside={<CharCount value={form.knowledge_base} />}
        hint="Plain text or bullet points. Keep it factual. The agent won't invent anything that isn't here."
      />
    </SettingsSection>
  );
}

export function ObjectionsSection({ form, set }: SectionProps) {
  return (
    <SettingsSection
      id="objections"
      title="Objection handling"
      subtitle="How to respond to price, MOQ, lead-time and 'we already have a supplier' pushback."
      icon={<ShieldCheck />}
      tone="subtle"
    >
      <TextAreaField
        label="Objection playbook"
        value={form.objection_handling}
        onChange={(e) => set("objection_handling", e.target.value)}
        rows={10}
        inputClassName="min-h-[200px]"
        aside={<CharCount value={form.objection_handling} />}
      />
    </SettingsSection>
  );
}

export function InterestRulesSection({ form, set }: SectionProps) {
  return (
    <SettingsSection
      id="interest"
      title="Interest rules"
      subtitle="How each call gets scored: rating 1-10 and cold / warm / hot."
      icon={<Flame />}
      tone="subtle"
    >
      <TextAreaField
        label="Scoring rules"
        value={form.interest_rules}
        onChange={(e) => set("interest_rules", e.target.value)}
        rows={8}
        inputClassName="min-h-[170px]"
        aside={<CharCount value={form.interest_rules} />}
        hint="Used by the post-call analysis that decides which leads reach your sales team."
      />
    </SettingsSection>
  );
}
