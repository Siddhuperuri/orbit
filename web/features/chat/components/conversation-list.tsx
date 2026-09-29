"use client";

import { MessagesSquare, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";

import { ConfirmDialog } from "@/components/feedback/confirm-dialog";
import { EmptyState } from "@/components/feedback/empty-state";
import { QueryBoundary } from "@/components/feedback/query-boundary";
import { notify } from "@/components/feedback/notify";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";
import { useConversations, useDeleteConversation } from "@/features/chat/api/use-chat";
import type { Conversation } from "@/features/chat/types";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";
import { formatRelativeTime } from "@/lib/utils/format";

function ListSkeleton() {
  return (
    <LoadingRegion label="Loading conversations" className="space-y-1 p-2">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="space-y-1.5 px-2 py-2.5">
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-3 w-1/3" />
        </div>
      ))}
    </LoadingRegion>
  );
}

function ConversationRow({
  conversation,
  workspaceId,
  active,
  onNavigate,
  onDelete,
}: {
  conversation: Conversation;
  workspaceId: string;
  active: boolean;
  onNavigate?: () => void;
  onDelete: (conversation: Conversation, trigger: HTMLElement | null) => void;
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  return (
    <li className="group relative">
      <Link
        href={routes.conversation(workspaceId, conversation.id)}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        className={cn(
          "relative block py-3 pr-10 pl-5 transition-colors duration-300 pointer-coarse:min-h-12",
          active
            ? "bg-fill text-fg before:bg-accent before:absolute before:inset-y-0 before:left-0 before:w-0.5"
            : "text-fg-muted hover:bg-fill hover:text-fg",
        )}
      >
        <span className="block truncate text-base font-medium">{conversation.title}</span>
        <span className="label-micro text-fg-subtle mt-0.5 block">
          {formatRelativeTime(conversation.updated_at)}
        </span>
      </Link>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            ref={triggerRef}
            type="button"
            aria-label={`Actions for ${conversation.title}`}
            className="text-fg-muted hover:bg-line absolute top-3 right-2 inline-flex size-7 items-center justify-center opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100 pointer-coarse:size-10 pointer-coarse:opacity-100"
          >
            <MoreHorizontal className="size-4" aria-hidden="true" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem destructive onSelect={() => onDelete(conversation, triggerRef.current)}>
            <Trash2 aria-hidden="true" />
            Delete…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}

/** The user's own conversations in this workspace (private to them; other members never see them). */
export function ConversationList({
  workspaceId,
  activeId,
  onNavigate,
}: {
  workspaceId: string;
  activeId: string | undefined;
  onNavigate?: () => void;
}) {
  const router = useRouter();
  const query = useConversations(workspaceId);
  const remove = useDeleteConversation(workspaceId);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const conversations = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-line flex h-14 shrink-0 items-center justify-between gap-2 border-b pr-3 pl-5">
        <h2 className="label-micro text-fg">Conversations</h2>
        <Button asChild size="sm" variant="secondary">
          <Link href={routes.chat(workspaceId)} onClick={onNavigate}>
            <Plus aria-hidden="true" />
            New
          </Link>
        </Button>
      </div>

      <nav aria-label="Conversations" className="min-h-0 flex-1 overflow-y-auto pb-3">
        <QueryBoundary
          query={query}
          loading={<ListSkeleton />}
          errorTitle="Couldn't load conversations"
          isEmpty={() => conversations.length === 0}
          empty={
            <EmptyState
              className="py-10"
              icon={MessagesSquare}
              title="No conversations yet"
              description="Ask your first question and it will appear here."
            />
          }
        >
          {() => (
            <>
              <ul className="divide-line divide-y">
                {conversations.map((conversation) => (
                  <ConversationRow
                    key={conversation.id}
                    conversation={conversation}
                    workspaceId={workspaceId}
                    active={conversation.id === activeId}
                    onNavigate={onNavigate}
                    onDelete={(target, trigger) => {
                      returnFocus.current = trigger;
                      setDeleting(target);
                    }}
                  />
                ))}
              </ul>
              {query.hasNextPage ? (
                <div className="py-3 text-center">
                  <Button
                    size="sm"
                    loading={query.isFetchingNextPage}
                    onClick={() => void query.fetchNextPage()}
                  >
                    Load more
                  </Button>
                </div>
              ) : null}
            </>
          )}
        </QueryBoundary>
      </nav>

      <ConfirmDialog
        returnFocusRef={returnFocus}
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={`Delete “${deleting?.title ?? "conversation"}”?`}
        description="The whole conversation and its answers will be removed. This can't be undone."
        confirmLabel="Delete conversation"
        pendingLabel="Deleting"
        onConfirm={async () => {
          if (!deleting) return;
          await remove.mutateAsync(deleting.id);
          notify.success("Conversation deleted");
          if (deleting.id === activeId) router.replace(routes.chat(workspaceId));
        }}
      />
    </div>
  );
}
