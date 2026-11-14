"use client";

import {
  BookOpen,
  Brain,
  Clock,
  Flame,
  Hourglass,
  ListChecks,
  MessageSquare,
  PhoneCall,
  RotateCcw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UserRound,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Modal";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { useEntrance } from "@/hooks/useEntrance";
import { api, errorMessage, keys } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { AgentSettings } from "@/lib/types";
import { AiBrainCard } from "./AiBrainCard";
import { CallingSection } from "./CallingSection";
import { CampaignWindowSection, ConversationSection } from "./ConversationSections";
import { IdentityVoiceSection } from "./IdentityVoiceSection";
import { coerce, diffSettings, normalize, validate, type SetField } from "./model";
import { QuestionsSection } from "./QuestionsSection";
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
  { id: "interest", label: "Interest rules", icon: Flame },
  { id: "silence", label: "Silence & hang-up", icon: Hourglass },
  { id: "feel", label: "Conversation feel", icon: Sparkles },
  { id: "window", label: "Campaign window", icon: Clock },
  { id: "ai-brain", label: "AI brain", icon: Brain },
];

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
  const [base, setBase] = useState<AgentSettings | null>(null);
  const [form, setForm] = useState<AgentSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);

  const changes = useMemo(() => (form && base ? diffSettings(form, base) : {}), [form, base]);
  const changedCount = Object.keys(changes).length;
  const dirty = changedCount > 0;
  const errors = useMemo(() => (form ? validate(form) : {}), [form]);
  const errorCount = Object.keys(errors).length;

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

  const save = useCallback(async () => {
    if (!form || !base || saving) return;
    if (errorCount) {
      toast({
        tone: "error",
        title: "Fix the highlighted fields first",
        description: `${errorCount} field${errorCount === 1 ? "" : "s"} need${errorCount === 1 ? "s" : ""} attention.`,
      });
      const firstInvalid = document.querySelector<HTMLElement>('#main [aria-invalid="true"]');
      firstInvalid?.scrollIntoView({ block: "center" });
      firstInvalid?.focus({ preventScroll: true });
      return;
    }
    const normalized = normalize(form);
    const body = diffSettings(normalized, base);
    if (!Object.keys(body).length) {
      setForm(normalized);
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
      toast({ tone: "success", title: "Settings saved", description: "They apply from the next call." });
    } catch (err) {
      toast({ tone: "error", title: "Couldn't save settings", description: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  }, [form, base, saving, errorCount, toast, mutate]);

  // Ctrl/Cmd + S saves.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (dirty) void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, save]);

  async function doReset() {
    setResetting(true);
    try {
      const res = await api.settings.reset();
      setBase(coerce(res));
      setForm(coerce(res));
      await mutate(res, { revalidate: false });
      setResetOpen(false);
      toast({ tone: "success", title: "Settings reset to defaults" });
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
            <Button variant="primary" icon={<Save />} onClick={() => void save()} loading={saving} disabled={!dirty}>
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
              <InterestRulesSection {...sectionProps} />
              <SilenceSection {...sectionProps} />
              <ConversationSection {...sectionProps} />
              <CampaignWindowSection {...sectionProps} />
            </>
          )}

          <AiBrainCard />

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
                <Button variant="primary" size="sm" icon={<Save />} onClick={() => void save()} loading={saving} disabled={!dirty}>
                  Save changes
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={resetOpen}
        onClose={() => !resetting && setResetOpen(false)}
        onConfirm={() => void doReset()}
        title="Reset to defaults?"
        body="Every setting on this page - calling switches (both back on), greetings, script, knowledge base, questions, voice, tone and timings - goes back to the default. This can't be undone."
        confirmLabel="Reset everything"
        danger
        busy={resetting}
      />
    </div>
  );
}
