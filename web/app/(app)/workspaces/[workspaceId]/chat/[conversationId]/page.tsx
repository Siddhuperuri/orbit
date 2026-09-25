import { Suspense } from "react";

import { ConversationPage } from "@/features/chat/components/conversation-page";

export default async function ConversationRoute({
  params,
}: {
  params: Promise<{ conversationId: string }>;
}) {
  const { conversationId } = await params;
  // `key` remounts the thread per conversation, so scroll position, scope, and
  // composer text never leak from one conversation into the next.
  return (
    <Suspense>
      <ConversationPage key={conversationId} conversationId={conversationId} />
    </Suspense>
  );
}
