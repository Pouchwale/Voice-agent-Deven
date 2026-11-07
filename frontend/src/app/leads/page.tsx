import type { Metadata } from "next";
import { Suspense } from "react";
import { LeadsView } from "@/components/leads/LeadsView";
import { SkeletonRows } from "@/components/ui/Skeleton";

export const metadata: Metadata = { title: "Leads" };

export default function LeadsPage() {
  return (
    <Suspense fallback={<SkeletonRows rows={8} cols={6} />}>
      <LeadsView />
    </Suspense>
  );
}
