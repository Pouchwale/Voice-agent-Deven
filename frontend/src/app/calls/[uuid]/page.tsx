import type { Metadata } from "next";
import { CallDetailView } from "@/components/calls/CallDetailView";

export const metadata: Metadata = { title: "Call summary" };

export default async function CallPage({ params }: { params: Promise<{ uuid: string }> }) {
  const { uuid } = await params;
  return <CallDetailView uuid={decodeURIComponent(uuid)} />;
}
