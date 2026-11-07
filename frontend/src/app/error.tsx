"use client";

import { CircleAlert, RefreshCw } from "lucide-react";
import { useEffect } from "react";
import { Button, ButtonLink } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/States";

/** Route error boundary: a render crash (e.g. an unexpected API response) keeps the shell and offers a retry. */
export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="rounded-xl border border-line bg-white">
      <EmptyState
        icon={<CircleAlert />}
        tone="subtle"
        title="Something went wrong on this page"
        body="The page hit an unexpected error, possibly from an unexpected backend response. Try again, or go back to the dashboard."
        action={
          <>
            <Button variant="primary" icon={<RefreshCw />} onClick={reset}>
              Try again
            </Button>
            <ButtonLink href="/">Go to dashboard</ButtonLink>
          </>
        }
      />
    </div>
  );
}
