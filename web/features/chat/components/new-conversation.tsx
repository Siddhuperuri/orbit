"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ConversationsButton } from "@/features/chat/components/chat-layout";
import { Composer } from "@/features/chat/components/composer";
import { useCreateConversation } from "@/features/chat/api/use-chat";
import { clearPrefilledQuestion, peekPrefilledQuestion } from "@/features/chat/lib/prefill";
import { titleFromQuestion } from "@/features/chat/lib/title";
import { useAnswerStreams } from "@/features/chat/streaming/answer-streams-provider";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { routes } from "@/lib/navigation";

/**
 * The start of a new conversation: the question box, and nothing else.
 *
 * Sending creates the conversation, starts the answer, and only then navigates to
 * it -- so the thread opens already showing the question being worked on, instead
 * of arriving empty and starting a request from an effect.
 */
export function NewConversation() {
  const { workspace, can } = useWorkspace();
  const router = useRouter();
  const streams = useAnswerStreams();
  const create = useCreateConversation(workspace.id);
  const docParam = useSearchParams().get("doc");
  const [scope, setScope] = useState<string[]>(docParam ? [docParam] : []);
  // Read once for the field's starting text (see `prefill.ts`).
  const [prefill] = useState(peekPrefilledQuestion);

  useEffect(() => clearPrefilledQuestion, []);

  async function start(question: string) {
    const conversation = await create.mutateAsync(titleFromQuestion(question));
    // Not awaited: it runs for as long as the answer takes, and the thread
    // subscribes to its progress after navigation.
    void streams.ask(conversation.id, question, scope);
    router.push(routes.conversation(workspace.id, conversation.id));
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div className="border-line flex items-center border-b px-4 py-2 md:hidden">
        <ConversationsButton />
      </div>
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-4 py-10">
        <div className="max-w-md text-center">
          <h1 className="text-fg text-xl font-semibold">Ask your documents</h1>
          <p className="text-fg-muted mt-2 text-base">
            Answers come only from what&apos;s in this workspace, and every claim links to the
            passage it came from. If your documents don&apos;t cover a question, you&apos;ll be told
            rather than given a guess.
          </p>
        </div>
      </div>

      {can("chat:use") ? (
        <Composer
          busy={create.isPending}
          scope={scope}
          onScopeChange={setScope}
          onStop={() => undefined}
          onSubmit={(question) => void start(question)}
          initialValue={prefill}
          autoFocus
        />
      ) : (
        <p className="border-line text-fg-muted border-t px-4 py-3 text-center text-sm">
          Your role in this workspace can&apos;t ask questions.
        </p>
      )}
    </div>
  );
}
