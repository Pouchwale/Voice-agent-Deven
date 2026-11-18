"use client";

import { Check, ExternalLink, Pencil, Quote, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useSWRConfig } from "swr";
import { Chip } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextAreaField } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { ApiError, api, errorMessage, keys } from "@/lib/api";
import { lineDiff } from "@/lib/diff";
import { formatShortDateTime, pluralize } from "@/lib/format";
import type { ReviewEvidence, ReviewSuggestion } from "@/lib/types";
import { ValueChange } from "./ValueChange";

/** Refresh everything a decision touches (the live review.update event follows, this is just quicker). */
export function useRefreshReview() {
  const { mutate } = useSWRConfig();
  return () =>
    Promise.all([
      mutate(keys.reviewStatus),
      mutate((key) => typeof key === "string" && (key.startsWith("/api/review/suggestions") || key.startsWith(keys.reviewRuns))),
      mutate(keys.settings),
      mutate(keys.settingsHistory),
    ]);
}

const asEditable = (v: unknown): string => (Array.isArray(v) ? v.map(String).join("\n") : typeof v === "string" ? v : "");

/** A text the person edits back into the setting's shape (question lists are one per line). */
const fromEditable = (text: string, like: unknown): unknown =>
  Array.isArray(like) ? text.split("\n").map((l) => l.replace(/^[\s\-•]+/, "").trim()).filter(Boolean) : text;

/**
 * The setting changed after the suggestion was made. If the suggestion only ADDS lines, offer the current value
 * with those lines added (nothing the team wrote meanwhile is lost); otherwise start from the suggestion.
 */
function mergedDraft(s: ReviewSuggestion, current: unknown): string {
  if (typeof s.base_value === "string" && typeof s.proposed_value === "string" && typeof current === "string") {
    const { removed, added } = lineDiff(s.base_value, s.proposed_value);
    if (!removed.length && added.length) return [current.trimEnd(), ...added].filter((l) => l.trim()).join("\n");
  }
  return asEditable(s.proposed_value);
}

export function SuggestionCard({ s, onOpenRun }: { s: ReviewSuggestion; onOpenRun: (runId: number) => void }) {
  const toast = useToast();
  const refresh = useRefreshReview();
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [draftForce, setDraftForce] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [note, setNote] = useState("");
  const [stale, setStale] = useState<{ message: string; current: unknown } | null>(null);

  const calls = new Set(s.evidence.map((e) => e.call_uuid)).size;

  async function approve(value?: unknown, force = false) {
    setBusy("approve");
    try {
      const out = await api.review.approve(s.id, value === undefined ? { force } : { value, force });
      setEditOpen(false);
      setStale(null);
      toast({
        tone: "success",
        title: `Approved: ${s.field_label} updated`,
        description: out.decision_note ? `${out.decision_note}. It applies from the next call.` : "It applies from the next call.",
        action: { label: "Undo in Change history", href: "/settings#history" },
      });
      await refresh();
    } catch (err) {
      const data = err instanceof ApiError ? (err.data as { stale?: boolean; current?: unknown } | undefined) : undefined;
      if (err instanceof ApiError && err.status === 409 && data?.stale) {
        setEditOpen(false);
        setStale({ message: err.detail, current: data.current });
      } else {
        toast({ tone: "error", title: "Couldn't approve", description: errorMessage(err) });
        if (err instanceof ApiError && (err.status === 404 || err.status === 409)) void refresh();
      }
    } finally {
      setBusy(null);
    }
  }

  async function reject() {
    setBusy("reject");
    try {
      await api.review.reject(s.id, note.trim() || undefined);
      setRejectOpen(false);
      toast({ tone: "success", title: "Rejected", description: "Nothing was changed. The review agent won't suggest this exact change again." });
      await refresh();
    } catch (err) {
      toast({ tone: "error", title: "Couldn't reject", description: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  }

  function openEdit(text: string, force: boolean) {
    setDraft(text);
    setDraftForce(force);
    setStale(null);
    setEditOpen(true);
  }

  return (
    <Card className="space-y-4" data-anim>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-mixed font-heading text-base font-semibold">{s.title}</h3>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-muted">
            <Chip size="xs" color="primary">
              {s.field_label}
            </Chip>
            <span>
              Proven in {pluralize(calls, "call")} ·{" "}
              <button type="button" onClick={() => onOpenRun(s.run_id)} className="font-medium text-primary hover:underline">
                review #{s.run_id}
              </button>{" "}
              · {formatShortDateTime(s.created_at)}
            </span>
          </p>
        </div>
      </div>

      {s.rationale ? <p className="text-mixed text-sm">{s.rationale}</p> : null}
      {s.expected_effect ? (
        <p className="text-mixed text-[13px] text-subtle-fg">
          <span className="font-semibold text-fg">Expected effect: </span>
          {s.expected_effect}
        </p>
      ) : null}

      <div className="rounded-lg border border-line p-3">
        <p className="mb-2 text-[12px] font-semibold tracking-wide text-muted uppercase">The change</p>
        <ValueChange before={s.base_value} after={s.proposed_value} />
      </div>

      <EvidenceList evidence={s.evidence} />

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
        <Button variant="primary" size="sm" icon={<Check />} onClick={() => void approve()} loading={busy === "approve"} disabled={busy !== null}>
          Approve
        </Button>
        <Button variant="secondary" size="sm" icon={<Pencil />} onClick={() => openEdit(asEditable(s.proposed_value), false)} disabled={busy !== null}>
          Edit &amp; approve
        </Button>
        <Button variant="ghost" size="sm" icon={<X />} onClick={() => setRejectOpen(true)} disabled={busy !== null}>
          Reject
        </Button>
      </div>

      {/* edit before approving */}
      <Modal
        open={editOpen}
        onClose={() => busy === null && setEditOpen(false)}
        title={`Edit, then approve: ${s.field_label}`}
        description={
          Array.isArray(s.proposed_value)
            ? "One question per line. This becomes the new value of the setting."
            : "This becomes the new value of the setting, exactly as written."
        }
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditOpen(false)} disabled={busy !== null}>
              Cancel
            </Button>
            <Button
              variant="primary"
              icon={<Check />}
              loading={busy === "approve"}
              disabled={!draft.trim() && !Array.isArray(s.proposed_value) && s.field !== "learned_rules"}
              onClick={() => void approve(fromEditable(draft, s.proposed_value), draftForce)}
            >
              Approve
            </Button>
          </>
        }
      >
        <TextAreaField
          label={s.field_label}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={12}
          inputClassName="min-h-[240px] text-mixed"
          autoFocus
        />
      </Modal>

      {/* reject */}
      <Modal
        open={rejectOpen}
        onClose={() => busy === null && setRejectOpen(false)}
        title="Reject this suggestion?"
        description="Nothing changes. The review agent sees your note and won't suggest this exact change again."
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setRejectOpen(false)} disabled={busy !== null}>
              Cancel
            </Button>
            <Button variant="danger" icon={<X />} loading={busy === "reject"} onClick={() => void reject()}>
              Reject
            </Button>
          </>
        }
      >
        <TextAreaField
          label="Why? (optional)"
          hint="e.g. “We prefer the current style” or “The price list is changing next week”."
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          maxLength={500}
        />
      </Modal>

      {/* the setting changed since the suggestion was made */}
      <Modal
        open={!!stale}
        onClose={() => busy === null && setStale(null)}
        title="This setting changed after the suggestion"
        description={stale?.message}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setStale(null)} disabled={busy !== null}>
              Cancel
            </Button>
            <Button variant="secondary" icon={<Pencil />} onClick={() => stale && openEdit(mergedDraft(s, stale.current), true)} disabled={busy !== null}>
              Edit first
            </Button>
            <Button variant="primary" icon={<Check />} loading={busy === "approve"} onClick={() => void approve(undefined, true)}>
              Approve anyway
            </Button>
          </>
        }
      >
        {stale ? (
          <div className="space-y-2 text-sm">
            <p>Approving as suggested would change the current value like this:</p>
            <div className="rounded-lg border border-line p-3">
              <ValueChange before={stale.current} after={s.proposed_value} />
            </div>
            <p className="text-[13px] text-muted">
              “Edit first” starts from the current value{typeof s.proposed_value === "string" ? " with the suggested lines added" : ""}, so
              nothing written meanwhile is lost.
            </p>
          </div>
        ) : null}
      </Modal>
    </Card>
  );
}

/** Quotes from the calls that prove the problem, each linking to its call. */
export function EvidenceList({ evidence, max = 6 }: { evidence: ReviewEvidence[]; max?: number }) {
  const [all, setAll] = useState(false);
  if (!evidence.length) return null;
  const shown = all ? evidence : evidence.slice(0, max);
  return (
    <div>
      <p className="mb-2 text-[12px] font-semibold tracking-wide text-muted uppercase">Proof from the calls</p>
      <ul className="space-y-2">
        {shown.map((e, i) => (
          <li key={`${e.call_uuid}-${e.turn}-${i}`} className="rounded-lg bg-canvas p-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-muted">
              <span>
                <span className="font-medium text-fg">{e.lead}</span> · at {e.at} in the call
                {e.call_at ? ` · ${formatShortDateTime(e.call_at)}` : ""}
              </span>
              <Link
                href={`/calls/${encodeURIComponent(e.call_uuid)}`}
                className="inline-flex items-center gap-1 font-medium text-primary hover:underline [&_svg]:size-3"
              >
                Open call <ExternalLink aria-hidden />
              </Link>
            </div>
            <p className="text-mixed mt-1 flex gap-1.5 text-[13px]">
              <Quote className="mt-1 size-3 shrink-0 text-muted" aria-hidden />
              <span>
                <span className="sr-only">Agent said: </span>“{e.quote}”
              </span>
            </p>
            {e.finding ? <p className="text-mixed mt-1 text-[12px] text-subtle-fg">{e.finding}</p> : null}
          </li>
        ))}
      </ul>
      {evidence.length > max ? (
        <button type="button" onClick={() => setAll((v) => !v)} className="mt-2 text-[12px] font-medium text-primary hover:underline">
          {all ? "Show less" : `Show all ${evidence.length}`}
        </button>
      ) : null}
    </div>
  );
}
