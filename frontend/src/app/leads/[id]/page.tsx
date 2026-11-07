import type { Metadata } from "next";
import { LeadDetailView } from "@/components/leads/LeadDetailView";

export const metadata: Metadata = { title: "Lead" };

export default async function LeadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <LeadDetailView id={Number(id)} />;
}
