import type { AnswerStreamEvent, AskBody } from "@/features/chat/api/stream";
import {
  initialStreamState,
  isBusy,
  streamReducer,
  type StreamAction,
  type StreamState,
} from "@/features/chat/state/stream-state";
import type { ErrorEvent, Message } from "@/features/chat/types";
import { isAbortError, isApiError } from "@/lib/api/errors";

/**
 * Runs streamed answers, one per conversation, outside React.
 *
 * Answering is slow, and a person will reasonably click over to Documents while it
 * generates. If the stream belonged to the thread component, that click would
 * cancel the answer; here it belongs to the workspace, so the thread simply
 * re-attaches and shows how far it got. It also means starting a stream is an
 * ordinary function call from an event handler, not something an effect must
 * orchestrate -- effects that start network work are where React's development
 * double-mount silently kills a request.
 */

export interface FinishedAnswer {
  conversationId: string;
  question: string;
  outcome:
    | { kind: "done"; message: Message }
    | { kind: "failed"; error: ErrorEvent }
    /** The user pressed Stop. The server keeps whatever was generated. */
    | { kind: "stopped" };
}

export interface AnswerStreamsOptions {
  stream: (
    conversationId: string,
    body: AskBody,
    signal: AbortSignal,
  ) => AsyncIterable<AnswerStreamEvent>;
  onFinished?: (finished: FinishedAnswer) => void;
}

/** A failure raised before any event arrived (a 409, a 422, retrieval failing) in the same shape a streamed `error` event has. */
export function toErrorEvent(error: unknown): ErrorEvent {
  if (isApiError(error)) {
    return {
      code: error.code,
      message: error.message,
      request_id: error.requestId,
      retryable: error.isRetryable,
      retry_after_seconds: error.retryAfterSeconds,
    };
  }
  return {
    code: "INTERNAL_ERROR",
    message: "The answer could not be completed.",
    request_id: "",
    retryable: false,
  };
}

export class AnswerStreams {
  private readonly states = new Map<string, StreamState>();
  private readonly controllers = new Map<string, AbortController>();
  private readonly listeners = new Set<() => void>();
  /** What each conversation last asked, so a failed answer can be retried. */
  private readonly lastAsk = new Map<
    string,
    { question: string; documentIds?: readonly string[] }
  >();
  private version = 0;

  constructor(private readonly options: AnswerStreamsOptions) {}

  // -- useSyncExternalStore contract --------------------------------------

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Bumps on every change; lets a consumer subscribe to "anything changed". */
  getVersion = (): number => this.version;

  getState(conversationId: string): StreamState {
    return this.states.get(conversationId) ?? initialStreamState;
  }

  // -- Commands -----------------------------------------------------------

  async ask(
    conversationId: string,
    question: string,
    documentIds?: readonly string[],
  ): Promise<void> {
    if (isBusy(this.getState(conversationId))) return;

    this.lastAsk.set(conversationId, { question, documentIds });
    this.dispatch(conversationId, { type: "submit", question });

    const controller = new AbortController();
    this.controllers.set(conversationId, controller);

    try {
      for await (const event of this.options.stream(
        conversationId,
        { question, documentIds },
        controller.signal,
      )) {
        this.apply(conversationId, question, event);
      }
    } catch (error) {
      if (isAbortError(error)) {
        this.dispatch(conversationId, { type: "reset" });
        this.options.onFinished?.({ conversationId, question, outcome: { kind: "stopped" } });
      } else {
        const failure = toErrorEvent(error);
        this.dispatch(conversationId, { type: "error", error: failure });
        this.options.onFinished?.({
          conversationId,
          question,
          outcome: { kind: "failed", error: failure },
        });
      }
    } finally {
      this.controllers.delete(conversationId);
    }
  }

  /** Re-asks the last question in a conversation, e.g. after a failure. */
  retry(conversationId: string): Promise<void> {
    const previous = this.lastAsk.get(conversationId);
    if (!previous) return Promise.resolve();
    this.reset(conversationId);
    return this.ask(conversationId, previous.question, previous.documentIds);
  }

  /** Stops generation. Closing the connection is what tells the server to stop. */
  stop(conversationId: string): void {
    this.controllers.get(conversationId)?.abort();
  }

  /** Clears a finished or failed state (dismissing an error). */
  reset(conversationId: string): void {
    if (isBusy(this.getState(conversationId))) return;
    this.dispatch(conversationId, { type: "reset" });
  }

  /** Aborts everything; called when the workspace is left. */
  dispose(): void {
    for (const controller of this.controllers.values()) controller.abort();
    this.controllers.clear();
  }

  // -- Internals ----------------------------------------------------------

  private apply(conversationId: string, question: string, event: AnswerStreamEvent): void {
    switch (event.type) {
      case "started":
        break;
      case "retrieval":
        this.dispatch(conversationId, {
          type: "retrieval",
          retrieved: event.data.retrieved,
          sources: event.data.sources,
          degraded: event.data.degraded,
        });
        break;
      case "delta":
        this.dispatch(conversationId, { type: "delta", text: event.data.text });
        break;
      case "done":
        this.dispatch(conversationId, { type: "done", message: event.data });
        this.options.onFinished?.({
          conversationId,
          question,
          outcome: { kind: "done", message: event.data },
        });
        break;
      case "error":
        this.dispatch(conversationId, { type: "error", error: event.data });
        this.options.onFinished?.({
          conversationId,
          question,
          outcome: { kind: "failed", error: event.data },
        });
        break;
    }
  }

  private dispatch(conversationId: string, action: StreamAction): void {
    const current = this.getState(conversationId);
    const next = streamReducer(current, action);
    if (next === current) return;
    if (next.phase === "idle") this.states.delete(conversationId);
    else this.states.set(conversationId, next);
    this.version += 1;
    for (const listener of this.listeners) listener();
  }
}
