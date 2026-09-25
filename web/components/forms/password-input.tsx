"use client";

import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import type * as React from "react";

import { Input } from "@/components/ui/input";

/**
 * A password field with a show/hide toggle. The toggle is a real button with
 * `aria-pressed`, reachable by keyboard; the field keeps the browser's
 * `autocomplete` hints so password managers work (`current-password` to sign in,
 * `new-password` to choose one -- which is also what triggers "suggest a strong
 * password").
 */
export function PasswordInput({
  autoComplete,
  ...props
}: Omit<React.ComponentProps<typeof Input>, "type"> & {
  autoComplete: "current-password" | "new-password";
}) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="relative">
      <Input
        {...props}
        type={visible ? "text" : "password"}
        autoComplete={autoComplete}
        autoCapitalize="off"
        spellCheck={false}
        className="pr-10"
      />
      <button
        type="button"
        onClick={() => setVisible((current) => !current)}
        aria-pressed={visible}
        aria-label={visible ? "Hide password" : "Show password"}
        className="text-fg-muted hover:text-fg absolute inset-y-0 right-0 inline-flex w-9 items-center justify-center rounded-r-md pointer-coarse:w-11"
      >
        {visible ? (
          <EyeOff className="size-4" aria-hidden="true" />
        ) : (
          <Eye className="size-4" aria-hidden="true" />
        )}
      </button>
    </div>
  );
}
