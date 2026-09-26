"use client";

import { AlertTriangle } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";

import { CopyButton } from "@/components/feedback/copy-button";
import { Button } from "@/components/ui/button";
import { routes } from "@/lib/navigation";

/**
 * What a route shows when rendering it throws. Next.js gives these boundaries an
 * opaque `digest` -- the handle that ties this screen to the server log line -- so
 * it is shown as the reference, exactly as an API failure shows its request id.
 * The error's message is not shown: it is an exception from our own code, and says
 * nothing a person can act on.
 */
export function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div
      role="alert"
      className="mx-auto flex max-w-md flex-col items-center px-4 py-24 text-center"
    >
      <span className="border-danger/60 text-danger inline-flex size-12 items-center justify-center border">
        <AlertTriangle className="size-5" strokeWidth={1.5} aria-hidden="true" />
      </span>
      <h1 className="text-fg mt-6 font-serif text-4xl font-light tracking-tight">
        Something went wrong
      </h1>
      <p className="text-fg-muted mt-1.5 text-base">
        This page hit an unexpected problem. Trying again often clears it; if it doesn&apos;t, quote
        the reference below to support.
      </p>
      {error.digest ? (
        <p className="text-fg-subtle mt-3 flex items-center gap-1 text-xs">
          Reference <code className="text-fg-muted font-mono">{error.digest}</code>
          <CopyButton value={error.digest} label="Copy reference" />
        </p>
      ) : null}
      <div className="mt-6 flex gap-2">
        <Button variant="primary" onClick={reset}>
          Try again
        </Button>
        <Button asChild>
          <Link href={routes.home}>Go to ORBIT home</Link>
        </Button>
      </div>
    </div>
  );
}
