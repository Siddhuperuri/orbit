"use client";

import { Download, Info } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef } from "react";

import { QueryBoundary } from "@/components/feedback/query-boundary";
import { Button } from "@/components/ui/button";
import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";
import { useDocumentContent, useDownloadDocument } from "@/features/documents/api/use-documents";
import type { Document, Passage } from "@/features/documents/types";
import { useDocumentAccess } from "@/features/documents/hooks/use-document-access";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { cn } from "@/lib/utils/cn";
import { pluralize } from "@/lib/utils/format";

/**
 * Following a link to a passage may need several pages loaded. This bounds the chase, so a
 * link naming an ordinal that does not exist (an old citation, an edited URL) gives up
 * instead of paging through the whole document looking for it.
 */
const MAX_PAGES_TO_FIND_PASSAGE = 25;

function pageLabel(passage: Passage): string | null {
  if (passage.page_from === null) return null;
  return passage.page_to !== null && passage.page_to !== passage.page_from
    ? `Pages ${passage.page_from}–${passage.page_to}`
    : `Page ${passage.page_from}`;
}

function ViewerSkeleton() {
  return (
    <LoadingRegion label="Loading the document's text" className="space-y-4">
      {[0, 1, 2].map((index) => (
        <div key={index} className="space-y-2">
          <Skeleton className="h-3 w-1/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ))}
    </LoadingRegion>
  );
}

function PassageView({
  passage,
  previous,
  highlighted,
}: {
  passage: Passage;
  previous: Passage | undefined;
  highlighted: boolean;
}) {
  // A heading or page is shown when it *changes*: repeating it above every passage of a
  // section is noise, and the reader only needs to know where a new part begins.
  const heading = passage.heading_path && passage.heading_path !== previous?.heading_path;
  const page = pageLabel(passage);
  const showPage = page && page !== (previous ? pageLabel(previous) : null);

  return (
    <section
      id={`passage-${passage.ordinal}`}
      aria-label={`Passage ${passage.ordinal + 1}`}
      aria-current={highlighted ? "location" : undefined}
      className={cn(
        "scroll-mt-20 rounded-md py-1",
        highlighted && "bg-accent-soft/60 ring-accent -mx-3 px-3 ring-2",
      )}
    >
      {heading || showPage ? (
        <p className="text-fg-muted mb-1 text-xs font-medium tracking-wide uppercase">
          {[heading ? passage.heading_path : null, showPage ? page : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      ) : null}
      {/* Plain text in a text node: the document is untrusted, so it is never parsed as markup. */}
      {passage.text ? <p className="reading whitespace-pre-wrap">{passage.text}</p> : null}
    </section>
  );
}

/**
 * The document's text -- what ORBIT actually indexed, in reading order.
 *
 * This is deliberately *not* a rendering of the original file. A PDF's layout, images, and
 * typography are not kept, and pretending otherwise would show a document that disagrees
 * with what search and answers can see. The text shown is exactly what a citation points
 * into, which is why a link from an answer (`?passage=N`) can land on the passage it quotes.
 *
 * Passages are paged in as the reader asks for them; nothing loads the whole document.
 */
export function DocumentViewer({ document }: { document: Document }) {
  const { workspace } = useWorkspace();
  const { canDownload, gone } = useDocumentAccess();
  const download = useDownloadDocument(workspace.id);
  const searchParams = useSearchParams();
  const version = document.current_version;
  const ready = version?.status === "ready";

  const target = useMemo(() => {
    const raw = searchParams.get("passage");
    const ordinal = raw === null ? Number.NaN : Number.parseInt(raw, 10);
    const cited = Number.parseInt(searchParams.get("v") ?? "", 10);
    return {
      ordinal: Number.isInteger(ordinal) && ordinal >= 0 ? ordinal : null,
      citedVersion: Number.isInteger(cited) ? cited : null,
    };
  }, [searchParams]);

  const query = useDocumentContent(
    workspace.id,
    document.id,
    version?.version_number ?? 0,
    ready && version !== null,
  );
  const passages = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  );

  // Find and reveal the linked passage, loading further pages if it is not yet on screen.
  const revealed = useRef<number | null>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query;
  const pagesLoaded = query.data?.pages.length ?? 0;
  useEffect(() => {
    if (target.ordinal === null || passages.length === 0) return;
    const found = passages.some((passage) => passage.ordinal === target.ordinal);
    if (found) {
      if (revealed.current !== target.ordinal) {
        revealed.current = target.ordinal;
        globalThis.document
          .getElementById(`passage-${target.ordinal}`)
          ?.scrollIntoView({ block: "center" });
      }
      return;
    }
    const last = passages[passages.length - 1];
    const beyond = last !== undefined && last.ordinal < target.ordinal;
    if (beyond && hasNextPage && !isFetchingNextPage && pagesLoaded < MAX_PAGES_TO_FIND_PASSAGE) {
      void fetchNextPage();
    }
  }, [target.ordinal, passages, hasNextPage, isFetchingNextPage, fetchNextPage, pagesLoaded]);

  const stale =
    target.ordinal !== null &&
    target.citedVersion !== null &&
    version !== null &&
    target.citedVersion !== version.version_number;

  return (
    <section aria-labelledby="text-heading">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="text-heading" className="text-md text-fg font-semibold">
          Text
        </h2>
        {version && canDownload ? (
          <Button
            size="sm"
            variant="ghost"
            loading={download.isPending}
            onClick={() => download.mutate(document.id)}
          >
            <Download aria-hidden="true" />
            Download original
          </Button>
        ) : null}
      </div>

      <p className="text-fg-muted mb-4 flex items-start gap-1.5 text-sm">
        <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span>
          This is the text ORBIT read and indexed, not the original file&apos;s layout. Search and
          answers cite passages of exactly this text.
        </span>
      </p>

      {stale ? (
        <p
          role="note"
          className="border-warning/30 bg-warning-soft text-warning mb-4 rounded-md border px-3 py-2 text-sm"
        >
          That link cites version {target.citedVersion} of this document, which has since been
          replaced. You&apos;re reading version {version?.version_number}, so passage{" "}
          {(target.ordinal ?? 0) + 1} may say something different.
        </p>
      ) : null}

      {!version ? (
        <p className="text-fg-muted text-base">There is no file for this document yet.</p>
      ) : version.status === "failed" ? (
        <p className="text-fg-muted text-base">
          No text is available because processing failed. The reason is shown above.
        </p>
      ) : !ready ? (
        <p role="status" className="text-fg-muted text-base">
          The text will appear here once ORBIT has finished reading this document.
        </p>
      ) : (
        <QueryBoundary
          query={query}
          loading={<ViewerSkeleton />}
          errorTitle="Couldn't load the document's text"
          staleNotice={!gone}
          isEmpty={() => passages.length === 0}
          empty={
            <p className="text-fg-muted text-base">
              This version has no readable passages yet. If you just uploaded it, give it a moment.
            </p>
          }
        >
          {() => (
            <>
              <div className="space-y-5">
                {passages.map((passage, index) => (
                  <PassageView
                    key={passage.ordinal}
                    passage={passage}
                    previous={passages[index - 1]}
                    highlighted={passage.ordinal === target.ordinal}
                  />
                ))}
              </div>

              {target.ordinal !== null &&
              !passages.some((passage) => passage.ordinal === target.ordinal) &&
              !hasNextPage ? (
                <p role="note" className="text-fg-muted mt-4 text-sm">
                  Passage {target.ordinal + 1} isn&apos;t in this version&apos;s text.
                </p>
              ) : null}

              <div className="text-fg-muted mt-5 flex flex-wrap items-center gap-3 text-sm">
                <span>
                  Showing {passages.length} of {pluralize(version.chunk_count, "passage")}
                </span>
                {hasNextPage ? (
                  <Button
                    size="sm"
                    loading={isFetchingNextPage}
                    onClick={() => void fetchNextPage()}
                  >
                    {isFetchingNextPage ? "Loading" : "Load more text"}
                  </Button>
                ) : null}
              </div>
            </>
          )}
        </QueryBoundary>
      )}
    </section>
  );
}
