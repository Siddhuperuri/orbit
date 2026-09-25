import { ChatLayout } from "@/features/chat/components/chat-layout";

/**
 * Chat fills the space below the top bar and scrolls its own panes, so it opts out
 * of the page-level scroll the other screens use.
 */
export default function ChatSegmentLayout({ children }: { children: React.ReactNode }) {
  return <ChatLayout>{children}</ChatLayout>;
}
