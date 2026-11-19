import type { Metadata } from "next";
import { Suspense } from "react";
import { ActivityView } from "@/components/activity/ActivityView";
import { Skeleton } from "@/components/ui/Skeleton";

export const metadata: Metadata = { title: "Activity log" };

export default function ActivityPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[480px] rounded-xl" />}>
      <ActivityView />
    </Suspense>
  );
}
