"use client";

import { ClipboardCheck, GraduationCap, RotateCcw, ScrollText, TriangleAlert, Undo2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { SOURCE_LABEL } from "@/components/review/labels";
import { ValueChange } from "@/components/review/ValueChange";
import { Chip } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Field, TextAreaField, TextInput } from "@/components/ui/Input";
import { ConfirmDialog } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import { ErrorState } from "@/components/ui/States";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { useToast } from "@/components/ui/Toast";
import { ApiError, api, errorMessage, keys } from "@/lib/api";
import { formatShortDateTime, pluralize } from "@/lib/format";
import type { AgentSettings, ReviewMethod, ReviewModelChoice, SettingsChangeEntry, SettingsHistory, UndoConflict } from "@/lib/types";
import { LEARNED_RULES_MAX, type SectionProps } from "./model";
import { NumberField, SettingsSection } from "./SettingsSection";

// ---------------------------------------------------------------------------------------------- Learned rules

export function LearnedRulesSection({ form, set, errors }: SectionProps) {
  const lines = form.learned_rules.split("\n").filter((l) => l.trim()).length;
  return (
    <SettingsSection
      id="learned-rules"
      title="Learned rules"
      subtitle="Rules learned from reviews of your own calls. Approving a review suggestion adds its rule here; every call follows them."
      icon={<GraduationCap />}
      actions={
        <Link href="/review" className="text-[13px] font-medium text-primary hover:underline">
          Open Call review
        </Link>
      }
    >
      <TextAreaField
        id="learned_rules"
        label="Rules"
        value={form.learned_rules}
        onChange={(e) => set("learned_rules", e.target.value)}
        error={errors.learned_rules}
        rows={8}
        inputClassName="min-h-[160px] text-mixed"
        placeholder="- Keep the first pitch to one short sentence, then ask one question."
        aside={
          <span className="tabular">
            {form.learned_rules.length.toLocaleString("en-IN")} / {LEARNED_RULES_MAX.toLocaleString("en-IN")}
          </span>
        }
        hint={
          lines
            ? `${pluralize(lines, "rule")}. One rule per line; edit or delete any of them (every change can be undone under Change history).`
            : "Empty for now. Approve a suggestion on the Call review page, or write your own rules (one per line)."
        }
      />
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------------------------- Review agent

const OTHER = "__other__";

export function ReviewAgentSection({ form, set, errors }: SectionProps) {
  const { data: models } = useSWR<{ models: ReviewModelChoice[] }>(keys.reviewModels, { revalidateOnFocus: false });
  const { data: method } = useSWR<ReviewMethod>(keys.reviewMethod, { revalidateOnFocus: false });
  const choices = useMemo(() => models?.models ?? [], [models]);
  const listed = choices.some((m) => m.id === form.review_model);
  // "Other model…": any OpenAI model name can be typed in (e.g. a newer one than the list knows)
  const [typing, setTyping] = useState(false);
  const custom = typing || (!!choices.length && !listed);
  const selected = choices.find((m) => m.id === form.review_model);
  const defaultRubric = method?.default_rubric ?? "";

  return (
    <SettingsSection
      id="review-agent"
      title="Review agent"
      subtitle="A second AI that reviews finished calls, finds where and why customers were lost (with quotes as proof) and suggests changes. Nothing changes until you approve it."
      icon={<ClipboardCheck />}
      actions={
        <Link href="/review" className="text-[13px] font-medium text-primary hover:underline">
          Open Call review
        </Link>
      }
    >
      <Switch
        checked={form.review_enabled}
        onChange={(v) => set("review_enabled", v)}
        label="Automatic reviews"
        description={`Reviews the calls by itself every ${Number.isFinite(form.review_every_calls) ? form.review_every_calls : "few"} calls. “Run review now” on the Call review page works either way.`}
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <NumberField
          label="Review after every"
          value={form.review_every_calls}
          onChange={(v) => set("review_every_calls", v)}
          min={3}
          max={50}
          unit="calls"
          error={errors.review_every_calls}
          hint="Phone calls in which the customer spoke. Unanswered calls, voicemail and browser tests don't count."
        />
        <NumberField
          label="Proof needed"
          value={form.review_min_evidence}
          onChange={(v) => set("review_min_evidence", v)}
          min={1}
          max={5}
          unit="calls"
          error={errors.review_min_evidence}
          hint="A problem becomes a suggestion only when it is proven (quotes from the calls) in at least this many calls."
        />
      </div>

      <Switch
        checked={form.review_include_inbound}
        onChange={(v) => set("review_include_inbound", v)}
        label="Review inbound calls too"
        description="Outbound calls are always reviewed."
      />

      <Field
        label="Review model"
        htmlFor="review_model"
        error={errors.review_model}
        hint={
          selected?.available === false
            ? undefined
            : "Used only for reviews (live calls keep their own model). The gpt-5.x models judge more carefully than gpt-4o-mini; switch once approved."
        }
      >
        <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Select
            id="review_model"
            aria-label="Review model"
            value={custom ? OTHER : form.review_model}
            onChange={(v) => {
              if (v === OTHER) {
                setTyping(true);
                return;
              }
              setTyping(false);
              set("review_model", v);
            }}
            options={[
              ...(choices.length ? [] : [{ value: form.review_model, label: form.review_model }]),
              ...choices.map((m) => ({
                value: m.id,
                label: `${m.id}${m.available === false ? " (not on this OpenAI key)" : ""}`,
              })),
              { value: OTHER, label: "Other model…" },
            ]}
          />
          {custom ? (
            <TextInput
              aria-label="Model name"
              value={form.review_model}
              onChange={(e) => set("review_model", e.target.value)}
              placeholder="e.g. gpt-5.5-pro"
              invalid={!!errors.review_model}
              spellCheck={false}
              autoCapitalize="none"
            />
          ) : (
            <p className="self-center text-[13px] text-muted">{selected?.note ?? ""}</p>
          )}
        </div>
        {selected?.available === false ? (
          <p className="mt-2 flex items-start gap-1.5 text-[13px] font-medium text-fg">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            This OpenAI key can&apos;t use {selected.id} yet: reviews would fail. Pick another model or enable it in your OpenAI account.
          </p>
        ) : null}
      </Field>

      <TextAreaField
        id="review_rubric"
        label="How calls are judged"
        value={form.review_rubric}
        onChange={(e) => set("review_rubric", e.target.value)}
        error={errors.review_rubric}
        rows={14}
        inputClassName="min-h-[260px] font-mono !text-[13px]"
        hint="The review agent's rules: what counts as losing a customer because of the agent, which normal reasons never count, the mistake types and the proof it must give."
        aside={
          defaultRubric && form.review_rubric !== defaultRubric ? (
            <button
              type="button"
              onClick={() => set("review_rubric", defaultRubric)}
              className="inline-flex items-center gap-1 font-medium text-primary hover:underline [&_svg]:size-3"
            >
              <RotateCcw aria-hidden /> Restore default
            </button>
          ) : null
        }
      />
    </SettingsSection>
  );
}

// ---------------------------------------------------------------------------------------------- Change history

const PAGE = 15;

export function ChangeHistorySection({
  form,
  set,
  errors,
  dirty,
  onApplied,
}: SectionProps & { dirty: boolean; onApplied: (settings: AgentSettings) => void }) {
  const toast = useToast();
  const { mutate: mutateGlobal } = useSWRConfig();
  const { data, error, mutate } = useSWR<SettingsHistory>(keys.settingsHistory, { revalidateOnFocus: false });
  const [shown, setShown] = useState(PAGE);
  const [busy, setBusy] = useState<number | null>(null);
  const [conflict, setConflict] = useState<{ entry: SettingsChangeEntry; message: string; items: UndoConflict[] } | null>(null);
  const items = data?.items ?? [];

  async function undo(entry: SettingsChangeEntry, force = false) {
    if (dirty) {
      toast({ tone: "info", title: "Save or discard your changes first", description: "Undo replaces settings, so unsaved edits would be lost." });
      return;
    }
    setBusy(entry.id);
    try {
      const res = await api.settings.undo(entry.id, force);
      onApplied(res.settings);
      setConflict(null);
      await Promise.all([mutate(), mutateGlobal(keys.settings, res.settings, { revalidate: false })]);
      toast({ tone: "success", title: "Undone", description: `${entry.label}. Changed your mind? Undo the undo below.` });
    } catch (err) {
      const conflicts = err instanceof ApiError && err.status === 409 ? conflictsOf(err.data) : null;
      if (conflicts?.length && !force) {
        setConflict({ entry, message: errorMessage(err), items: conflicts });
      } else {
        toast({ tone: "error", title: "Couldn't undo", description: errorMessage(err) });
        void mutate();
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <SettingsSection
      id="history"
      title="Change history"
      subtitle={`Every change to these settings: your saves, approved review suggestions, resets and undos. Undo puts the earlier values back.`}
      icon={<ScrollText />}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <NumberField
          label="Keep history for"
          value={form.history_days}
          onChange={(v) => set("history_days", v)}
          min={1}
          max={365}
          unit="days"
          error={errors.history_days}
          hint="Older changes are removed from the list (and can no longer be undone)."
        />
      </div>

      {error && !data ? (
        <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load the change history" compact />
      ) : !data ? (
        <div className="space-y-2" role="status" aria-label="Loading the change history">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-14 rounded-lg" />
          ))}
        </div>
      ) : !items.length ? (
        <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">
          No changes in the last {pluralize(data.days, "day")}.
        </p>
      ) : (
        <div>
          <ul className="divide-y divide-line rounded-lg border border-line">
            {items.slice(0, shown).map((entry) => (
              <HistoryRow key={entry.id} entry={entry} busy={busy === entry.id} disabled={busy !== null} onUndo={() => void undo(entry)} />
            ))}
          </ul>
          {items.length > shown ? (
            <div className="mt-3 flex justify-center">
              <Button variant="secondary" size="sm" onClick={() => setShown((n) => n + PAGE)}>
                Show more ({items.length - shown})
              </Button>
            </div>
          ) : null}
        </div>
      )}

      <ConfirmDialog
        open={!!conflict}
        onClose={() => busy === null && setConflict(null)}
        onConfirm={() => conflict && void undo(conflict.entry, true)}
        title="Undo anyway?"
        body={
          conflict ? (
            <div className="space-y-3">
              <p>{conflict.message}</p>
              <ul className="space-y-2">
                {conflict.items.map((c) => (
                  <li key={c.key} className="rounded-lg border border-line p-2.5">
                    <p className="text-[13px] font-semibold">{c.label}</p>
                    <p className="mt-1 text-[12px] text-muted">Now → after the undo:</p>
                    <ValueChange before={c.now} after={c.restore} className="mt-1" />
                  </li>
                ))}
              </ul>
            </div>
          ) : null
        }
        confirmLabel="Undo anyway"
        busy={busy !== null}
      />
    </SettingsSection>
  );
}

function conflictsOf(data: unknown): UndoConflict[] | null {
  if (!data || typeof data !== "object" || !("conflicts" in data)) return null;
  const list = (data as { conflicts: unknown }).conflicts;
  return Array.isArray(list) ? (list as UndoConflict[]) : null;
}

function HistoryRow({
  entry,
  busy,
  disabled,
  onUndo,
}: {
  entry: SettingsChangeEntry;
  busy: boolean;
  disabled: boolean;
  onUndo: () => void;
}) {
  const [open, setOpen] = useState(false);
  const undone = entry.undone_by !== null && entry.undone_by !== undefined;
  const changes = entry.changes ?? [];
  return (
    <li className="px-3 py-3 sm:px-4">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Chip size="xs" color={entry.source === "review" ? "primary" : entry.source === "undo" ? "white" : "neutral"}>
              {SOURCE_LABEL[entry.source] ?? entry.source}
            </Chip>
            <span className="tabular text-[12px] text-muted">{formatShortDateTime(entry.created_at)}</span>
            {undone ? (
              <Chip size="xs" color="white">
                Undone
              </Chip>
            ) : null}
          </div>
          <p className="text-mixed mt-1 text-sm font-medium break-words">{entry.label}</p>
          {changes.length ? (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              className="mt-1 text-[12px] font-medium text-primary hover:underline"
            >
              {open ? "Hide details" : `What changed (${pluralize(changes.length, "setting")})`}
            </button>
          ) : null}
        </div>
        {!undone && changes.length ? (
          <Button variant="secondary" size="sm" icon={<Undo2 />} onClick={onUndo} loading={busy} disabled={disabled && !busy}>
            Undo
          </Button>
        ) : null}
      </div>
      {open ? (
        <ul className="mt-3 space-y-3">
          {changes.map((c) => (
            <li key={c.key} className="rounded-lg bg-canvas p-2.5">
              <p className="text-[12px] font-semibold text-subtle-fg">{c.label}</p>
              <ValueChange before={c.old} after={c.new} className="mt-1" />
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
