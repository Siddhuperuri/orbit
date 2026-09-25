"use client";

import { useSearchParams } from "next/navigation";

import { ErrorState } from "@/components/feedback/error-state";
import { Button } from "@/components/ui/button";
import { useConversation } from "@/features/chat/api/use-chat";
import { Thread } from "@/features/chat/components/thread";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { ErrorCode, isApiError } from "@/lib/api/errors";
import { useDocumentTitle } from "@/lib/hooks/use-document-title";
import { routes } from "@/lib/navigation";
import Link from "next/link";

/**
 * A conversation is private to the person who started it: anyone else -- including
 * other members of the workspace -- gets a 404, which is shown as "not found" and
 * nothing more.
 */
export function ConversationPage({ conversationId }: { conversationId: string }) {
  const { workspace } = useWorkspace();
  const conversation = useConversation(workspace.id, conversationId);
  const doc = useSearchParams().get("doc");
  useDocumentTitle(conversation.data?.title);

  if (isApiError(conversation.error) && conversation.error.code === ErrorCode.NotFound) {
    return (
      <div className="mx-auto max-w-md px-4 py-24 text-center">
        <h1 className="text-fg text-lg font-semibold">Conversation not found</h1>
        <p className="text-fg-muted mt-1.5 text-base">
          It may have been deleted. Conversations are private to the person who started them.
        </p>
        <Button asChild variant="primary" className="mt-5">
          <Link href={routes.chat(workspace.id)}>Start a new conversation</Link>
        </Button>
      </div>
    );
  }

  if (conversation.isError && !conversation.data) {
    return (
      <ErrorState
        error={conversation.error}
        title="Couldn't open this conversation"
        onRetry={() => void conversation.refetch()}
        retrying={conversation.isFetching}
      />
    );
  }

  return (
    <Thread
      conversationId={conversationId}
      title={conversation.data?.title ?? "Conversation"}
      initialScope={doc ? [doc] : []}
    />
  );
}
