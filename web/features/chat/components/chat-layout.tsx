"use client";

import { MessagesSquare } from "lucide-react";
import { useParams } from "next/navigation";
import { createContext, useContext, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { ConversationList } from "@/features/chat/components/conversation-list";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";

const DrawerContext = createContext<(() => void) | null>(null);

/**
 * Opens the conversation list on screens too narrow to show it beside the thread.
 * Rendered inside each pane's own title row (rather than as a bar of its own), so
 * the list costs no extra vertical space on a phone. It renders nothing from `md`
 * up, where the list is always visible.
 */
export function ConversationsButton() {
  const open = useContext(DrawerContext);
  if (!open) return null;
  return (
    <Button size="sm" variant="ghost" className="-ml-2 shrink-0 md:hidden" onClick={open}>
      <MessagesSquare aria-hidden="true" />
      Conversations
    </Button>
  );
}

/**
 * Two panes: conversations on the left, the open thread on the right.
 *
 * Below `md` there is no room for both, so the list lives in a drawer opened from a
 * "Conversations" button -- the thread (or the new-question box) always gets the
 * full width, and the list is one tap away rather than a separate page to navigate
 * back to.
 */
export function ChatLayout({ children }: { children: ReactNode }) {
  const { workspace } = useWorkspace();
  const { conversationId } = useParams<{ conversationId?: string }>();
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <DrawerContext.Provider value={() => setDrawerOpen(true)}>
      <div className="flex h-full min-h-0">
        <aside
          className="border-line bg-canvas hidden w-72 shrink-0 border-r md:block"
          aria-label="Conversations"
        >
          <ConversationList workspaceId={workspace.id} activeId={conversationId} />
        </aside>

        <section className="flex min-w-0 flex-1 flex-col" aria-label="Chat">
          <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        </section>

        <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
          <SheetContent aria-describedby={undefined} className="bg-canvas">
            <SheetTitle className="sr-only">Conversations</SheetTitle>
            <SheetDescription className="sr-only">
              Your conversations in this workspace
            </SheetDescription>
            <ConversationList
              workspaceId={workspace.id}
              activeId={conversationId}
              onNavigate={() => setDrawerOpen(false)}
            />
          </SheetContent>
        </Sheet>
      </div>
    </DrawerContext.Provider>
  );
}
