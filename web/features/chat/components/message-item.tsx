"use client";

import { useState } from "react";

import { LogoMark } from "@/components/layout/wordmark";
import { GroundingNotice } from "@/features/chat/components/grounding-notice";
import { AnswerBody } from "@/features/chat/components/answer-body";
import { SourcesList } from "@/features/chat/components/sources-list";
import { citationsOf } from "@/features/chat/lib/message";
import type { Message } from "@/features/chat/types";
import { cn } from "@/lib/utils/cn";

/** A question, as the reader's own turn: set to the right, on a quiet fill. */
export function QuestionBubble({ text }: { text: string }) {
  return (
    <article aria-label="Your question" className="scroll-reveal flex justify-end">
      <p className="border-line-strong text-fg max-w-[85%] border-r-2 py-1 pr-5 text-right font-serif text-2xl leading-snug font-light tracking-tight [overflow-wrap:anywhere] whitespace-pre-wrap">
        {text}
      </p>
    </article>
  );
}

/** Who is speaking in an answer: the mark and the name, set small. */
export function AnswerByline({ children }: { children?: React.ReactNode }) {
  return (
    <div className="label-micro text-fg-muted mb-4 flex items-center gap-2.5">
      <span aria-hidden="true" className="text-fg inline-flex">
        <LogoMark className="size-5" />
      </span>
      <span className="text-fg">ORBIT</span>
      {children}
    </div>
  );
}

function UserMessage({ message }: { message: Message }) {
  return <QuestionBubble text={message.content} />;
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
    <article aria-label="Answer" className="scroll-reveal">
      <AnswerByline />
      <div
        className={cn(
          // A grounded answer stands on its own; one that is not is set off by a
          // dashed rule (with its notice and quieter text), so the two never look alike.
          !grounded && "border-line-strong border-l-2 border-dashed pl-4",
        )}
      >
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
      </div>

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
