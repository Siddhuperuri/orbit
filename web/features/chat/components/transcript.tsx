"use client";

import { MessageItem } from "@/features/chat/components/message-item";
import { PendingQuestion, StreamView } from "@/features/chat/components/stream-view";
import type { StreamState } from "@/features/chat/state/stream-state";
import type { Message } from "@/features/chat/types";

/**
 * The question currently being answered, if it is not yet in the recorded thread.
 *
 * The question is shown from the stream state while it is in flight. Once the server has
 * recorded it, the recorded copy takes over, so it is never shown twice -- and "recorded"
 * only counts for the *latest* turn, so asking the same thing twice does not hide the
 * second ask behind the first.
 */
export function pendingQuestionOf(items: readonly Message[], state: StreamState): string | null {
  const inFlight =
    state.phase === "retrieving" || state.phase === "streaming" || state.phase === "failed"
      ? state.question
      : null;
  if (inFlight === null) return null;

  const alreadyRecorded = items.some((message, index) => {
    if (message.role !== "user" || message.content !== inFlight) return false;
    return index >= items.length - 2;
  });
  return alreadyRecorded ? null : inFlight;
}

/**
 * A conversation's turns as a list: the recorded messages, the question being answered,
 * and the answer as it is written (or the failure). Shared by the chat page and the
 * document's ask panel, so a question reads and streams the same wherever it is asked.
 */
export function Transcript({
  workspaceId,
  messages,
  state,
  onRetry,
  onDismiss,
}: {
  workspaceId: string;
  messages: readonly Message[];
  state: StreamState;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const pending = pendingQuestionOf(messages, state);

  return (
    <ol aria-label="Conversation" className="space-y-12">
      {messages.map((message) => (
        <li key={message.id}>
          <MessageItem message={message} workspaceId={workspaceId} />
        </li>
      ))}
      {pending ? (
        <li>
          <PendingQuestion question={pending} />
        </li>
      ) : null}
      {state.phase !== "idle" && state.phase !== "settled" ? (
        <li>
          <StreamView state={state} onRetry={onRetry} onDismiss={onDismiss} />
        </li>
      ) : null}
    </ol>
  );
}
