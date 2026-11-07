import { Compass } from "lucide-react";
import { ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/States";

export default function NotFound() {
  return (
    <EmptyState
      icon={<Compass />}
      tone="subtle"
      title="Page not found"
      body="That page doesn't exist. Head back to the dashboard."
      action={<ButtonLink href="/" variant="primary">Go to dashboard</ButtonLink>}
    />
  );
}
