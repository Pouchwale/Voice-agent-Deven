import { Download, FileSpreadsheet } from "lucide-react";
import { buttonClass } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";

/** Mirrors backend lead_csv.TEMPLATE_CSV (what "Download template" gives you). */
const COLUMNS: { key: string; required?: boolean; hint: string }[] = [
  { key: "name", hint: "Customer name" },
  { key: "phone", required: true, hint: "Indian mobile, any format" },
  { key: "company", hint: "Business / brand" },
  { key: "city", hint: "City" },
  { key: "email", hint: "Optional" },
  { key: "source", hint: "Where the lead came from" },
  { key: "notes", hint: "Anything for the agent" },
];

const SAMPLE: string[][] = [
  ["Rahul Sharma", "9876543210", "Sharma Snacks", "Ahmedabad", "rahul@example.com", "IndiaMART", "Namkeen brand"],
  ["Priya Patel", "+91 98250 12345", "Organic Roots", "Pune", "", "Website", "Needs stand-up pouches"],
];

/** Shows the expected CSV layout with sample rows and a download link for the template file. */
export function CsvTemplate({ className, compact = false }: { className?: string; compact?: boolean }) {
  return (
    <section className={cn("rounded-xl border border-line bg-white", className)} aria-labelledby="csv-template-title">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4">
        <div className="flex min-w-0 items-start gap-3">
          <span className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary [&_svg]:size-4.5" aria-hidden>
            <FileSpreadsheet />
          </span>
          <div className="min-w-0">
            <h3 id="csv-template-title" className="font-heading text-[15px] font-semibold">
              CSV template
            </h3>
            <p className="text-[13px] font-normal text-muted">
              One row per number. Only <span className="font-semibold text-fg">phone</span> is required; header names are
              flexible (&ldquo;Mobile&rdquo;, &ldquo;Customer&rdquo;, &ldquo;Brand&rdquo; work too).
            </p>
          </div>
        </div>
        <a href={api.campaigns.templateUrl()} download className={buttonClass({ variant: "secondary", size: "sm" })}>
          <Download aria-hidden />
          Download template
        </a>
      </div>

      <div className="scroll-thin mt-3 overflow-x-auto border-t border-line">
        <table className="w-full min-w-[640px] border-collapse text-left text-[12px]">
          <thead>
            <tr className="bg-canvas">
              {COLUMNS.map((c) => (
                <th key={c.key} scope="col" className="border-b border-line px-3 py-2 align-bottom font-semibold whitespace-nowrap">
                  <span className="font-mono text-[12px]">{c.key}</span>
                  {c.required ? (
                    <span className="ml-1.5 rounded-full bg-primary px-1.5 py-px text-[10px] font-semibold text-white">required</span>
                  ) : null}
                  {!compact ? <span className="block text-[11px] font-normal text-muted">{c.hint}</span> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {SAMPLE.map((row, i) => (
              <tr key={i} className={i < SAMPLE.length - 1 ? "border-b border-line" : undefined}>
                {row.map((cell, j) => (
                  <td key={j} className={cn("px-3 py-2 whitespace-nowrap", COLUMNS[j].required ? "tabular font-semibold" : "text-muted")}>
                    {cell || <span className="text-faint">(blank)</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ul className="grid gap-x-6 gap-y-1 border-t border-line px-4 py-3 text-[12px] font-normal text-muted sm:grid-cols-2">
        <li>Duplicate numbers are removed automatically (even if written differently).</li>
        <li>Invalid numbers and Do-Not-Call leads are left out and listed.</li>
        <li>Numbers already in your leads are reused, not duplicated.</li>
        <li>Excel: File &rarr; Save As &rarr; CSV. Keep the phone column as Text.</li>
      </ul>
    </section>
  );
}
