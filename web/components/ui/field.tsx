"use client";

import { AlertCircle } from "lucide-react";
import { useId, type ReactNode } from "react";

import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils/cn";

/**
 * The accessibility props a control needs to belong to a {@link Field}. Spread
 * them onto the input: they wire the label, the hint, and the error message to it
 * programmatically, which is what makes them readable by a screen reader rather
 * than merely adjacent on screen.
 */
export interface FieldControlProps {
  id: string;
  "aria-describedby": string | undefined;
  "aria-invalid": true | undefined;
}

export interface FieldProps {
  label: ReactNode;
  /** Text shown beneath the label, before the control -- format hints, limits. */
  description?: ReactNode;
  /** The validation message. Its presence marks the control invalid. */
  error?: string | undefined;
  /** Marks a field the user may leave empty; required is the default and is not decorated. */
  optional?: boolean;
  /** Visually hides the label while keeping it for assistive tech. */
  hideLabel?: boolean;
  className?: string;
  children: (control: FieldControlProps) => ReactNode;
}

/**
 * A labelled form field: label, optional hint, control, and error, all associated.
 *
 * Optional fields are labelled "(optional)" instead of marking required ones with
 * an asterisk: an asterisk is a symbol whose meaning must be explained, and most
 * forms here are all-required.
 */
export function Field({
  label,
  description,
  error,
  optional = false,
  hideLabel = false,
  className,
  children,
}: FieldProps) {
  const id = useId();
  const descriptionId = `${id}-description`;
  const errorId = `${id}-error`;
  const describedBy =
    [description ? descriptionId : null, error ? errorId : null].filter(Boolean).join(" ") ||
    undefined;

  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id} className={hideLabel ? "sr-only" : undefined}>
        {label}
        {optional ? <span className="text-fg-muted ml-1 font-normal">(optional)</span> : null}
      </Label>
      {description ? (
        <p id={descriptionId} className="text-fg-muted text-sm">
          {description}
        </p>
      ) : null}
      {children({
        id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
      })}
      {error ? (
        <p id={errorId} className="text-danger flex items-start gap-1.5 text-sm">
          <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}
