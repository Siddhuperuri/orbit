import type { Metadata } from "next";
import { Suspense } from "react";

import { NewConversation } from "@/features/chat/components/new-conversation";

export const metadata: Metadata = { title: "Chat" };

export default function ChatPage() {
  // `useSearchParams` (an optional `?doc=` scope) needs a Suspense boundary.
  return (
    <Suspense>
      <NewConversation />
    </Suspense>
  );
}
