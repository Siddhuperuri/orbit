"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { chatKeys } from "@/features/chat/api/keys";
import { streamAnswer } from "@/features/chat/api/stream";
import { AnswerStreams } from "@/features/chat/streaming/answer-streams";
import { initialStreamState, type StreamState } from "@/features/chat/state/stream-state";
import type { Message } from "@/features/chat/types";
import { announce } from "@/lib/a11y/announcer";

const StreamsContext = createContext<AnswerStreams | null>(null);

/**
 * Owns the workspace's answer streams for as long as the workspace is open, so
 * navigating between pages does not cancel an answer in progress.
 *
 * When an answer finishes, the finished message is written into the query cache --
 * the stream itself bypasses the cache while it runs (ADR-0016) -- and the thread is
 * then re-read from the server in the background to reconcile with what was
 * actually recorded.
 */
export function AnswerStreamsProvider({
  workspaceId,
  children,
}: {
  workspaceId: string;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();

  const [streams] = useState(
    () =>
      new AnswerStreams({
        stream: (conversationId, body, signal) =>
          streamAnswer(workspaceId, conversationId, body, signal),

        onFinished: ({ conversationId, question, outcome }) => {
          const messagesKey = chatKeys.messages(workspaceId, conversationId);

          if (outcome.kind === "done") {
            const answer = outcome.message;
            queryClient.setQueryData<Message[]>(messagesKey, (current) => {
              const existing = current ?? [];
              if (existing.some((message) => message.id === answer.id)) return existing;
              // The API returns the assistant's message only; the question it
              // answered is known here, one ordinal earlier. The refetch below
              // replaces this stand-in with the recorded message.
              const asked: Message = {
                id: `pending-${answer.id}`,
                conversation_id: conversationId,
                role: "user",
                ordinal: answer.ordinal - 1,
                content: question,
                status: "complete",
                created_at: answer.created_at,
                citations: [],
                discarded_citation_count: 0,
              };
              const hasQuestion = existing.some((message) => message.ordinal === asked.ordinal);
              return [...existing, ...(hasQuestion ? [] : [asked]), answer];
            });
            announce("Answer ready");
          } else if (outcome.kind === "failed") {
            announce("The answer could not be completed", "assertive");
          }

          void queryClient.invalidateQueries({ queryKey: messagesKey });
          void queryClient.invalidateQueries({ queryKey: chatKeys.lists(workspaceId) });
        },
      }),
  );

  // Leaving the workspace ends its streams; the server keeps what was generated.
  useEffect(() => () => streams.dispose(), [streams]);

  return <StreamsContext.Provider value={streams}>{children}</StreamsContext.Provider>;
}

/** The stream manager for the current workspace. */
export function useAnswerStreams(): AnswerStreams {
  const streams = useContext(StreamsContext);
  if (!streams) throw new Error("useAnswerStreams must be used inside <AnswerStreamsProvider>.");
  return streams;
}

/** One conversation's stream state and controls. */
export function useConversationStream(conversationId: string): {
  state: StreamState;
  ask: (question: string, documentIds?: readonly string[]) => Promise<void>;
  stop: () => void;
  retry: () => Promise<void>;
  dismiss: () => void;
} {
  const streams = useAnswerStreams();
  const state = useSyncExternalStore(
    streams.subscribe,
    () => streams.getState(conversationId),
    () => initialStreamState,
  );

  return useMemo(
    () => ({
      state,
      ask: (question, documentIds) => streams.ask(conversationId, question, documentIds),
      stop: () => streams.stop(conversationId),
      retry: () => streams.retry(conversationId),
      dismiss: () => streams.reset(conversationId),
    }),
    [state, streams, conversationId],
  );
}
