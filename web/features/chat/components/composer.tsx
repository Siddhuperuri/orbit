"use client";

import { ArrowUp, Square } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ScopePicker } from "@/features/chat/components/scope-picker";
import { MAX_QUESTION_CHARACTERS } from "@/features/chat/types";
import { cn } from "@/lib/utils/cn";

/**
 * The question box.
 *
 * Enter sends and Shift+Enter starts a new line -- except while an input method
 * (IME) is composing, when Enter confirms the candidate text and must not send.
 * While an answer is generating the send button becomes Stop: the API allows one
 * answer at a time per conversation, so a second question could only be refused.
 *
 * `docked` sits at the foot of a thread; `hero` is the centrepiece of a new
 * conversation, with no bar around it.
 */
export function Composer({
  onSubmit,
  onStop,
  busy,
  scope,
  onScopeChange,
  initialValue = "",
  autoFocus = false,
  placeholder = "Ask a question about your documents",
  variant = "docked",
}: {
  onSubmit: (question: string) => void;
  onStop: () => void;
  busy: boolean;
  /**
   * Which documents the answer may draw on. Omit both `scope` and `onScopeChange` where the
   * scope is fixed by context (a document's own page) and there is nothing to choose.
   */
  scope?: readonly string[];
  onScopeChange?: (ids: string[]) => void;
  initialValue?: string;
  autoFocus?: boolean;
  placeholder?: string;
  variant?: "docked" | "hero";
}) {
  const [value, setValue] = useState(initialValue);
  const hintId = useId();
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const trimmed = value.trim();
  const canSend = trimmed.length > 0 && !busy;
  const nearLimit = value.length > MAX_QUESTION_CHARACTERS * 0.85;

  function submit() {
    if (!canSend) return;
    onSubmit(trimmed);
    setValue("");
    inputRef.current?.focus();
  }

  return (
    <form
      // A question must never reach a URL (history, logs, referrers), even from a
      // submit that lands before React has hydrated -- which is what `method` guards.
      method="post"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className={cn(variant === "docked" ? "shrink-0 px-3 pt-2 pb-3 sm:px-6 sm:pb-5" : "w-full")}
    >
      <div className="mx-auto max-w-3xl">
        {/* The card is the visible control; it carries the focus indicator for the
            borderless textarea inside it. */}
        <div
          className={cn(
            "border-control bg-surface flex flex-col gap-2 rounded-2xl border p-3 transition-[border-color,box-shadow] duration-500",
            "hover:border-fg-subtle has-[textarea:focus-visible]:border-accent",
            "has-[textarea:focus-visible]:ring-focus/30 has-[textarea:focus-visible]:ring-3",
            variant === "hero" && "bg-surface/80 backdrop-blur-md",
          )}
        >
          <textarea
            ref={inputRef}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                submit();
              }
            }}
            rows={variant === "hero" ? 2 : 1}
            maxLength={MAX_QUESTION_CHARACTERS}
            autoFocus={autoFocus}
            placeholder={placeholder}
            aria-label="Your question"
            aria-describedby={hintId}
            enterKeyHint="send"
            // Grows with its content up to a ceiling, then scrolls. Where the
            // browser lacks `field-sizing` it stays at its row count and scrolls.
            className={cn(
              "text-fg placeholder:text-fg-subtle field-sizing-content max-h-48 min-h-10 w-full resize-none bg-transparent px-1.5 py-1 font-normal tracking-tight focus-visible:outline-none",
              variant === "hero" ? "text-xl" : "text-lg",
            )}
          />

          <div className="flex items-center justify-between gap-2">
            {scope && onScopeChange ? (
              <ScopePicker selected={scope} onChange={onScopeChange} disabled={busy} />
            ) : (
              <span />
            )}
            <div className="flex items-center gap-2">
              {nearLimit ? (
                <span className="text-fg-muted text-xs tabular-nums" aria-live="polite">
                  {value.length} / {MAX_QUESTION_CHARACTERS}
                </span>
              ) : null}
              {busy ? (
                <Button
                  variant="secondary"
                  size="icon-sm"
                  className="rounded-full"
                  onClick={onStop}
                  aria-label="Stop generating"
                >
                  <Square className="size-3.5 fill-current" aria-hidden="true" />
                </Button>
              ) : (
                <Button
                  type="submit"
                  variant="primary"
                  size="icon-sm"
                  className="rounded-full"
                  disabled={!canSend}
                  aria-label="Send question"
                >
                  <ArrowUp aria-hidden="true" />
                </Button>
              )}
            </div>
          </div>
        </div>
        <p id={hintId} className="label-micro text-fg-subtle mt-3 hidden text-center sm:block">
          Enter to send · Shift+Enter for a new line · Answers cite the passages they come from
        </p>
      </div>
    </form>
  );
}
