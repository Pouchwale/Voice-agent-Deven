import type { Metadata } from "next";
import { Suspense } from "react";
import { CallsView } from "@/components/calls/CallsView";
import { SkeletonRows } from "@/components/ui/Skeleton";

export const metadata: Metadata = { title: "Calls" };

export default function CallsPage() {
  return (
    <Suspense fallback={<SkeletonRows rows={8} cols={7} />}>
      <CallsView />
    </Suspense>
  );
}
