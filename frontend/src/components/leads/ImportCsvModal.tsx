"use client";

import { CircleAlert, Download, FileSpreadsheet, Upload, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { useSWRConfig } from "swr";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import type { ImportResult } from "@/lib/types";

const MAX_BYTES = 10 * 1024 * 1024;

function prettySize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function ResultTile({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className={cn("rounded-xl border border-line p-3 shadow-sm", tone)}>
      <p className="text-[13px] font-medium text-muted">{label}</p>
      <p className="mt-1 text-3xl leading-none font-semibold">{formatNumber(value)}</p>
    </div>
  );
}

export function ImportCsvModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const { mutate } = useSWRConfig();

  function reset() {
    setFile(null);
    setResult(null);
    setError(null);
    setDrag(false);
  }

  function close() {
    // Closing mid-upload would let the result land in a hidden modal and reappear on the next open.
    if (uploading) return;
    reset();
    onClose();
  }

  function pick(f: File | null | undefined) {
    setResult(null);
    setError(null);
    if (!f) return;
    if (!/\.csv$/i.test(f.name) && f.type !== "text/csv") {
      setError("Please choose a .csv file (Excel: File → Save as → CSV).");
      return;
    }
    if (f.size === 0) {
      setError("That file is empty. Export the sheet again with a header row and at least one lead.");
      return;
    }
    if (f.size > MAX_BYTES) {
      setError("That file is over 10 MB. Split it into smaller files.");
      return;
    }
    setFile(f);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDrag(false);
    pick(e.dataTransfer.files?.[0]);
  }

  async function upload() {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const res = await api.leads.import(file);
      setResult(res);
      void mutate((key) => typeof key === "string" && (key.startsWith("/api/leads") || key.startsWith("/api/dashboard")));
      toast({ tone: "success", title: `Imported ${formatNumber(res.imported)} leads` });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title="Import leads"
      description="Upload a CSV. We detect name, phone, company, city, email, source and notes columns automatically."
      size="lg"
      footer={
        result ? (
          <>
            <Button onClick={reset} icon={<Upload />}>
              Import another
            </Button>
            <Button variant="primary" onClick={close}>
              Done
            </Button>
          </>
        ) : (
          <>
            <a href={api.leads.templateUrl()} className="btn mr-auto bg-white px-4 py-2 text-[13px] hover:bg-canvas" download>
              <Download className="size-4" aria-hidden />
              Download template
            </a>
            <Button onClick={close} disabled={uploading}>
              Cancel
            </Button>
            <Button variant="primary" icon={<Upload />} onClick={() => void upload()} disabled={!file} loading={uploading}>
              {uploading ? "Importing…" : "Import"}
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div>
          <div className="grid grid-cols-3 gap-3">
            <ResultTile label="Imported" value={result.imported} tone="bg-primary-tint" />
            <ResultTile label="Duplicates" value={result.duplicates} tone="bg-subtle" />
            <ResultTile label="Invalid" value={result.invalid} tone="bg-subtle" />
          </div>
          <p className="mt-3 text-sm font-normal text-muted">
            Duplicates are phone numbers that already exist (they were skipped). Invalid rows were missing a usable name or phone.
          </p>
          {result.errors?.length ? (
            <div className="mt-4">
              <p className="field-label">Problems ({formatNumber(result.errors.length)})</p>
              <ul className="scroll-thin max-h-48 space-y-1 overflow-y-auto rounded-lg border border-line p-3 text-[13px] font-normal">
                {result.errors.slice(0, 50).map((e, i) => (
                  <li key={i} className="flex gap-2">
                    <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                    <span className="break-words">{e}</span>
                  </li>
                ))}
                {result.errors.length > 50 ? <li className="text-muted">…and {formatNumber(result.errors.length - 50)} more</li> : null}
              </ul>
            </div>
          ) : null}
        </div>
      ) : (
        <div>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={onDrop}
            className={cn(
              "flex flex-col items-center justify-center rounded-xl border border-dashed border-line px-6 py-10 text-center transition-colors",
              drag ? "bg-canvas" : "bg-canvas",
            )}
          >
            <span className="inline-flex size-14 items-center justify-center rounded-full border border-line bg-primary [&_svg]:size-6" aria-hidden>
              <FileSpreadsheet />
            </span>
            <p className="font-heading font-semibold tracking-tight mt-3 text-base">Drop your CSV here</p>
            <p className="mt-1 text-sm font-normal text-muted">or</p>
            <Button className="mt-2" size="sm" onClick={() => inputRef.current?.click()} icon={<Upload />}>
              Choose file
            </Button>
            <input
              ref={inputRef}
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              aria-label="CSV file"
              onChange={(e) => {
                pick(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
          </div>

          {file ? (
            <div className="mt-4 flex items-center gap-3 rounded-lg border border-line bg-white px-3 py-2">
              <FileSpreadsheet className="size-5 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1 truncate text-sm font-semibold">{file.name}</span>
              <span className="text-xs font-medium text-muted">{prettySize(file.size)}</span>
              <button type="button" onClick={() => setFile(null)} className="rounded-full p-1 hover:bg-canvas [&_svg]:size-4" aria-label="Remove file">
                <X aria-hidden />
              </button>
            </div>
          ) : null}

          {error ? (
            <p className="mt-4 rounded-lg border border-line bg-subtle px-3 py-2 text-sm font-medium" role="alert">
              {error}
            </p>
          ) : null}

          <ul className="mt-5 grid gap-1.5 text-[13px] font-normal sm:grid-cols-2">
            <li>
              <span className="font-semibold">Required:</span> name, phone
            </li>
            <li>
              <span className="font-semibold">Optional:</span> company, city, email, source, notes
            </li>
            <li>Header names are flexible (e.g. &ldquo;Mobile&rdquo;, &ldquo;Customer&rdquo;, &ldquo;Brand&rdquo;).</li>
            <li>Extra columns are kept with the lead.</li>
          </ul>
        </div>
      )}
    </Modal>
  );
}
