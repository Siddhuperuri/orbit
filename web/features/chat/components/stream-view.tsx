"use client";

import { Fragment } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { StreamState } from "@/features/chat/state/stream-state";
import { describeError } from "@/lib/api/describe-error";
import { ApiError } from "@/lib/api/errors";
import { pluralize } from "@/lib/utils/format";

/** The question being answered, shown the moment it is sent. */
export function PendingQuestion({ question }: { question: string }) {
  return (
    <article aria-label="Your question" className="pb-2">
      <p className="text-fg-muted mb-1 text-xs font-medium tracking-wide uppercase">You</p>
      <p className="text-md text-fg font-medium [overflow-wrap:anywhere] whitespace-pre-wrap">
        {question}
      </p>
    </article>
  );
}

/** Provisional `[S1]` markers are dimmed: they only become citations when the answer completes. */
function ProvisionalText({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\[S\d{1,4}\])/gi).map((part, index) =>
        /^\[S\d{1,4}\]$/i.test(part) ? (
          <span key={index} className="text-fg-subtle">
            {part}
          </span>
        ) : (
          <Fragment key={index}>{part}</Fragment>
        ),
      )}
    </>
  );
}

/**
 * The answer while it is being written, and the failure state if it is not.
 *
 * Text is plain and provisional while streaming (the final message replaces it,
 * with Markdown formatting and resolved citations), which also avoids re-rendering
 * half-finished Markdown on every token. It is not announced token by token -- a
 * live region reading each word aloud would be unusable -- only its completion is.
 */
export function StreamView({
  state,
  onRetry,
  onDismiss,
}: {
  state: StreamState;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  if (state.phase === "retrieving") {
    return (
      <p
        role="status"
        className="border-line-strong text-fg-muted flex items-center gap-2 border-l-2 pl-4 text-base"
      >
        <Spinner />
        Searching your documents…
      </p>
    );
  }

  if (state.phase === "streaming") {
    return (
      <article
        aria-label="Answer being written"
        aria-busy="true"
        className="border-line-strong border-l-2 pl-4"
      >
        <p className="text-fg-muted mb-1 text-xs font-medium tracking-wide uppercase">ORBIT</p>
        <p className="text-fg-muted text-sm">
          {state.sources > 0
            ? `Answering from ${pluralize(state.retrieved, "passage")} across ${pluralize(state.sources, "source")}`
            : "Writing an answer"}
          {state.degraded ? " · keyword matching only" : ""}
        </p>
        <p className="reading mt-2 whitespace-pre-wrap">
          <ProvisionalText text={state.text} />
          <span
            className="bg-fg ml-0.5 inline-block h-4 w-px animate-pulse align-text-bottom"
            aria-hidden="true"
          />
        </p>
        <p className="text-fg-subtle mt-3 text-xs">Sources appear when the answer is complete.</p>
      </article>
    );
  }

  if (state.phase === "failed") {
    const { error } = state;
    const asApiError = new ApiError({
      code: error.code,
      message: error.message,
      status: error.retryable ? 503 : 400,
      requestId: error.request_id,
      retryAfterSeconds: error.retry_after_seconds ?? null,
    });

    return (
      <div className="border-danger border-l-2 pl-4">
        <ErrorState compact error={asApiError} title={describeError(asApiError).title} />
        <div className="flex gap-2">
          {error.retryable ? (
            <Button size="sm" variant="primary" onClick={onRetry}>
              Try again
            </Button>
          ) : null}
          <Button size="sm" onClick={onDismiss}>
            Dismiss
          </Button>
        </div>
      </div>
    );
  }

  return null;
}
