"use client";

import { RouteError } from "@/components/feedback/route-error";

/** Catches a render error anywhere outside the authenticated shell (the sign-in pages). */
export default function RootError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main id="main" tabIndex={-1}>
      <RouteError {...props} />
    </main>
  );
}
