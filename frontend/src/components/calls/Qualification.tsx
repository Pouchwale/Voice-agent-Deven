import { CircleCheck, CircleDot, CircleMinus } from "lucide-react";
import type { ReactNode } from "react";
import { Chip, type ChipColor } from "@/components/ui/Badge";
import type { QualificationItem } from "@/lib/types";

const STATUS: Record<QualificationItem["status"], { label: string; color: ChipColor; icon: ReactNode }> = {
  answered: { label: "Answered", color: "primary", icon: <CircleCheck aria-hidden /> },
  partial: { label: "Partial", color: "neutral", icon: <CircleDot aria-hidden /> },
  not_asked: { label: "Not asked", color: "white", icon: <CircleMinus aria-hidden /> },
};

export function QualificationChecklist({ items }: { items: QualificationItem[] }) {
  const answered = items.filter((i) => i.status === "answered").length;
  return (
    <div>
      <p className="mb-3 text-[13px] text-muted">
        {answered} of {items.length} answered
      </p>
      <ol className="space-y-2.5">
        {items.map((q, i) => {
          const st = STATUS[q.status] ?? STATUS.not_asked;
          return (
            <li key={i} className="rounded-lg border border-line p-3">
              <div className="flex items-start justify-between gap-3">
                <p className="text-mixed text-sm font-medium">
                  <span className="tabular mr-1.5 text-muted">{i + 1}.</span>
                  {q.question}
                </p>
                <Chip size="xs" color={st.color} icon={st.icon} className="shrink-0">
                  {st.label}
                </Chip>
              </div>
              {q.answer ? (
                <p className="text-mixed mt-1 text-sm text-subtle-fg">{q.answer}</p>
              ) : (
                <p className="mt-1 text-[13px] text-muted">No answer captured.</p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
