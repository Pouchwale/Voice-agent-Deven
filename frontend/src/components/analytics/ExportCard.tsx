"use client";

import { Download, FileSpreadsheet, FileText, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { AnalyticsParams } from "@/lib/types";

type Item = "pdf" | "calls" | "leads";

function DownloadRow({
  icon,
  title,
  description,
  href,
  option,
  preparing,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  href: string;
  option?: ReactNode;
  preparing: boolean;
  onClick: () => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-3 py-3">
      <span className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary [&_svg]:size-4" aria-hidden>
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-[13px] text-muted">{description}</p>
        {option ? <div className="mt-1.5">{option}</div> : null}
      </div>
      <a
        href={href}
        download
        onClick={onClick}
        aria-busy={preparing || undefined}
        className={cn("btn border-line bg-white text-fg hover:bg-canvas", preparing && "pointer-events-none opacity-70")}
      >
        {preparing ? <LoaderCircle className="spin size-4" aria-hidden /> : <Download className="size-4" aria-hidden />}
        {preparing ? "Preparing…" : "Download"}
      </a>
    </li>
  );
}

function Check({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-[13px] text-subtle-fg">
      <input type="checkbox" className="size-4 accent-primary" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/** Report downloads for the current range / campaign (plain links; the backend streams the file). */
export function ExportCard({ params, campaignName, id = "export" }: { params: AnalyticsParams; campaignName?: string; id?: string }) {
  const [pdfTranscripts, setPdfTranscripts] = useState(false);
  const [csvTranscripts, setCsvTranscripts] = useState(true);
  const [preparing, setPreparing] = useState<Item | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  function prepare(item: Item) {
    setPreparing(item);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setPreparing(null), 3500);
  }

  const scope = `${formatDate(params.from)} – ${formatDate(params.to)}${campaignName ? ` · ${campaignName}` : ""}${params.include_tests ? " · incl. test calls" : ""}`;

  return (
    <section id={id} tabIndex={-1} className="scroll-mt-20 rounded-xl border border-line bg-white p-4 outline-none sm:p-5" aria-labelledby={`${id}-title`}>
      <CardHeader title={<span id={`${id}-title`}>Export report</span>} subtitle={`Uses the current filters: ${scope}`} />
      <ul className="-my-3 divide-y divide-line">
        <DownloadRow
          icon={<FileText />}
          title="Summary report (PDF)"
          description="KPIs, charts, hot and warm lead tables and every call."
          href={api.reports.summaryPdf({ ...params, include_transcripts: pdfTranscripts })}
          option={<Check checked={pdfTranscripts} onChange={setPdfTranscripts} label="Include full transcripts" />}
          preparing={preparing === "pdf"}
          onClick={() => prepare("pdf")}
        />
        <DownloadRow
          icon={<FileSpreadsheet />}
          title="Calls (CSV)"
          description="One row per call, inbound and outbound, with the customer's number, summary, rating, qualification answers and recording link."
          href={api.reports.callsCsv({ ...params, include_transcripts: csvTranscripts })}
          option={<Check checked={csvTranscripts} onChange={setCsvTranscripts} label="Include transcripts" />}
          preparing={preparing === "calls"}
          onClick={() => prepare("calls")}
        />
        <DownloadRow
          icon={<FileSpreadsheet />}
          title="Leads (CSV)"
          description="One row per lead called in this range: stage, interest, rating, attempts, last summary."
          href={api.reports.leadsCsv({ from: params.from, to: params.to })}
          preparing={preparing === "leads"}
          onClick={() => prepare("leads")}
        />
      </ul>
    </section>
  );
}
