"use client";

import { useQueries, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { documentApi } from "@/features/documents/api/endpoints";
import { patchDocumentInLists } from "@/features/documents/api/cache";
import { documentKeys } from "@/features/documents/api/keys";
import { isInProgress, statusOf } from "@/features/documents/status";
import type { Document } from "@/features/documents/types";
import { announce } from "@/lib/a11y/announcer";
import { pollDelay } from "@/lib/utils/polling";

/**
 * A ceiling on how many documents are watched at once. Uploading fifty files
 * should not fire fifty simultaneous polls; the rest are picked up as earlier ones
 * finish and the list re-renders.
 */
const MAX_WATCHED = 10;

/**
 * Follows documents that are still being processed until they finish, and folds
 * each update back into the lists and detail views that show them.
 *
 * Scoped, backed-off, and self-terminating (ADR-0016). Only documents in a
 * non-terminal state are polled, each on its own schedule; the poll for a document
 * stops the moment it reaches READY or FAILED, and when nothing is in progress
 * this hook runs no queries at all. There is no blanket interval on the list.
 *
 * The backoff counts **polls made during this document's current stretch of processing**,
 * not how many times its cache entry has ever been written. The two differ wildly: a
 * document someone has been reading has had its entry written by page loads, patches from
 * renames and tags, and refetches on focus. Backing off by *that* count meant that uploading
 * a new version of a document that had been open for a while scheduled the first check 15
 * seconds out, and the page sat on "processing" long after the worker had finished.
 *
 * Completion is also announced to assistive technology: a sighted user notices a
 * badge change peripherally, a screen-reader user otherwise never learns it
 * happened.
 */
export function useProcessingWatcher(workspaceId: string, documents: readonly Document[]) {
  const queryClient = useQueryClient();

  const watchedIds = documents
    .filter(isInProgress)
    .slice(0, MAX_WATCHED)
    .map((document) => document.id);

  // Polls made per document in its current in-progress stretch. Counted where a poll
  // *happens* (the query function), and dropped once the document is no longer watched, so
  // the next stretch -- a new version, a retry -- starts again at the short delay.
  const polls = useRef(new Map<string, number>());
  const watched = watchedIds.join("|");
  useEffect(() => {
    const keep = new Set(watched === "" ? [] : watched.split("|"));
    for (const id of polls.current.keys()) if (!keep.has(id)) polls.current.delete(id);
  }, [watched]);

  const results = useQueries({
    queries: watchedIds.map((documentId) => ({
      queryKey: documentKeys.detail(workspaceId, documentId),
      queryFn: ({ signal }: { signal: AbortSignal }) => {
        polls.current.set(documentId, (polls.current.get(documentId) ?? 0) + 1);
        return documentApi.get(workspaceId, documentId, signal);
      },
      // Always ask the server: the cached copy is what told us it was unfinished.
      staleTime: 0,
      // Interval polling pauses while the tab is in the background (deliberately: nobody is
      // looking). Coming back is the moment to check at once, rather than wait out whatever is
      // left of a backoff that may be 15 seconds long. Only in-progress documents are watched,
      // so this is at most a handful of requests.
      refetchOnWindowFocus: true,
      refetchInterval: (query: { state: { data: Document | undefined } }) => {
        const latest = query.state.data;
        if (latest && !isInProgress(latest)) {
          polls.current.delete(documentId);
          return false;
        }
        // The fetch that just ran was poll one, so the delay after it is the *first* backoff step.
        return pollDelay(Math.max(0, (polls.current.get(documentId) ?? 1) - 1));
      },
    })),
  });

  // The last status announced per document, so a re-render never repeats an announcement.
  const seen = useRef(new Map<string, string>());
  const signature = results.map((result) => `${result.data?.id}:${result.dataUpdatedAt}`).join("|");

  useEffect(() => {
    // Remember the state the list showed *before* the first poll answered, so a
    // document that finishes inside that first interval is still announced.
    for (const document of documents) {
      if (isInProgress(document) && !seen.current.has(document.id)) {
        seen.current.set(document.id, statusOf(document));
      }
    }

    for (const result of results) {
      const document = result.data;
      if (!document) continue;

      const status = statusOf(document);
      const previous = seen.current.get(document.id);
      seen.current.set(document.id, status);

      // Lists hold their own copies; bring them in line. (The detail entry is the
      // one this hook polls, so it is already current.)
      patchDocumentInLists(queryClient, workspaceId, document);

      if (previous && previous !== status && !isInProgress(document)) {
        announce(
          status === "ready" ? `${document.title} is ready` : `${document.title} failed to process`,
          status === "failed" ? "assertive" : "polite",
        );
      }
    }
    // `signature` captures exactly the changes worth reacting to.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, queryClient, workspaceId]);

  return { watching: watchedIds.length };
}
