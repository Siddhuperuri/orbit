import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";

import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils/cn";

/**
 * The pattern every primitive follows: variants declared with `cva`, `asChild`
 * for composition, and no styling decisions made at the call site.
 *
 * Sizes are compact because ORBIT is a tool people keep open all day: the default
 * control is 32px. On a coarse pointer (a finger) every size grows to the 44px
 * minimum touch target via `pointer-coarse:`, so tablets and phones get
 * comfortable targets without a separate "mobile button".
 */
const buttonVariants = cva(
  cn(
    "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md",
    "text-base font-medium whitespace-nowrap select-none",
    // 100-200ms, and only on properties that signal state.
    "transition-colors duration-150",
    "disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
    "pointer-coarse:min-h-11",
  ),
  {
    variants: {
      variant: {
        primary: "bg-accent-solid text-on-accent hover:bg-accent-solid-hover",
        secondary: "border border-control bg-surface text-fg hover:bg-sunken",
        ghost: "text-fg-muted hover:bg-sunken hover:text-fg",
        // Destructive actions are visually distinct AND confirmed at the call
        // site; the colour alone is never the safeguard.
        danger: "bg-danger-solid text-on-danger hover:opacity-90",
        link: "rounded-xs text-accent underline-offset-4 hover:underline",
      },
      size: {
        sm: "h-7 px-2.5 text-sm",
        md: "h-8 px-3",
        lg: "h-10 px-4",
        icon: "size-8 p-0 pointer-coarse:min-w-11",
      },
    },
    compoundVariants: [{ variant: "link", className: "h-auto px-0 pointer-coarse:min-h-0" }],
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  /** Render the child element instead of a `<button>`, keeping the styles. */
  asChild?: boolean;
  ref?: React.Ref<HTMLButtonElement>;
  /**
   * Shows a spinner and blocks activation. Uses `aria-disabled` rather than
   * `disabled`, so the button keeps focus and does not vanish from a screen
   * reader's tab order mid-action.
   */
  loading?: boolean;
}

export function Button({
  className,
  variant,
  size,
  asChild = false,
  loading = false,
  type = "button",
  children,
  onClick,
  ...props
}: ButtonProps) {
  const classes = cn(buttonVariants({ variant, size }), className);

  if (asChild) {
    return (
      <Slot className={classes} {...props}>
        {children}
      </Slot>
    );
  }

  return (
    <button
      // Defaulting to `type="button"` prevents the classic bug where a button
      // inside a form submits it by accident.
      type={type}
      className={classes}
      aria-busy={loading || undefined}
      aria-disabled={loading || props.disabled || undefined}
      onClick={loading ? (event) => event.preventDefault() : onClick}
      {...props}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

export { buttonVariants };
