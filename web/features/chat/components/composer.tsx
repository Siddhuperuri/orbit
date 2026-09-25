"use client";

import { ArrowUp, Square } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ScopePicker } from "@/features/chat/components/scope-picker";
import { MAX_QUESTION_CHARACTERS } from "@/features/chat/types";
import { controlClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils/cn";

/**
 * The question box.
 *
 * Enter sends and Shift+Enter starts a new line -- except while an input method
 * (IME) is composing, when Enter confirms the candidate text and must not send.
 * While an answer is generating the send button becomes Stop: the API allows one
 * answer at a time per conversation, so a second question could only be refused.
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
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="border-line bg-canvas border-t px-3 pt-3 pb-3 sm:px-4"
    >
      <div className="mx-auto max-w-3xl">
        <div className={cn(controlClasses, "flex flex-col gap-1 p-2")}>
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
            rows={1}
            maxLength={MAX_QUESTION_CHARACTERS}
            autoFocus={autoFocus}
            placeholder={placeholder}
            aria-label="Your question"
            aria-describedby={hintId}
            enterKeyHint="send"
            // Grows with its content up to a ceiling, then scrolls. Where the
            // browser lacks `field-sizing` it stays one row and scrolls.
            className="text-md text-fg placeholder:text-fg-subtle field-sizing-content max-h-40 min-h-9 w-full resize-none bg-transparent px-1 py-1 focus:outline-none sm:text-base"
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
                  size="icon"
                  onClick={onStop}
                  aria-label="Stop generating"
                >
                  <Square className="fill-current" aria-hidden="true" />
                </Button>
              ) : (
                <Button
                  type="submit"
                  variant="primary"
                  size="icon"
                  disabled={!canSend}
                  aria-label="Send question"
                >
                  <ArrowUp aria-hidden="true" />
                </Button>
              )}
            </div>
          </div>
        </div>
        <p id={hintId} className="text-fg-subtle mt-1.5 hidden text-xs sm:block">
          Enter to send · Shift+Enter for a new line · Answers cite the passages they come from
        </p>
      </div>
    </form>
  );
}
