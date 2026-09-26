"use client";

import { ArrowUpRight } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { OrbitArt } from "@/components/layout/orbit-art";
import { LogoMark } from "@/components/layout/wordmark";
import { KineticText } from "@/components/ui/kinetic-text";
import { ConversationsButton } from "@/features/chat/components/chat-layout";
import { Composer } from "@/features/chat/components/composer";
import { useCreateConversation } from "@/features/chat/api/use-chat";
import { clearPrefilledQuestion, peekPrefilledQuestion } from "@/features/chat/lib/prefill";
import { titleFromQuestion } from "@/features/chat/lib/title";
import { useAnswerStreams } from "@/features/chat/streaming/answer-streams-provider";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";

/**
 * Starting points for someone facing an empty box. Deliberately about *their*
 * documents in general -- ORBIT does not know what is in them yet -- and each is a
 * kind of question grounded answering is good at.
 */
const STARTERS = [
  "Summarise the main ideas across my documents",
  "What are the key definitions I should know?",
  "What do my documents say about deadlines or dates?",
  "Which topics come up in more than one document?",
];

/**
 * The start of a new conversation: the question box, and a few ways in.
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
  // Read once for the field's starting text (see `prefill.ts`). A starter replaces
  // it; the composer is re-keyed so it takes the new text as its own.
  const [draft, setDraft] = useState(() => ({ text: peekPrefilledQuestion(), key: 0 }));

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
      <div className="@container relative flex min-h-0 flex-1 flex-col items-center overflow-x-clip overflow-y-auto px-4 py-10 sm:px-6 lg:px-10">
        {/* The artwork turns slowly in the corner behind the question, and shifts with the
            pointer; set off-centre so its rings frame the page rather than cross the text. */}
        <OrbitArt
          body={false}
          className="absolute -top-[34%] -right-[44%] size-[min(64rem,100cqi)] opacity-60"
        />
        <div className="relative my-auto w-full max-w-3xl">
          <div className="mb-10 text-center">
            <LogoMark className="enter text-fg mx-auto mb-8 size-8" />
            <h1 className="text-fg font-serif text-[clamp(2.5rem,1rem+5cqi,5.25rem)] leading-[0.95] font-light tracking-[-0.035em] text-balance">
              <KineticText text="What would you like to know?" />
            </h1>
            <p className="enter enter-2 text-fg-muted mx-auto mt-6 max-w-lg text-base">
              Answers come only from what&apos;s in {workspace.name}, and every claim links to the
              passage it came from. If your documents don&apos;t cover a question, you&apos;ll be
              told rather than given a guess.
            </p>
          </div>

          {can("chat:use") ? (
            <>
              <Composer
                key={draft.key}
                variant="hero"
                busy={create.isPending}
                scope={scope}
                onScopeChange={setScope}
                onStop={() => undefined}
                onSubmit={(question) => void start(question)}
                initialValue={draft.text}
                autoFocus
              />
              <ul
                aria-label="Suggested questions"
                className="enter enter-4 border-line bg-canvas mt-8 grid border-t border-l sm:grid-cols-2"
              >
                {STARTERS.map((starter) => (
                  <li key={starter} className="border-line border-r border-b">
                    <button
                      type="button"
                      onClick={() =>
                        setDraft((current) => ({ text: starter, key: current.key + 1 }))
                      }
                      className={cn(
                        "text-fg-muted hover:text-canvas group relative isolate flex h-full w-full items-start justify-between gap-4 overflow-hidden px-5 py-4 text-left text-sm transition-colors duration-500 pointer-coarse:min-h-11",
                        // Ink rises through the cell under the pointer.
                        "before:bg-fg before:absolute before:inset-0 before:-z-10 before:origin-bottom before:scale-y-0 before:transition-transform before:duration-500 before:ease-out hover:before:scale-y-100",
                      )}
                    >
                      <span>{starter}</span>
                      <ArrowUpRight
                        className="text-fg-subtle group-hover:text-canvas mt-0.5 size-4 shrink-0 transition-[color,transform] duration-500 ease-out group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
                        strokeWidth={1.5}
                        aria-hidden="true"
                      />
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="border-line text-fg-muted bg-canvas border px-4 py-3 text-center text-sm">
              Your role in this workspace can&apos;t ask questions.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
