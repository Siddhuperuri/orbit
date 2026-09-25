import {
  EVENT_SCHEMAS,
  isEventName,
  type ErrorEvent,
  type RetrievalEvent,
  type StartedEvent,
} from "@/features/chat/api/stream-events";
import type { Message } from "@/features/chat/types";
import { sendRequest } from "@/lib/api/client";
import { readSse } from "@/lib/api/sse";

/**
 * The streamed answer, as typed events.
 *
 * Order is fixed by the API: `started`, `retrieval`, any number of `delta`, then
 * exactly one of `done` or `error`. `delta` text is provisional -- the `done`
 * message is authoritative, its citations resolved and any handle that matched no
 * retrieved source removed (ADR-0006).
 *
 * Failures *before* generation (validation, a conversation that is not yours, an
 * answer already in flight, retrieval) are ordinary HTTP errors thrown from the
 * initial request, not events: the stream is only opened once evidence is in hand.
 */
export type AnswerStreamEvent =
  | { type: "started"; data: StartedEvent }
  | { type: "retrieval"; data: RetrievalEvent }
  | { type: "delta"; data: { text: string } }
  | { type: "done"; data: Message }
  | { type: "error"; data: ErrorEvent };

export interface AskBody {
  question: string;
  /** Answer only from these documents. Omit for the whole workspace. */
  documentIds?: readonly string[] | undefined;
}

export async function* streamAnswer(
  workspaceId: string,
  conversationId: string,
  body: AskBody,
  signal: AbortSignal,
): AsyncGenerator<AnswerStreamEvent, void, void> {
  const response = await sendRequest(
    "/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages/stream",
    {
      method: "post",
      path: { workspace_id: workspaceId, conversation_id: conversationId },
      json: {
        question: body.question,
        document_ids: body.documentIds && body.documentIds.length > 0 ? body.documentIds : null,
      },
      headers: { Accept: "text/event-stream" },
      signal,
    },
  );

  if (!response.body) throw new Error("The answer stream had no body.");
  const requestId = response.headers.get("X-Request-ID") ?? "";

  for await (const frame of readSse(response.body, signal)) {
    // Unknown event names are ignored: a newer server may add events an older
    // client should skip rather than choke on.
    if (!isEventName(frame.event)) continue;

    let payload: unknown;
    try {
      payload = JSON.parse(frame.data);
    } catch {
      payload = undefined;
    }

    const parsed = EVENT_SCHEMAS[frame.event].safeParse(payload);
    if (!parsed.success) {
      // A frame that does not match its schema means the stream is corrupt.
      // Surface it as the terminal error the protocol already defines.
      yield {
        type: "error",
        data: {
          code: "INTERNAL_ERROR",
          message: "The answer stream was interrupted.",
          request_id: requestId,
          retryable: true,
        },
      };
      return;
    }

    yield { type: frame.event, data: parsed.data } as AnswerStreamEvent;
  }
}
