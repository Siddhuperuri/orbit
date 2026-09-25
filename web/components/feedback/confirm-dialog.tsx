"use client";

import { useState, type ReactNode } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

/**
 * Confirmation for a destructive action. It names the specific thing being
 * destroyed (the brief: "confirmation naming the specific resource"), and for
 * irreversible or high-blast-radius actions requires the resource's name to be
 * typed -- a checkbox is muscle memory, typing is not.
 *
 * A failed action keeps the dialog open and shows why, rather than closing on an
 * error the user then cannot see.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  pendingLabel,
  typedConfirmation,
  onConfirm,
  returnFocusRef,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  pendingLabel?: string;
  /** When set, the confirm button stays disabled until this exact text is typed. */
  typedConfirmation?: string;
  onConfirm: () => Promise<unknown>;
  /** The control to refocus on close when this was opened from a menu item. */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [typed, setTyped] = useState("");

  const confirmed = typedConfirmation === undefined || typed === typedConfirmation;

  function handleOpenChange(next: boolean) {
    // Closing mid-request would orphan the outcome; wait for it.
    if (pending) return;
    if (!next) {
      setError(null);
      setTyped("");
    }
    onOpenChange(next);
  }

  async function confirm() {
    setPending(true);
    setError(null);
    try {
      await onConfirm();
      setTyped("");
      onOpenChange(false);
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent returnFocusRef={returnFocusRef}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription asChild>
            <div>{description}</div>
          </DialogDescription>
        </DialogHeader>

        {typedConfirmation !== undefined ? (
          <Field
            label={
              <>
                Type <span className="font-mono">{typedConfirmation}</span> to confirm
              </>
            }
          >
            {(control) => (
              <Input
                {...control}
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
              />
            )}
          </Field>
        ) : null}

        {error ? <ErrorState compact error={error} /> : null}

        <DialogFooter>
          <Button variant="secondary" onClick={() => handleOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={() => void confirm()}
            loading={pending}
            disabled={!confirmed}
          >
            {pending ? (pendingLabel ?? "Working") : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
