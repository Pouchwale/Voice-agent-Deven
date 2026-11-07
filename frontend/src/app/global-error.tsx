"use client";

import "./globals.css";

/** Last-resort boundary for errors in the root layout itself (sidebar, header, providers). */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body>
        <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center gap-3 px-6 text-center">
          <h1 className="font-heading text-lg font-semibold">The dashboard hit an unexpected error</h1>
          <p className="text-sm text-muted">Reload the page. If it keeps happening, check that the backend is running and up to date.</p>
          <button type="button" className="btn bg-primary text-white hover:bg-primary-hover" onClick={reset}>
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
