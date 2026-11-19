"use client";

import {
  BookOpen,
  Brain,
  ClipboardCheck,
  Clock,
  Flame,
  GraduationCap,
  Hourglass,
  ListChecks,
  MessageSquare,
  PhoneCall,
  RotateCcw,
  Save,
  ScrollText,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UserRound,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { Chip } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { TypeConfirmDialog } from "@/components/ui/TypeConfirmDialog";
import { useEntrance } from "@/hooks/useEntrance";
import { api, errorMessage, keys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { pluralize, titleCase } from "@/lib/format";
import type { AgentSettings } from "@/lib/types";
import { AiBrainCard } from "./AiBrainCard";
import { CallingSection } from "./CallingSection";
import { CampaignWindowSection, ConversationSection } from "./ConversationSections";
import { IdentityVoiceSection } from "./IdentityVoiceSection";
import { coerce, diffSettings, normalize, validate, type SetField } from "./model";
import { QuestionsSection } from "./QuestionsSection";
import { ChangeHistorySection, LearnedRulesSection, ReviewAgentSection } from "./ReviewSections";
import { CallScriptSection, InterestRulesSection, KnowledgeBaseSection, ObjectionsSection } from "./ScriptSections";
import { SectionNav, type NavItem } from "./SectionNav";
import { SilenceSection } from "./SilenceSection";
import { ToneSection } from "./ToneSection";

const NAV: NavItem[] = [
  { id: "identity", label: "Identity & voice", icon: UserRound },
  { id: "tone", label: "Tone & accent", icon: SlidersHorizontal },
  { id: "calling", label: "Calling", icon: PhoneCall },
  { id: "script", label: "Call script", icon: MessageSquare },
  { id: "knowledge", label: "Knowledge base", icon: BookOpen },
  { id: "qualification", label: "Qualification", icon: ListChecks },
  { id: "objections", label: "Objections", icon: ShieldCheck },
  { id: "learned-rules", label: "Learned rules", icon: GraduationCap },
  { id: "interest", label: "Interest rules", icon: Flame },
  { id: "silence", label: "Silence & hang-up", icon: Hourglass },
  { id: "feel", label: "Conversation feel", icon: Sparkles },
  { id: "window", label: "Campaign window", icon: Clock },
  { id: "review-agent", label: "Review agent", icon: ClipboardCheck },
  { id: "ai-brain", label: "AI brain", icon: Brain },
  { id: "history", label: "Change history", icon: ScrollText },
];

/** How many setting names the save popup lists before "and N more". */
const SAVE_LIST_MAX = 12;

function SettingsSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading settings">
      {[0, 1, 2].map((i) => (
        <div key={i} className="rounded-xl border border-line bg-white p-6">
          <div className="mb-5 flex items-center gap-3">
            <Skeleton className="size-11 rounded-full" />
            <Skeleton className="h-6 w-48" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Skeleton className="h-11 rounded-lg" />
            <Skeleton className="h-11 rounded-lg" />
          </div>
          <Skeleton className="mt-4 h-24 rounded-lg" />
        </div>
      ))}
    </div>
  );
}

export function SettingsView() {
  const toast = useToast();
  const { data, error, mutate } = useSWR<AgentSettings>(keys.settings, { revalidateOnFocus: false });
  const { mutate: mutateGlobal } = useSWRConfig();
  const [base, setBase] = useState<AgentSettings | null>(null);
  const [form, setForm] = useState<AgentSettings | null>(null);
  const [saving, setSaving] = useState(false);
  // The "type CONFIRM" popup of a save is open.
  const [confirmSave, setConfirmSave] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  // What each setting is called, for the list in the popup.
  const { data: labels } = useSWR<Record<string, string>>(keys.settingsLabels, { revalidateOnFocus: false });

  const changes = useMemo(() => (form && base ? diffSettings(form, base) : {}), [form, base]);
  const changedCount = Object.keys(changes).length;
  const dirty = changedCount > 0;
  const errors = useMemo(() => (form ? validate(form) : {}), [form]);
  const errorCount = Object.keys(errors).length;
  // What the save popup lists: worked out from the form as it is now, not frozen when the popup opened. (Ctrl/Cmd + S
  // can open it while a field still holds a typed value that only lands in the form when the field loses focus.)
  const savingKeys = useMemo(
    () => (confirmSave && form && base ? Object.keys(diffSettings(normalize(form), base)) : []),
    [confirmSave, form, base],
  );

  // Adopt server data whenever it changes - unless the user has unsaved edits.
  const dirtyRef = useRef(dirty);
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);
  useEffect(() => {
    if (!data || dirtyRef.current) return;
    const safe = coerce(data);
    setBase(safe);
    setForm(safe);
  }, [data]);

  const set = useCallback<SetField>((key, value) => {
    setForm((f) => (f ? { ...f, [key]: value } : f));
  }, []);

  // Arriving via a link like /settings#calling ("Outbound calling is off in Settings → Calling"): the sections only
  // exist once the settings have loaded, after the browser's own jump to the anchor has already missed.
  const jumped = useRef(false);
  const ready = !!form;
  useEffect(() => {
    if (!ready || jumped.current) return;
    jumped.current = true;
    const id = window.location.hash.slice(1);
    if (id && NAV.some((n) => n.id === id)) document.getElementById(id)?.scrollIntoView({ block: "start" });
  }, [ready]);

  // Warn before leaving the page with unsaved edits.
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  /** What a save would send. With errors in the form it points at them instead; null = nothing to send. */
  const pendingChanges = useCallback((): Partial<AgentSettings> | null => {
    if (!form || !base || saving) return null;
    if (errorCount) {
      toast({
        tone: "error",
        title: "Fix the highlighted fields first",
        description: `${errorCount} field${errorCount === 1 ? "" : "s"} need${errorCount === 1 ? "s" : ""} attention.`,
      });
      const firstInvalid = document.querySelector<HTMLElement>('#main [aria-invalid="true"]');
      firstInvalid?.scrollIntoView({ block: "center" });
      firstInvalid?.focus({ preventScroll: true });
      return null;
    }
    const normalized = normalize(form);
    const body = diffSettings(normalized, base);
    if (!Object.keys(body).length) {
      setForm(normalized);
      return null;
    }
    return body;
  }, [form, base, saving, errorCount, toast]);

  // Every way of saving (both Save buttons, Ctrl/Cmd + S) opens the popup first: the person gives their name and
  // types CONFIRM, and only then is anything sent.
  const askToSave = useCallback(() => {
    if (pendingChanges()) setConfirmSave(true);
  }, [pendingChanges]);

  const save = useCallback(async () => {
    const body = pendingChanges();
    if (!body || !form) {
      setConfirmSave(false);
      return;
    }
    setSaving(true);
    const sent = form;
    try {
      const res = await api.settings.update(body);
      const saved = coerce(res);
      setBase(saved);
      // Fields edited while the PUT was in flight stay as typed (and stay dirty) instead of being overwritten.
      setForm((cur) => {
        if (!cur) return saved;
        const merged: Record<string, unknown> = { ...saved };
        for (const k of Object.keys(cur) as (keyof AgentSettings)[]) {
          if (JSON.stringify(cur[k]) !== JSON.stringify(sent[k])) merged[k] = cur[k];
        }
        return merged as unknown as AgentSettings;
      });
      await mutate(res, { revalidate: false });
      void mutateGlobal(keys.settingsHistory);
      toast({ tone: "success", title: "Settings saved", description: "They apply from the next call." });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't save settings", description: errorMessage(err) });
    } finally {
      setSaving(false);
      setConfirmSave(false);
    }
  }, [pendingChanges, form, toast, mutate, mutateGlobal]);

  // An undo from Change history replaced settings on the server: show them (only offered without unsaved edits).
  const applyServerSettings = useCallback((s: AgentSettings) => {
    const safe = coerce(s);
    setBase(safe);
    setForm(safe);
  }, []);

  // Ctrl/Cmd + S asks to save (never while a popup is already open).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (dirty && !document.querySelector("[data-dialog-panel]")) askToSave();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, askToSave]);

  async function doReset() {
    setResetting(true);
    try {
      const res = await api.settings.reset();
      setBase(coerce(res));
      setForm(coerce(res));
      await mutate(res, { revalidate: false });
      void mutateGlobal(keys.settingsHistory);
      setResetOpen(false);
      toast({ tone: "success", title: "Settings reset to defaults", description: "Changed your mind? Undo it under Change history." });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't reset settings", description: errorMessage(err) });
    } finally {
      setResetting(false);
    }
  }

  const rootRef = useEntrance<HTMLDivElement>(ready || !!error);
  const sectionProps = form ? { form, set, errors } : null;

  return (
    <div ref={rootRef}>
      <PageHeader
        title="Settings"
        description="How the agent talks, qualifies and scores every lead. Changes apply from the next call."
        actions={
          <>
            <Button variant="secondary" icon={<RotateCcw />} onClick={() => setResetOpen(true)} disabled={!form}>
              Reset to defaults
            </Button>
            <Button variant="primary" icon={<Save />} onClick={askToSave} loading={saving} disabled={!dirty}>
              Save
            </Button>
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="min-w-0">
          <SectionNav key={form ? "ready" : "loading"} items={NAV} />
        </aside>

        <div className="min-w-0 space-y-6">
          {error && !data ? (
            <ErrorState error={error} onRetry={() => void mutate()} title="Couldn't load agent settings" />
          ) : !sectionProps ? (
            <SettingsSkeleton />
          ) : (
            <>
              <IdentityVoiceSection {...sectionProps} />
              <ToneSection {...sectionProps} />
              <CallingSection {...sectionProps} />
              <CallScriptSection {...sectionProps} />
              <KnowledgeBaseSection {...sectionProps} />
              <QuestionsSection {...sectionProps} />
              <ObjectionsSection {...sectionProps} />
              <LearnedRulesSection {...sectionProps} />
              <InterestRulesSection {...sectionProps} />
              <SilenceSection {...sectionProps} />
              <ConversationSection {...sectionProps} />
              <CampaignWindowSection {...sectionProps} />
              <ReviewAgentSection {...sectionProps} />
            </>
          )}

          <AiBrainCard />

          {sectionProps ? <ChangeHistorySection dirty={dirty} onApplied={applyServerSettings} /> : null}

          {form ? (
            <div className="sticky bottom-4 z-20" role="region" aria-label="Save changes">
              <div
                className={cn(
                  "flex flex-wrap items-center gap-3 rounded-xl border border-line px-4 py-3 ",
                  dirty ? "bg-canvas" : "bg-white",
                )}
              >
                <p className="min-w-0 flex-1 text-sm font-semibold" aria-live="polite">
                  {dirty ? `Unsaved changes · ${changedCount} field${changedCount === 1 ? "" : "s"}` : "All changes saved"}
                  {errorCount ? (
                    <span className="ml-2 rounded-full border border-line bg-subtle px-2 py-0.5 text-xs">
                      {errorCount} to fix
                    </span>
                  ) : null}
                </p>
                <Button variant="secondary" size="sm" onClick={() => base && setForm(base)} disabled={!dirty || saving}>
                  Discard
                </Button>
                <Button variant="primary" size="sm" icon={<Save />} onClick={askToSave} loading={saving} disabled={!dirty}>
                  Save changes
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <TypeConfirmDialog
        open={confirmSave}
        onClose={() => setConfirmSave(false)}
        onConfirm={() => void save()}
        title="Save these changes?"
        confirmLabel="Save changes"
        busy={saving}
      >
        <p>
          You are about to change {pluralize(savingKeys.length, "setting")}. They apply from the next call, and the change is
          recorded with your name.
        </p>
        <ul className="mt-2.5 flex flex-wrap gap-1.5" aria-label="Settings that will change">
          {savingKeys.slice(0, SAVE_LIST_MAX).map((key) => (
            <li key={key}>
              <Chip size="sm">{labels?.[key] ?? titleCase(key.replace(/_/g, " "))}</Chip>
            </li>
          ))}
          {savingKeys.length > SAVE_LIST_MAX ? (
            <li className="self-center text-[13px] text-muted">and {savingKeys.length - SAVE_LIST_MAX} more</li>
          ) : null}
        </ul>
      </TypeConfirmDialog>

      <TypeConfirmDialog
        open={resetOpen}
        onClose={() => setResetOpen(false)}
        onConfirm={() => void doReset()}
        title="Reset to defaults?"
        confirmLabel="Reset everything"
        danger
        busy={resetting}
      >
        Every setting on this page - calling switches (both back on), greetings, script, knowledge base, learned rules,
        questions, voice, tone, timings and the review agent - goes back to the default. You can undo it afterwards under
        Change history.
      </TypeConfirmDialog>
    </div>
  );
}
