import type { Metadata } from "next";
import { Suspense } from "react";
import { AnalyticsView } from "@/components/analytics/AnalyticsView";
import { Skeleton } from "@/components/ui/Skeleton";

export const metadata: Metadata = { title: "Analytics" };

export default function AnalyticsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[480px] rounded-xl" />}>
      <AnalyticsView />
    </Suspense>
  );
}
