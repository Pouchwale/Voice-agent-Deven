import type { Metadata } from "next";
import { Suspense } from "react";
import { ReviewView } from "@/components/review/ReviewView";
import { Skeleton } from "@/components/ui/Skeleton";

export const metadata: Metadata = { title: "Call review" };

export default function ReviewPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[480px] rounded-xl" />}>
      <ReviewView />
    </Suspense>
  );
}
