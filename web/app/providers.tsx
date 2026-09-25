"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { SessionEvents } from "@/app/session-events";
import { Toaster } from "@/components/feedback/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Announcer } from "@/lib/a11y/announcer";
import { createQueryClient } from "@/lib/query/client";

/**
 * Client-side providers for the whole application.
 *
 * The QueryClient is created inside `useState` rather than at module scope. A
 * module-level client would be shared across every request on the server, which
 * leaks one user's cached data into another user's render -- a cross-tenant
 * disclosure introduced by an apparently harmless convenience.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(createQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={400} skipDelayDuration={200}>
        {children}
      </TooltipProvider>
      <SessionEvents />
      <Toaster />
      <Announcer />
    </QueryClientProvider>
  );
}
