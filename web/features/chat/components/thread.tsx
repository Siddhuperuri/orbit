"use client";

import { useEffect, useRef, useState } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";
import { useMessages } from "@/features/chat/api/use-chat";
import { ConversationsButton } from "@/features/chat/components/chat-layout";
import { Composer } from "@/features/chat/components/composer";
import { EmptyThread } from "@/features/chat/components/empty-thread";
import { Transcript, pendingQuestionOf } from "@/features/chat/components/transcript";
import { isBusy } from "@/features/chat/state/stream-state";
import { useConversationStream } from "@/features/chat/streaming/answer-streams-provider";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";

/** How close to the bottom counts as "following along". */
const STICK_THRESHOLD_PX = 96;

function ThreadSkeleton() {
  return (
    <LoadingRegion label="Loading conversation" className="space-y-8 px-4 py-6">
      {[0, 1].map((index) => (
        <div key={index} className="space-y-3">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-20 w-full" />
        </div>
      ))}
    </LoadingRegion>
  );
}

/**
 * One conversation: the recorded messages, the answer being written (if any), and
 * the composer.
 *
 * The transcript follows new text only while the reader is already near the
 * bottom. If they have scrolled up to re-read something, a new token must not yank
 * them back down -- the most common way a streaming chat becomes unusable.
 */
export function Thread({
  conversationId,
  title,
  initialScope,
}: {
  conversationId: string;
  title: string;
  initialScope: readonly string[];
}) {
  const { workspace, can } = useWorkspace();
  const messages = useMessages(workspace.id, conversationId);
  const { state, ask, stop, retry, dismiss } = useConversationStream(conversationId);
  const [scope, setScope] = useState<string[]>([...initialScope]);

  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(true);

  const streamedLength = state.phase === "streaming" ? state.text.length : 0;
  const count = messages.data?.length ?? 0;

  useEffect(() => {
    const element = scroller.current;
    if (element && following.current) element.scrollTop = element.scrollHeight;
  }, [count, streamedLength, state.phase]);

  const items = messages.data ?? [];
  const last = items[items.length - 1];

  const showEmpty =
    messages.isSuccess && items.length === 0 && pendingQuestionOf(items, state) === null;

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <header className="border-line bg-canvas flex h-14 shrink-0 items-center gap-2 border-b px-4 sm:px-6">
        <ConversationsButton />
        <h1 className="text-fg min-w-0 flex-1 truncate font-serif text-xl font-light tracking-tight md:mx-auto md:max-w-3xl">
          {title}
        </h1>
      </header>
      <div
        ref={scroller}
        onScroll={(event) => {
          const element = event.currentTarget;
          following.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < STICK_THRESHOLD_PX;
        }}
        className="bg-canvas min-h-0 flex-1 overflow-y-auto"
        aria-busy={isBusy(state) || undefined}
      >
        <div className="mx-auto max-w-3xl px-4 py-12 sm:px-6">
          {messages.isPending ? (
            <ThreadSkeleton />
          ) : messages.isError && !messages.data ? (
            <ErrorState
              error={messages.error}
              title="Couldn't load this conversation"
              onRetry={() => void messages.refetch()}
              retrying={messages.isFetching}
            />
          ) : (
            <Transcript
              workspaceId={workspace.id}
              messages={items}
              state={state}
              onRetry={() => void retry()}
              onDismiss={dismiss}
            />
          )}
          {showEmpty ? <EmptyThread /> : null}
          {last === undefined ? null : <div aria-hidden="true" className="h-2" />}
        </div>
      </div>

      {can("chat:use") ? (
        <Composer
          key={conversationId}
          busy={isBusy(state)}
          scope={scope}
          onScopeChange={setScope}
          onStop={stop}
          onSubmit={(question) => {
            following.current = true;
            void ask(question, scope);
          }}
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
