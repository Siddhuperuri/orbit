"use client";

import {
  ChevronLeft,
  ChevronRight,
  Info,
  Quote,
  SearchX,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { EmptyState } from "@/components/feedback/empty-state";
import { ErrorState } from "@/components/feedback/error-state";
import { PageContainer } from "@/components/layout/page-container";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";
import { setPrefilledQuestion } from "@/features/chat/lib/prefill";
import { useSearch } from "@/features/search/api/use-search";
import { SearchFilters } from "@/features/search/components/search-filters";
import { SearchForm } from "@/features/search/components/search-form";
import { SearchResultItem } from "@/features/search/components/search-result-item";
import {
  MAX_PAGE,
  isNarrowed,
  parseSearchState,
  searchHref,
  withoutFilters,
  type SearchState,
} from "@/features/search/lib/search-params";
import { DEFAULT_RESULT_LIMIT, type SearchResult } from "@/features/search/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { announce } from "@/lib/a11y/announcer";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";
import { pluralize } from "@/lib/utils/format";

/** Explains a degraded search in terms of what the user gets, not which component failed. */
function degradedNotice(degraded: string): string {
  if (degraded.startsWith("semantic")) {
    return "Meaning-based matching is unavailable right now, so these results match your words only.";
  }
  return "Some matching methods were unavailable, so these results may be incomplete.";
}

function ResultsSkeleton() {
  return (
    <LoadingRegion label="Searching" className="space-y-6">
      {Array.from({ length: 3 }, (_, index) => (
        <div key={index} className="space-y-2">
          <Skeleton className="h-5 w-1/3" />
          <Skeleton className="h-3 w-1/4" />
          <Skeleton className="h-16 w-full" />
        </div>
      ))}
    </LoadingRegion>
  );
}

export function SearchPageContent() {
  const { workspace, can } = useWorkspace();
  const router = useRouter();
  const params = useSearchParams();
  const state = parseSearchState(params);

  const search = useSearch(workspace.id, state);
  const data = search.data;
  const settled = data !== undefined && !search.isPlaceholderData;

  function go(next: SearchState, options: { scroll?: boolean } = {}) {
    router.push(searchHref(workspace.id, next), { scroll: options.scroll ?? false });
  }

  /** Carries a result's document into a new question, without putting it in a URL. */
  function ask(result: SearchResult) {
    setPrefilledQuestion(state.query);
    router.push(routes.chat(workspace.id, { doc: result.document.id }));
  }

  const first = state.page * DEFAULT_RESULT_LIMIT + 1;
  const last = state.page * DEFAULT_RESULT_LIMIT + (data?.results.length ?? 0);

  useEffect(() => {
    if (!settled) return;
    announce(
      data.results.length === 0
        ? `No results for ${data.query}`
        : `${pluralize(data.results.length, "result")} for ${data.query}`,
    );
  }, [settled, data]);

  return (
    <PageContainer>
      <PageHeader
        title="Search"
        description="Find passages across every document in this workspace."
      />

      {/* Keyed on what was searched, so the field resets when the address changes. */}
      <SearchForm
        key={`${state.query}|${state.mode}|${state.documentId ?? ""}`}
        workspaceId={workspace.id}
        state={state}
        onSubmit={go}
      />

      <div className="mt-4">
        <SearchFilters workspaceId={workspace.id} state={state} onChange={go} />
      </div>

      <div className="mt-8" aria-busy={search.isFetching || undefined}>
        {state.query === "" ? (
          <SearchTips />
        ) : search.isPending ? (
          <ResultsSkeleton />
        ) : !data ? (
          <ErrorState
            error={search.error}
            title="Search failed"
            onRetry={() => void search.refetch()}
            retrying={search.isFetching}
          />
        ) : (
          <div className={search.isPlaceholderData ? "opacity-60 transition-opacity" : undefined}>
            {data.degraded ? (
              <p
                role="status"
                className="border-warning/40 text-warning mb-6 flex items-start gap-2 border px-4 py-3 text-sm"
              >
                <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                {degradedNotice(data.degraded)}
              </p>
            ) : null}

            {data.results.length === 0 ? (
              <EmptyResults state={state} onChange={go} workspaceId={workspace.id} />
            ) : (
              <>
                <p className="label-micro text-fg-subtle mb-4">
                  {/* A range, not a total: the API deliberately does not count every
                      matching chunk, and inventing "about N" would be a guess. */}
                  {state.page === 0 && !data.has_more
                    ? pluralize(data.results.length, "result")
                    : `Results ${first}–${last}`}
                </p>
                <ol aria-label="Search results" className="border-line border-b">
                  {data.results.map((result) => (
                    <SearchResultItem
                      key={result.chunk.id}
                      result={result}
                      query={state.query}
                      workspaceId={workspace.id}
                      mode={state.mode}
                      onAsk={can("chat:use") ? ask : undefined}
                    />
                  ))}
                </ol>

                <Pager state={state} hasMore={data.has_more} onChange={go} />
              </>
            )}
          </div>
        )}
      </div>
    </PageContainer>
  );
}

const TIPS = [
  {
    icon: Quote,
    title: "Results are passages",
    body: "Each result is the exact text that matched, with the document and page it came from.",
  },
  {
    icon: Sparkles,
    title: "Ask it like a question",
    body: "Hybrid matching finds passages that mean what you asked, even in different words.",
  },
  {
    icon: SlidersHorizontal,
    title: "Narrow it down",
    body: "Filter by folder, tag, or file type -- or switch to Keyword for names and codes.",
  },
];

/** Before the first search: what search does here, in three lines. */
function SearchTips() {
  return (
    <ul className="border-line bg-canvas grid border-y sm:grid-cols-3">
      {TIPS.map(({ icon: Icon, title, body }, index) => (
        <li
          key={title}
          className={cn(
            "enter border-line p-6 sm:not-first:border-l",
            index === 1 ? "enter-1" : index === 2 ? "enter-2" : "",
          )}
        >
          <span className="border-line-strong text-accent mb-10 inline-flex size-9 items-center justify-center border">
            <Icon className="size-4" strokeWidth={1.5} aria-hidden="true" />
          </span>
          <p className="text-fg text-xl font-medium tracking-tight">{title}</p>
          <p className="text-fg-muted mt-2 text-sm">{body}</p>
        </li>
      ))}
    </ul>
  );
}

/**
 * Nothing matched -- and *why* nothing matched decides what to offer.
 *
 * A filtered search that found nothing is one click from a search that might; an
 * unfiltered one needs a different query. Telling both the same thing wastes the
 * only moment the user is asking for help.
 */
function EmptyResults({
  state,
  onChange,
  workspaceId,
}: {
  state: SearchState;
  onChange: (next: SearchState) => void;
  workspaceId: string;
}) {
  if (state.page > 0) {
    return (
      <EmptyState
        icon={SearchX}
        title="No more results"
        description="You've reached the end of the matches for this search."
        action={
          <Button onClick={() => onChange({ ...state, page: 0 })}>Back to the first page</Button>
        }
      />
    );
  }

  if (isNarrowed(state)) {
    return (
      <EmptyState
        icon={SearchX}
        title="No matches with these filters"
        description="Your documents may still cover this. Searching everywhere is one click away."
        action={<Button onClick={() => onChange(withoutFilters(state))}>Search everywhere</Button>}
      />
    );
  }

  return (
    <EmptyState
      icon={SearchX}
      title="No matching passages"
      description={
        state.mode === "lexical"
          ? "Try fewer or different words, or switch to meaning-based matching."
          : "Try different words, or a shorter query. Documents still being processed aren't searchable yet."
      }
      action={
        <Button asChild variant="ghost">
          <a href={routes.documents(workspaceId)}>Check your documents</a>
        </Button>
      }
    />
  );
}

/**
 * Previous and next, and nothing else.
 *
 * Numbered pages would need a total, and the API deliberately does not count every
 * matching chunk -- that would cost a second full scan to answer a question
 * ("4,812 results") that helps nobody decide what to read. `has_more` is exact, so
 * "Next" is never offered into an empty page.
 */
function Pager({
  state,
  hasMore,
  onChange,
}: {
  state: SearchState;
  hasMore: boolean;
  onChange: (next: SearchState, options?: { scroll?: boolean }) => void;
}) {
  const atCeiling = state.page >= MAX_PAGE;
  if (state.page === 0 && !hasMore) return null;

  return (
    <nav aria-label="Search result pages" className="mt-6 flex items-center justify-between gap-3">
      <Button
        size="sm"
        disabled={state.page === 0}
        onClick={() => onChange({ ...state, page: state.page - 1 }, { scroll: true })}
      >
        <ChevronLeft aria-hidden="true" />
        Previous
      </Button>

      <span className="text-fg-muted text-sm">Page {state.page + 1}</span>

      <div className="flex flex-col items-end gap-1">
        <Button
          size="sm"
          disabled={!hasMore || atCeiling}
          onClick={() => onChange({ ...state, page: state.page + 1 }, { scroll: true })}
        >
          Next
          <ChevronRight aria-hidden="true" />
        </Button>
        {hasMore && atCeiling ? (
          <p className="text-fg-subtle text-xs">
            This is as deep as search goes. Narrow the query to find it.
          </p>
        ) : null}
      </div>
    </nav>
  );
}
