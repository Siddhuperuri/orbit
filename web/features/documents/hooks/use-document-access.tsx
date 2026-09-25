"use client";

import { createContext, useContext, type ReactNode } from "react";

import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";

/**
 * Whether the document on screen still exists for this user.
 *
 * A detail page keeps showing the last copy it loaded after learning the document is gone
 * (deleted by someone else, or access revoked) -- yanking a reader away mid-thought is worse
 * than a labelled snapshot. But the panels around that snapshot each make their own requests,
 * and every one of them would now be a 404. This is how the page tells them.
 */
const AvailabilityContext = createContext(false);

export function DocumentAvailability({ gone, children }: { gone: boolean; children: ReactNode }) {
  return <AvailabilityContext.Provider value={gone}>{children}</AvailabilityContext.Provider>;
}

/**
 * What this user may do with the document on screen *right now*: their role's permissions,
 * and nothing at all once the document is gone. Panels ask this rather than `can()` directly,
 * so "no control for something you can't change" has one definition and a missing document
 * cannot be forgotten by whichever panel was written last.
 */
export function useDocumentAccess() {
  const { can } = useWorkspace();
  const gone = useContext(AvailabilityContext);
  return {
    gone,
    canUpdate: !gone && can("document:update"),
    canTag: !gone && can("tag:write"),
    /** Reading is what let the page open at all; it only ends when the document does. */
    canDownload: !gone,
  };
}
