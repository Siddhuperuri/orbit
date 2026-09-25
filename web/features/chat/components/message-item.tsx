"use client";

import { useState } from "react";

import { GroundingNotice } from "@/features/chat/components/grounding-notice";
import { AnswerBody } from "@/features/chat/components/answer-body";
import { SourcesList } from "@/features/chat/components/sources-list";
import { citationsOf } from "@/features/chat/lib/message";
import type { Message } from "@/features/chat/types";
import { cn } from "@/lib/utils/cn";

function UserMessage({ message }: { message: Message }) {
  return (
    <article aria-label="Your question" className="pb-2">
      <p className="text-fg-muted mb-1 text-xs font-medium tracking-wide uppercase">You</p>
      <p className="text-md text-fg font-medium [overflow-wrap:anywhere] whitespace-pre-wrap">
        {message.content}
      </p>
    </article>
  );
}

function statusNotice(message: Message): string | null {
  if (message.status === "partial") return "This answer was cut short and may be incomplete.";
  if (message.status === "failed" && message.content.trim() === "")
    return "No answer was produced for this question.";
  if (message.status === "failed") return "Generating this answer failed part way through.";
  return null;
}

function AssistantMessage({ message, workspaceId }: { message: Message; workspaceId: string }) {
  const [openHandle, setOpenHandle] = useState<string | null>(null);
  const grounded = message.grounding === "grounded";
  const notice = statusNotice(message);
  const hasText = message.content.trim() !== "";

  function cite(handle: string) {
    setOpenHandle(handle);
    // The marker and its source are far apart in a long answer; bring the source to the reader.
    requestAnimationFrame(() => {
      document
        .getElementById(`source-${message.id}-${handle}`)
        ?.scrollIntoView({ block: "nearest" });
    });
  }

  return (
    <article
      aria-label="Answer"
      className={cn(
        "border-l-2 pl-4",
        // Solid rule for a grounded answer; dashed and lighter for one that is not.
        grounded ? "border-line-strong" : "border-line-strong border-dashed",
      )}
    >
      <p className="text-fg-muted mb-1 text-xs font-medium tracking-wide uppercase">ORBIT</p>
      <GroundingNotice grounding={message.grounding} />

      {hasText ? (
        <AnswerBody
          message={message}
          onCite={cite}
          muted={!grounded && message.status === "complete"}
        />
      ) : null}

      {notice ? (
        <p role="note" className="text-danger mt-2 text-sm">
          {notice}
        </p>
      ) : null}

      <SourcesList
        messageId={message.id}
        workspaceId={workspaceId}
        citations={citationsOf(message)}
        openHandle={openHandle}
        onToggle={(handle) => setOpenHandle((current) => (current === handle ? null : handle))}
      />
    </article>
  );
}

export function MessageItem({ message, workspaceId }: { message: Message; workspaceId: string }) {
  return message.role === "user" ? (
    <UserMessage message={message} />
  ) : (
    <AssistantMessage message={message} workspaceId={workspaceId} />
  );
}
