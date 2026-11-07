"use client";

import { Save, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useSWRConfig } from "swr";
import { Button } from "@/components/ui/Button";
import { TextAreaField, TextField } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { SelectField } from "@/components/ui/Select";
import { useToast } from "@/components/ui/Toast";
import { api, ApiError, errorMessage } from "@/lib/api";
import { INTEREST_ALL, INTEREST_LABEL, LEAD_STATUSES, LEAD_STATUS_LABEL } from "@/lib/format";
import type { Interest, Lead, LeadStatus } from "@/lib/types";

interface FormState {
  name: string;
  phone: string;
  company: string;
  city: string;
  email: string;
  source: string;
  notes: string;
  status: LeadStatus;
  interest: Interest;
}

function initialState(lead?: Lead | null): FormState {
  return {
    name: lead?.name ?? "",
    phone: lead?.phone ?? "",
    company: lead?.company ?? "",
    city: lead?.city ?? "",
    email: lead?.email ?? "",
    source: lead?.source ?? "",
    notes: lead?.notes ?? "",
    status: lead?.status ?? "new",
    interest: lead?.interest ?? "unknown",
  };
}

const orNull = (s: string) => (s.trim() ? s.trim() : null);

function FormBody({ lead, onClose, onSaved }: { lead?: Lead | null; onClose: () => void; onSaved?: (l: Lead) => void }) {
  const [form, setForm] = useState<FormState>(() => initialState(lead));
  const [errors, setErrors] = useState<Partial<Record<keyof FormState | "form", string>>>({});
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const { mutate } = useSWRConfig();
  const editing = !!lead;

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => {
    setForm((f) => ({ ...f, [k]: v }));
    setErrors((e) => ({ ...e, [k]: undefined, form: undefined }));
  };

  function validate(): boolean {
    const next: typeof errors = {};
    if (!form.name.trim()) next.name = "Name is required.";
    const digits = form.phone.replace(/\D/g, "");
    if (!digits) next.phone = "Phone is required.";
    else if (digits.length < 10 || digits.length > 13) next.phone = "Enter a 10-digit mobile number (with or without +91).";
    if (form.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) next.email = "That email doesn't look right.";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!validate()) return;
    setSaving(true);
    const base = {
      name: form.name.trim(),
      phone: form.phone.trim(),
      company: orNull(form.company),
      city: orNull(form.city),
      email: orNull(form.email),
      source: orNull(form.source),
      notes: orNull(form.notes),
    };
    try {
      const saved = editing
        ? await api.leads.update(lead!.id, { ...base, status: form.status, interest: form.interest })
        : await api.leads.create(base);
      toast({ tone: "success", title: editing ? "Lead updated" : "Lead added", description: saved.name });
      void mutate((key) => typeof key === "string" && (key.startsWith("/api/leads") || key.startsWith("/api/dashboard")));
      onSaved?.(saved);
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setErrors({ phone: "A lead with this phone number already exists." });
      else setErrors({ form: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Name *" value={form.name} onChange={(e) => set("name", e.target.value)} error={errors.name} autoComplete="off" data-autofocus />
        <TextField
          label="Phone *"
          type="tel"
          inputMode="tel"
          value={form.phone}
          onChange={(e) => set("phone", e.target.value)}
          error={errors.phone}
          hint="Indian mobile, e.g. 98765 43210"
          autoComplete="off"
        />
        <TextField label="Company" value={form.company} onChange={(e) => set("company", e.target.value)} />
        <TextField label="City" value={form.city} onChange={(e) => set("city", e.target.value)} />
        <TextField label="Email" type="email" value={form.email} onChange={(e) => set("email", e.target.value)} error={errors.email} />
        <TextField label="Source" placeholder="e.g. IndiaMART, website" value={form.source} onChange={(e) => set("source", e.target.value)} />
        {editing ? (
          <>
            <SelectField<LeadStatus>
              label="Status"
              value={form.status}
              onChange={(v) => set("status", v)}
              options={LEAD_STATUSES.map((s) => ({ value: s, label: LEAD_STATUS_LABEL[s] }))}
            />
            <SelectField<Interest>
              label="Interest"
              value={form.interest}
              onChange={(v) => set("interest", v)}
              options={INTEREST_ALL.map((i) => ({ value: i, label: INTEREST_LABEL[i] }))}
            />
          </>
        ) : null}
        <TextAreaField
          className="sm:col-span-2"
          label="Notes"
          value={form.notes}
          onChange={(e) => set("notes", e.target.value)}
          placeholder="Anything the agent or sales team should know"
          rows={3}
        />
      </div>
      {errors.form ? (
        <p className="mt-4 rounded-lg border border-line bg-subtle px-3 py-2 text-sm font-medium" role="alert">
          {errors.form}
        </p>
      ) : null}
      <div className="mt-6 flex flex-wrap justify-end gap-3">
        <Button onClick={onClose}>Cancel</Button>
        <Button type="submit" variant="primary" loading={saving} icon={editing ? <Save /> : <UserPlus />}>
          {editing ? "Save changes" : "Add lead"}
        </Button>
      </div>
    </form>
  );
}

export function LeadFormModal({
  open,
  onClose,
  lead,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  lead?: Lead | null;
  onSaved?: (l: Lead) => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title={lead ? "Edit lead" : "Add lead"} description={lead ? lead.name : "Add one lead by hand. For many leads, import a CSV."}>
      {/* keyed so the form resets every time it opens */}
      {open ? <FormBody key={lead?.id ?? "new"} lead={lead} onClose={onClose} onSaved={onSaved} /> : null}
    </Modal>
  );
}
