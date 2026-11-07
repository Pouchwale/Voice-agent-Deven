"use client";

import { ArrowDown, ArrowUp, ListChecks, Plus, Trash2 } from "lucide-react";
import { useRef } from "react";
import { Button, IconButton } from "@/components/ui/Button";
import { TextInput } from "@/components/ui/Input";
import type { SectionProps } from "./model";
import { SettingsSection } from "./SettingsSection";

export function QuestionsSection({ form, set }: SectionProps) {
  const listRef = useRef<HTMLOListElement>(null);
  const questions = form.qualification_questions;

  function focusLater(selector: string) {
    requestAnimationFrame(() => listRef.current?.querySelector<HTMLElement>(selector)?.focus());
  }

  function update(i: number, value: string) {
    const next = questions.slice();
    next[i] = value;
    set("qualification_questions", next);
  }

  function move(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= questions.length) return;
    const next = questions.slice();
    [next[i], next[j]] = [next[j], next[i]];
    set("qualification_questions", next);
    // keep focus on the same control of the moved row (or its sibling when it hits an edge)
    const edge = (dir === -1 && j === 0) || (dir === 1 && j === questions.length - 1);
    focusLater(`[data-q="${j}"][data-action="${edge ? (dir === -1 ? "down" : "up") : dir === -1 ? "up" : "down"}"]`);
  }

  function remove(i: number) {
    const next = questions.filter((_, k) => k !== i);
    set("qualification_questions", next);
    if (next.length) focusLater(`[data-q="${Math.min(i, next.length - 1)}"][data-action="input"]`);
  }

  function add() {
    set("qualification_questions", [...questions, ""]);
    focusLater(`[data-q="${questions.length}"][data-action="input"]`);
  }

  return (
    <SettingsSection
      id="qualification"
      title="Qualification questions"
      subtitle="Asked naturally during the call. Answers show up as a checklist on every call summary."
      icon={<ListChecks />}
      tone="subtle"
      actions={
        <Button size="sm" variant="secondary" icon={<Plus />} onClick={add}>
          Add question
        </Button>
      }
    >
      {questions.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm font-normal text-muted">
          No questions yet. Add what your sales team needs to know - product type, monthly quantity, current supplier, timeline.
        </p>
      ) : (
        <ol ref={listRef} className="space-y-2.5">
          {questions.map((q, i) => (
            <li key={i} className="flex items-center gap-2">
              <span
                className="tabular inline-flex size-8 shrink-0 items-center justify-center rounded-full border border-line bg-canvas text-[13px] font-semibold"
                aria-hidden
              >
                {i + 1}
              </span>
              <TextInput
                data-q={i}
                data-action="input"
                aria-label={`Question ${i + 1}`}
                value={q}
                placeholder="e.g. Aap kaunsa product pack karte hain?"
                onChange={(e) => update(i, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    if (i === questions.length - 1 && q.trim()) add();
                  }
                }}
                className="text-mixed min-w-0 flex-1"
              />
              <div className="flex shrink-0 items-center gap-1">
                <IconButton
                  data-q={i}
                  data-action="up"
                  label={`Move question ${i + 1} up`}
                  icon={<ArrowUp />}
                  size="sm"
                  disabled={i === 0}
                  onClick={() => move(i, -1)}
                />
                <IconButton
                  data-q={i}
                  data-action="down"
                  label={`Move question ${i + 1} down`}
                  icon={<ArrowDown />}
                  size="sm"
                  disabled={i === questions.length - 1}
                  onClick={() => move(i, 1)}
                />
                <IconButton
                  data-q={i}
                  data-action="remove"
                  label={`Remove question ${i + 1}`}
                  icon={<Trash2 />}
                  size="sm"
                  variant="danger"
                  onClick={() => remove(i)}
                />
              </div>
            </li>
          ))}
        </ol>
      )}
      <p className="text-[13px] font-normal text-muted">Blank questions are dropped when you save.</p>
    </SettingsSection>
  );
}
