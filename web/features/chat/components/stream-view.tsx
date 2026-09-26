"use client";

import { Fragment } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { AnswerByline, QuestionBubble } from "@/features/chat/components/message-item";
import type { StreamState } from "@/features/chat/state/stream-state";
import { describeError } from "@/lib/api/describe-error";
import { ApiError } from "@/lib/api/errors";
import { pluralize } from "@/lib/utils/format";

/** The question being answered, shown the moment it is sent. */
export function PendingQuestion({ question }: { question: string }) {
  return <QuestionBubble text={question} />;
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
      <div role="status">
        <AnswerByline>
          <span className="text-fg-subtle inline-flex items-center gap-2">
            <Spinner className="size-3.5" />
            Searching your documents…
          </span>
        </AnswerByline>
        <div className="space-y-2 pt-1" aria-hidden="true">
          <div className="skeleton-sweep animate-skeleton h-4 w-11/12" />
          <div className="skeleton-sweep animate-skeleton h-4 w-9/12" />
        </div>
      </div>
    );
  }

  if (state.phase === "streaming") {
    return (
      <article aria-label="Answer being written" aria-busy="true">
        <AnswerByline>
          <span className="text-fg-subtle">
            ·{" "}
            {state.sources > 0
              ? `Answering from ${pluralize(state.retrieved, "passage")} across ${pluralize(state.sources, "source")}`
              : "Writing an answer"}
            {state.degraded ? " · keyword matching only" : ""}
          </span>
        </AnswerByline>
        <p className="reading whitespace-pre-wrap">
          <ProvisionalText text={state.text} />
          <span
            className="bg-accent animate-caret ml-0.5 inline-block h-4 w-0.5 rounded-full align-text-bottom"
            aria-hidden="true"
          />
        </p>
        <p className="label-micro text-fg-subtle mt-4">
          Sources appear when the answer is complete.
        </p>
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
      <div className="border-danger/50 border px-4 py-1">
        <ErrorState compact error={asApiError} title={describeError(asApiError).title} />
        <div className="flex gap-2 pb-3">
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
