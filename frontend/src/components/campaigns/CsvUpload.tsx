"use client";

import { Ban, CircleAlert, Copy, FileSpreadsheet, LoaderCircle, PhoneCall, RefreshCw, Upload, UserCheck, UserPlus, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Chip, type ChipColor } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Pagination } from "@/components/ui/Pagination";
import { Segmented } from "@/components/ui/Select";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber, formatPhone, pluralize } from "@/lib/format";
import type { CsvPreview, CsvPreviewRow, CsvRowStatus } from "@/lib/types";
import { CsvTemplate } from "./CsvTemplate";

const MAX_BYTES = 20 * 1024 * 1024; // backend limit
const PAGE_SIZE = 50;

function prettySize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export interface CsvState {
  file: File | null;
  preview: CsvPreview | null;
  loading: boolean;
  error: string | null;
  pick: (f: File | null | undefined) => void;
  clear: () => void;
  retry: () => void;
}

/** File + server-side preview of a campaign CSV. A newer pick cancels an older preview request. */
export function useCsvPreview(): CsvState {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);

  useEffect(() => () => ctrl.current?.abort(), []);

  const load = useCallback(async (f: File) => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      const res = await api.campaigns.previewCsv(f, c.signal);
      if (!c.signal.aborted) setPreview(res);
    } catch (err) {
      if (!c.signal.aborted) setError(errorMessage(err));
    } finally {
      if (!c.signal.aborted) setLoading(false);
    }
  }, []);

  const pick = useCallback(
    (f: File | null | undefined) => {
      if (!f) return;
      ctrl.current?.abort();
      setPreview(null);
      setLoading(false);
      if (!/\.(csv|txt)$/i.test(f.name) && f.type !== "text/csv") {
        setFile(null);
        setError("Please choose a .csv file (Excel: File → Save As → CSV).");
        return;
      }
      if (f.size === 0) {
        setFile(null);
        setError("That file is empty. Export the sheet again with a header row and at least one number.");
        return;
      }
      if (f.size > MAX_BYTES) {
        setFile(null);
        setError("That file is over 20 MB. Split it into smaller files.");
        return;
      }
      setFile(f);
      void load(f);
    },
    [load],
  );

  const clear = useCallback(() => {
    ctrl.current?.abort();
    setFile(null);
    setPreview(null);
    setError(null);
    setLoading(false);
  }, []);

  const retry = useCallback(() => {
    if (file) void load(file);
  }, [file, load]);

  return { file, preview, loading, error, pick, clear, retry };
}

type View = "all" | "callable" | "duplicate" | "invalid" | "dnc";

const ROW_STATUS: Record<CsvRowStatus, { label: string; color: ChipColor; icon: ReactNode; removed: boolean }> = {
  new: { label: "New lead", color: "primary", icon: <UserPlus aria-hidden />, removed: false },
  existing: { label: "Already a lead", color: "white", icon: <UserCheck aria-hidden />, removed: false },
  duplicate: { label: "Duplicate, removed", color: "neutral", icon: <Copy aria-hidden />, removed: true },
  invalid: { label: "Invalid, removed", color: "neutral", icon: <CircleAlert aria-hidden />, removed: true },
  dnc: { label: "Do-not-call, skipped", color: "neutral", icon: <Ban aria-hidden />, removed: true },
};

function matches(view: View, row: CsvPreviewRow) {
  if (view === "all") return true;
  if (view === "callable") return row.status === "new" || row.status === "existing";
  return row.status === view;
}

/** Upload step of "New campaign → From a CSV file": template, drop zone, then the full number list. */
export function CsvUploadPanel({ csv }: { csv: CsvState }) {
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDrag(false);
    csv.pick(e.dataTransfer.files?.[0]);
  }

  const fileInput = (
    <input
      ref={inputRef}
      type="file"
      accept=".csv,text/csv"
      className="sr-only"
      aria-label="Campaign CSV file"
      onChange={(e) => {
        csv.pick(e.target.files?.[0]);
        e.target.value = "";
      }}
    />
  );

  if (!csv.file) {
    return (
      <div className="space-y-4">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
          className={cn(
            "flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-8 text-center transition-colors",
            drag ? "border-primary bg-primary-tint" : "border-line bg-canvas",
          )}
        >
          <span className="inline-flex size-12 items-center justify-center rounded-full bg-primary text-white [&_svg]:size-5" aria-hidden>
            <Upload />
          </span>
          <p className="mt-3 font-heading text-base font-semibold tracking-tight">Drop your CSV here</p>
          <p className="mt-0.5 text-[13px] font-normal text-muted">We list every number before anything is created.</p>
          <Button className="mt-3" size="sm" variant="primary" onClick={() => inputRef.current?.click()} icon={<FileSpreadsheet />}>
            Choose CSV file
          </Button>
          {fileInput}
        </div>
        {csv.error ? <ErrorLine>{csv.error}</ErrorLine> : null}
        <CsvTemplate />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-white px-3 py-2">
        <FileSpreadsheet className="size-5 shrink-0 text-primary" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold">{csv.file.name}</span>
        <span className="text-xs font-medium text-muted">{prettySize(csv.file.size)}</span>
        <Button size="sm" variant="ghost" icon={<RefreshCw />} onClick={() => inputRef.current?.click()}>
          Change file
        </Button>
        <button type="button" onClick={csv.clear} className="rounded-full p-1 hover:bg-canvas [&_svg]:size-4" aria-label="Remove file">
          <X aria-hidden />
        </button>
        {fileInput}
      </div>

      {csv.loading ? (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-line bg-canvas py-10 text-sm font-medium text-muted" role="status">
          <LoaderCircle className="spin size-4" aria-hidden />
          Reading the numbers…
        </div>
      ) : csv.error ? (
        <div className="space-y-3">
          <ErrorLine>{csv.error}</ErrorLine>
          <div className="flex gap-2">
            <Button size="sm" icon={<RefreshCw />} onClick={csv.retry}>
              Try again
            </Button>
            <Button size="sm" variant="ghost" onClick={csv.clear}>
              Choose another file
            </Button>
          </div>
        </div>
      ) : csv.preview ? (
        <PreviewBody preview={csv.preview} />
      ) : null}
    </div>
  );
}

function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-start gap-2 rounded-lg border border-line bg-subtle px-3 py-2 text-sm font-medium" role="alert">
      <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}

function SummaryTile({ label, value, caption, highlight }: { label: string; value: number; caption: string; highlight?: boolean }) {
  return (
    <div className={cn("rounded-xl border p-3", highlight ? "border-primary-tint-strong bg-primary-tint" : "border-line bg-white")}>
      <p className="text-[12px] font-medium text-muted">{label}</p>
      <p className={cn("tabular mt-1 font-heading text-2xl leading-none font-semibold", highlight && "text-primary", value === 0 && !highlight && "text-faint")}>
        {formatNumber(value)}
      </p>
      <p className="mt-1 text-[11px] font-normal text-muted">{caption}</p>
    </div>
  );
}

function PreviewBody({ preview }: { preview: CsvPreview }) {
  const { summary: s, rows } = preview;
  const [view, setView] = useState<View>("all");
  const [page, setPage] = useState(1);

  const filtered = useMemo(() => rows.filter((r) => matches(view, r)), [rows, view]);
  const pageRows = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const removed = s.duplicate + s.invalid + s.dnc;

  const views: { value: View; label: string }[] = [
    { value: "all", label: `All rows (${formatNumber(s.rows)})` },
    { value: "callable", label: `To call (${formatNumber(s.callable)})` },
    { value: "duplicate", label: `Duplicates (${formatNumber(s.duplicate)})` },
    { value: "invalid", label: `Invalid (${formatNumber(s.invalid)})` },
    ...(s.dnc > 0 ? [{ value: "dnc" as const, label: `Do-not-call (${formatNumber(s.dnc)})` }] : []),
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <SummaryTile label="Numbers to call" value={s.callable} caption={`of ${pluralize(s.rows, "row")} in the file`} highlight />
        <SummaryTile label="New leads" value={s.new} caption="Added to Leads" />
        <SummaryTile label="Already leads" value={s.existing} caption="Reused, not duplicated" />
        <SummaryTile label="Duplicates removed" value={s.duplicate} caption="Same number repeated" />
        <SummaryTile label="Invalid / skipped" value={s.invalid + s.dnc} caption={s.dnc ? `${formatNumber(s.dnc)} do-not-call` : "Bad or missing number"} />
      </div>

      {s.callable === 0 ? (
        <ErrorLine>No callable numbers in this file. Check the phone column, or use the template above.</ErrorLine>
      ) : (
        <p className="flex items-center gap-2 text-[13px] font-medium">
          <PhoneCall className="size-4 text-primary" aria-hidden />
          The agent will call {pluralize(s.callable, "unique number")} in file order
          {removed > 0 ? <span className="font-normal text-muted">· {formatNumber(removed)} left out (listed below)</span> : null}
        </p>
      )}

      <div className="rounded-xl border border-line bg-white">
        <div className="scroll-thin overflow-x-auto border-b border-line p-2">
          <Segmented<View>
            label="Show rows"
            size="sm"
            options={views}
            value={view}
            onChange={(v) => {
              setView(v);
              setPage(1);
            }}
            className="flex-nowrap"
          />
        </div>
        <div className="scroll-thin max-h-[340px] overflow-auto">
          <table className="data-table">
            <thead className="sticky top-0 z-[1] bg-white">
              <tr>
                <th scope="col" className="text-right">
                  Row
                </th>
                <th scope="col">Phone</th>
                <th scope="col">Name</th>
                <th scope="col">Company</th>
                <th scope="col">City</th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-muted">
                    No rows in this view.
                  </td>
                </tr>
              ) : (
                pageRows.map((r) => {
                  const st = ROW_STATUS[r.status];
                  return (
                    <tr key={r.line} className={st.removed ? "bg-canvas" : undefined}>
                      <td className="tabular text-right text-muted">{r.line}</td>
                      <td className={cn("tabular whitespace-nowrap", st.removed ? "text-muted line-through decoration-faint" : "font-semibold")}>
                        {r.phone ? formatPhone(r.phone) : r.raw_phone || <span className="no-underline">(empty)</span>}
                      </td>
                      <td className="max-w-[180px] truncate">{r.name || <span className="text-faint">—</span>}</td>
                      <td className="max-w-[160px] truncate text-muted">{r.company || "—"}</td>
                      <td className="max-w-[120px] truncate text-muted">{r.city || "—"}</td>
                      <td className="min-w-[200px]">
                        <Chip size="xs" color={st.color} icon={st.icon}>
                          {st.label}
                        </Chip>
                        {r.problem ? <span className="mt-0.5 block text-[12px] font-normal text-muted">{r.problem}</span> : null}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        {filtered.length > PAGE_SIZE ? <Pagination page={page} pageSize={PAGE_SIZE} total={filtered.length} onPage={setPage} /> : null}
        {preview.truncated ? (
          <p className="border-t border-line px-4 py-2.5 text-[12px] font-normal text-muted">
            Listing the first {formatNumber(rows.length)} rows. The counts above cover all {formatNumber(s.rows)} rows, and every
            callable number is added.
          </p>
        ) : null}
      </div>
    </div>
  );
}
