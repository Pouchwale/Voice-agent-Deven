"use client";

import { FileSpreadsheet, Filter, ListChecks, Play, Save } from "lucide-react";
import { useState } from "react";
import { useSWRConfig } from "swr";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Segmented, SelectField, type Option } from "@/components/ui/Select";
import { Slider, Switch } from "@/components/ui/Switch";
import { useToast } from "@/components/ui/Toast";
import { api, errorMessage, keys } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  formatDayLabel,
  formatNumber,
  INTEREST_ALL,
  INTEREST_LABEL,
  LEAD_STATUSES,
  LEAD_STATUS_LABEL,
  ymdIST,
} from "@/lib/format";
import type { Campaign, CampaignCreate, CampaignFilter, Interest, LeadStatus } from "@/lib/types";
import { CsvUploadPanel, useCsvPreview } from "./CsvUpload";
import { OutboundOffNotice, useOutboundOff } from "./OutboundOffNotice";

type Source = "selected" | "csv" | "filter";

const STATUS_OPTIONS: Option<LeadStatus | "">[] = [
  { value: "", label: "Any status" },
  ...LEAD_STATUSES.filter((s) => s !== "dnc").map((s) => ({ value: s, label: LEAD_STATUS_LABEL[s] })),
];

const INTEREST_OPTIONS: Option<Interest | "">[] = [
  { value: "", label: "Any interest" },
  ...INTEREST_ALL.map((i) => ({ value: i, label: INTEREST_LABEL[i] })),
];

const ATTEMPT_OPTIONS: Option<string>[] = ["1", "2", "3", "4", "5"].map((v) => ({ value: v, label: v }));

export interface CreateCampaignModalProps {
  open: boolean;
  onClose: () => void;
  /** Leads selected on the Leads page. */
  selectedIds?: number[];
  /** Prefill for the "Leads matching a filter" option. */
  initialFilter?: CampaignFilter;
  onCreated?: (c: Campaign) => void;
}

/** Create a campaign from selected leads or a lead filter. Form state resets every time it opens. */
export function CreateCampaignModal(props: CreateCampaignModalProps) {
  if (!props.open) return null;
  return <CreateCampaignForm {...props} />;
}

function CreateCampaignForm({ onClose, selectedIds, initialFilter, onCreated }: CreateCampaignModalProps) {
  const toast = useToast();
  const { mutate } = useSWRConfig();
  const outboundOff = useOutboundOff();
  const hasSelection = !!selectedIds && selectedIds.length > 0;

  const [name, setName] = useState(() => `Campaign – ${formatDayLabel(ymdIST())}`);
  const [nameError, setNameError] = useState<string | null>(null);
  const [source, setSource] = useState<Source>(hasSelection ? "selected" : "csv");
  const csv = useCsvPreview();
  const csvReady = !!csv.file && !!csv.preview && !csv.loading && csv.preview.summary.callable > 0;
  const blocked = source === "csv" && !csvReady;
  const [status, setStatus] = useState<LeadStatus | "">(initialFilter?.status && initialFilter.status !== "dnc" ? initialFilter.status : "");
  const [interest, setInterest] = useState<Interest | "">(initialFilter?.interest ?? "");
  const [search, setSearch] = useState(initialFilter?.search ?? "");
  const [concurrency, setConcurrency] = useState(2);
  const [maxAttempts, setMaxAttempts] = useState(2);
  const [retryDelay, setRetryDelay] = useState("30");
  const [respectWindow, setRespectWindow] = useState(true);
  const [busy, setBusy] = useState<"draft" | "start" | null>(null);

  const retryNum = Number(retryDelay);
  const retryError = !Number.isFinite(retryNum) || retryNum < 1 || retryNum > 1440 ? "Enter 1–1440 minutes." : null;

  async function submit(startNow: boolean) {
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError("Give the campaign a name.");
      return;
    }
    if (retryError) return;
    if (blocked) return;
    setNameError(null);

    const body: CampaignCreate = {
      name: trimmed,
      concurrency,
      max_attempts: maxAttempts,
      retry_delay_min: Math.round(retryNum),
      respect_call_window: respectWindow,
    };
    if (source === "selected" && hasSelection) {
      body.lead_ids = selectedIds;
    } else if (source === "filter") {
      const filter: CampaignFilter = {};
      if (status) filter.status = status;
      if (interest) filter.interest = interest;
      if (search.trim()) filter.search = search.trim();
      body.filter = filter;
    }

    setBusy(startNow ? "start" : "draft");
    let created: Campaign;
    let uploadNote = "";
    try {
      if (source === "csv" && csv.file) {
        const res = await api.campaigns.createFromCsv(csv.file, body);
        const u = res.upload;
        const left = [
          u.duplicate ? `${formatNumber(u.duplicate)} duplicate${u.duplicate === 1 ? "" : "s"} removed` : "",
          u.invalid ? `${formatNumber(u.invalid)} invalid` : "",
          u.dnc ? `${formatNumber(u.dnc)} do-not-call skipped` : "",
        ].filter(Boolean);
        uploadNote = ` ${formatNumber(u.new)} new leads added.${left.length ? ` ${left.join(", ")}.` : ""}`;
        // new leads were created: refresh lead lists and dashboard numbers too
        void mutate((key) => typeof key === "string" && (key.startsWith("/api/leads") || key.startsWith("/api/dashboard")));
        created = res;
      } else {
        created = await api.campaigns.create(body);
      }
    } catch (err) {
      toast({ tone: "error", title: "Couldn't create campaign", description: errorMessage(err) });
      setBusy(null);
      return;
    }

    let result = created;
    if (startNow) {
      try {
        result = await api.campaigns.start(created.id);
        toast({
          tone: "success",
          title: `Campaign started: ${result.name}`,
          description: `${formatNumber(result.counts.total)} numbers queued. The agent is dialing.${uploadNote}`,
          action: { label: "Watch live", href: "/live" },
        });
      } catch (err) {
        toast({
          tone: "error",
          title: "Created as draft, but couldn't start",
          description: errorMessage(err),
        });
      }
    } else {
      toast({
        tone: "success",
        title: `Draft saved: ${created.name}`,
        description: `${formatNumber(created.counts.total)} numbers ready. Start it from Campaigns.${uploadNote}`,
        action: { label: "Open campaigns", href: "/campaigns" },
      });
    }

    void mutate(keys.campaigns);
    setBusy(null);
    onCreated?.(result);
    onClose();
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="New campaign"
      description="The agent dials every number in the list, qualifies them and scores interest. Do-not-call leads are always skipped."
      size={source === "csv" ? "xl" : "lg"}
      footer={
        <>
          {source === "csv" && csv.preview && !csv.loading ? (
            <p className="mr-auto hidden text-[13px] font-medium text-muted sm:block">
              {csvReady
                ? `${formatNumber(csv.preview.summary.callable)} unique numbers will be called`
                : "No callable numbers in this file"}
            </p>
          ) : null}
          <Button variant="secondary" onClick={onClose} disabled={busy !== null}>
            Cancel
          </Button>
          <Button
            variant="secondary"
            icon={<Save />}
            loading={busy === "draft"}
            disabled={blocked || (busy !== null && busy !== "draft")}
            onClick={() => void submit(false)}
          >
            Create draft
          </Button>
          <Button
            variant="primary"
            icon={<Play />}
            loading={busy === "start"}
            disabled={blocked || outboundOff || (busy !== null && busy !== "start")}
            title={outboundOff ? "Outbound calling is off in Settings: save a draft and start it later." : undefined}
            onClick={() => void submit(true)}
          >
            Create &amp; start
          </Button>
        </>
      }
    >
      <form
        className="space-y-6"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(false);
        }}
      >
        {outboundOff ? (
          <OutboundOffNotice link={false}>
            Outbound calling is off in Settings, so this campaign can&apos;t start now. Save it as a draft and start it
            once calling is back on.
          </OutboundOffNotice>
        ) : null}
        <TextField
          label="Campaign name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={nameError}
          maxLength={120}
          required
          data-autofocus
        />

        <fieldset>
          <legend className="field-label">Numbers to call</legend>
          <div className={cn("grid gap-3", hasSelection ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
            {hasSelection ? (
              <SourceOption
                checked={source === "selected"}
                onSelect={() => setSource("selected")}
                icon={<ListChecks aria-hidden />}
                title={`Selected leads (${formatNumber(selectedIds!.length)})`}
                body="The leads you ticked on the Leads page."
              />
            ) : null}
            <SourceOption
              checked={source === "csv"}
              onSelect={() => setSource("csv")}
              icon={<FileSpreadsheet aria-hidden />}
              title="Upload a CSV file"
              body="See every number first. Duplicates and invalid numbers are removed."
            />
            <SourceOption
              checked={source === "filter"}
              onSelect={() => setSource("filter")}
              icon={<Filter aria-hidden />}
              title="Leads matching a filter"
              body="Every lead that matches right now. No filter = all leads."
            />
          </div>

          {source === "csv" ? (
            <div className="mt-4">
              <CsvUploadPanel csv={csv} />
            </div>
          ) : null}

          {source === "filter" ? (
            <div className="mt-4 grid gap-4 rounded-xl border border-line bg-canvas p-4 sm:grid-cols-3">
              <SelectField<LeadStatus | "">
                label="Lead status"
                options={STATUS_OPTIONS}
                value={status}
                onChange={setStatus}
              />
              <SelectField<Interest | "">
                label="Interest"
                options={INTEREST_OPTIONS}
                value={interest}
                onChange={setInterest}
              />
              <TextField
                label="Search"
                placeholder="Name, phone, company…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <p className="text-[13px] font-normal text-muted sm:col-span-3">
                Leads marked &ldquo;Do not call&rdquo; are skipped automatically.
              </p>
            </div>
          ) : null}
        </fieldset>

        <div className="grid gap-6 sm:grid-cols-2">
          <Slider
            label="Parallel calls"
            value={concurrency}
            min={1}
            max={10}
            step={1}
            onChange={setConcurrency}
            format={(v) => `${v} ${v === 1 ? "line" : "lines"}`}
            hint="How many calls the agent runs at the same time."
          />
          <div>
            <span className="field-label" aria-hidden>
              Max attempts per lead
            </span>
            <Segmented<string>
              label="Max attempts per lead"
              options={ATTEMPT_OPTIONS}
              value={String(maxAttempts)}
              onChange={(v) => setMaxAttempts(Number(v))}
            />
            <p className="mt-1.5 text-[13px] font-normal text-muted">Retries no-answer and busy numbers.</p>
          </div>
          <TextField
            label="Retry delay (minutes)"
            type="number"
            inputMode="numeric"
            min={1}
            max={1440}
            value={retryDelay}
            onChange={(e) => setRetryDelay(e.target.value)}
            error={retryError}
            hint="Wait this long before calling a lead again."
          />
          <div className="rounded-xl border border-line p-4">
            <Switch
              checked={respectWindow}
              onChange={setRespectWindow}
              label="Respect calling window"
              description="Uses the window from Settings (IST). Calls pause outside it."
            />
          </div>
        </div>
        {/* allow Enter to submit */}
        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
      </form>
    </Modal>
  );
}

function SourceOption({
  checked,
  onSelect,
  icon,
  title,
  body,
}: {
  checked: boolean;
  onSelect: () => void;
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-xl border border-line p-3.5 transition-[background-color,box-shadow] duration-150",
        checked ? "bg-canvas " : "bg-white hover:bg-canvas",
      )}
    >
      <input type="radio" name="campaign-source" checked={checked} onChange={onSelect} className="mt-1 size-4 accent-primary" />
      <span className="min-w-0">
        <span className="flex items-center gap-2 text-[15px] font-semibold [&_svg]:size-4">
          {icon}
          {title}
        </span>
        <span className="mt-0.5 block text-[13px] font-normal text-muted">{body}</span>
      </span>
    </label>
  );
}
