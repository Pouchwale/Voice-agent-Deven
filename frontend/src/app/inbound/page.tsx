import type { Metadata } from "next";
import { Suspense } from "react";
import { InboundView } from "@/components/inbound/InboundView";
import { SkeletonRows } from "@/components/ui/Skeleton";

export const metadata: Metadata = { title: "Inbound" };

export default function InboundPage() {
  return (
    <Suspense fallback={<SkeletonRows rows={8} cols={7} />}>
      <InboundView />
    </Suspense>
  );
}
