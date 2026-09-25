"use client";

import { useEffect } from "react";

/**
 * Sets the tab title for pages whose title depends on loaded data (a document's
 * name). Static pages use the `metadata` export instead. Restores the previous
 * title on unmount so a stale name never outlives its page.
 */
export function useDocumentTitle(title: string | undefined): void {
  useEffect(() => {
    if (!title) return;
    const previous = document.title;
    document.title = `${title} · ORBIT`;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
