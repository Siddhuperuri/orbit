"use client";

import { MessageSquare } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { useCreateConversation, useMessages } from "@/features/chat/api/use-chat";
import { Composer } from "@/features/chat/components/composer";
import { Transcript } from "@/features/chat/components/transcript";
import { titleFromQuestion } from "@/features/chat/lib/title";
import { isBusy } from "@/features/chat/state/stream-state";
import {
  useAnswerStreams,
  useConversationStream,
} from "@/features/chat/streaming/answer-streams-provider";
import { statusOf } from "@/features/documents/status";
import type { Document } from "@/features/documents/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { routes } from "@/lib/navigation";

/** Why this document cannot answer a question right now, or `null` if it can. */
function unavailableReason(document: Document, canChat: boolean): string | null {
  if (!canChat) return "Your role in this workspace can't ask questions.";
  if (document.archived_at !== null) {
    return "This document is archived, so it's left out of answers. Restore it to ask about it.";
  }
  switch (statusOf(document)) {
    case "ready":
      return null;
    case "failed":
      return "Processing failed, so this document can't answer questions yet.";
    default:
      return "You'll be able to ask questions once ORBIT has finished processing this document.";
  }
}

/**
 * Ask a question that is answered from *this document only*.
 *
 * The scope is fixed by the page, so there is no picker: the answer draws on this document
 * and nowhere else, and its citations link back to the passages of the text below. It is
 * the same conversation machinery as the Chat page -- the question starts a real
 * conversation (titled by the question, listed under Chat, and reachable from the link
 * here), streamed by the workspace-level stream manager, so navigating away does not lose
 * an answer in progress.
 *
 * Not offered when the document cannot answer -- archived, still processing, failed, or a
 * role without `chat:use` -- and the reason is said, rather than showing a box that would
 * only refuse.
 */
export function DocumentAskPanel({ document }: { document: Document }) {
  const { workspace, can } = useWorkspace();
  const streams = useAnswerStreams();
  const create = useCreateConversation(workspace.id);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const { state, stop, retry, dismiss } = useConversationStream(conversationId ?? "");
  const messages = useMessages(workspace.id, conversationId ?? "", conversationId !== null);

  const scroller = useRef<HTMLDivElement>(null);
  const streamed = state.phase === "streaming" ? state.text.length : 0;
  const count = messages.data?.length ?? 0;
  useEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [count, streamed, state.phase]);

  const reason = unavailableReason(document, can("chat:use"));

  async function ask(question: string) {
    let id = conversationId;
    if (id === null) {
      const conversation = await create.mutateAsync(titleFromQuestion(question));
      id = conversation.id;
      setConversationId(id);
    }
    // Not awaited: it runs for as long as the answer takes.
    void streams.ask(id, question, [document.id]);
  }

  const started = conversationId !== null;

  return (
    <section
      aria-labelledby="ask-heading"
      className="scroll-reveal border-line bg-canvas overflow-hidden border"
    >
      <div className="px-5 pt-5">
        <h2
          id="ask-heading"
          className="text-fg flex items-center gap-2.5 font-serif text-xl font-light tracking-tight"
        >
          <MessageSquare className="text-accent size-4" aria-hidden="true" />
          Ask about this document
        </h2>
        <p className="text-fg-muted mt-1 text-sm">
          Answers come only from this document, and cite the passages they use.
        </p>
      </div>

      {reason ? (
        <p role="status" className="text-fg-muted px-4 py-4 text-base">
          {reason}
        </p>
      ) : (
        <>
          {started ? (
            <div ref={scroller} className="max-h-[28rem] overflow-y-auto px-4 py-4">
              {messages.isPending ? (
                <p role="status" className="text-fg-muted text-sm">
                  Loading…
                </p>
              ) : (
                <Transcript
                  workspaceId={workspace.id}
                  messages={messages.data ?? []}
                  state={state}
                  onRetry={() => void retry()}
                  onDismiss={dismiss}
                />
              )}
            </div>
          ) : null}

          <Composer
            busy={isBusy(state) || create.isPending}
            onStop={stop}
            onSubmit={(question) => void ask(question)}
            placeholder="Ask a question about this document"
          />

          {started && conversationId ? (
            <p className="border-line border-t px-4 py-2.5 text-sm">
              <Link
                href={routes.conversation(workspace.id, conversationId)}
                className="text-accent rounded-xs font-medium hover:underline"
              >
                Continue in chat
              </Link>
              <span className="text-fg-muted"> · this conversation is saved under Chat.</span>
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
