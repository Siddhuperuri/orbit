import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";

import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils/cn";

/**
 * The pattern every primitive follows: variants declared with `cva`, `asChild`
 * for composition, and no styling decisions made at the call site.
 *
 * Square slabs with uppercase labels. The default control is 36px: compact enough
 * for a tool people keep open all day, large enough to hit without aiming. On a coarse pointer (a finger) every size
 * grows to the 44px minimum touch target via `pointer-coarse:`, so tablets and
 * phones get comfortable targets without a separate "mobile button".
 */
const buttonVariants = cva(
  cn(
    "relative isolate inline-flex shrink-0 items-center justify-center gap-2 overflow-hidden rounded-lg",
    // Labels are set like the navigation: mono, uppercase, crisp.
    "font-mono text-xs font-medium tracking-[0.06em] whitespace-nowrap uppercase select-none",
    "transition-[color,background-color,border-color,opacity,transform] duration-300 ease-out",
    "active:translate-y-px",
    "disabled:pointer-events-none disabled:opacity-40 aria-disabled:pointer-events-none aria-disabled:opacity-40",
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:transition-transform [&_svg]:duration-500",
    "pointer-coarse:min-h-11",
  ),
  {
    variants: {
      variant: {
        // A key: dark on paper, pale under the lamp, with a faint top highlight and one
        // orange signal dot in the corner.
        primary: cn(
          "bg-accent-solid text-on-accent shadow-button hover:bg-accent-solid-hover",
          "after:bg-signal after:absolute after:top-1.5 after:right-1.5 after:size-1 after:rounded-full",
        ),
        // An outline that fills with ink from below under the pointer -- the
        // inverted-block language of the navigation, as a gesture.
        secondary: cn(
          "border-line-strong text-fg bg-canvas border",
          "before:bg-fg before:absolute before:inset-0 before:-z-10 before:origin-bottom before:scale-y-0 before:transition-transform before:duration-500 before:ease-out",
          "hover:border-fg hover:text-canvas hover:before:scale-y-100",
          "data-[state=open]:border-fg data-[state=open]:text-canvas data-[state=open]:before:scale-y-100",
        ),
        subtle: "bg-fill text-fg hover:bg-line",
        ghost: "text-fg-muted hover:bg-fill hover:text-fg",
        // Destructive actions are visually distinct AND confirmed at the call
        // site; the colour alone is never the safeguard.
        danger: "bg-danger-solid text-on-danger hover:opacity-90",
        link: "link-draw text-accent rounded-xs normal-case tracking-normal",
      },
      size: {
        xs: "h-7 gap-1.5 px-2.5 text-2xs",
        sm: "h-8 gap-1.5 px-3 text-2xs",
        md: "h-9 px-4",
        lg: "h-11 px-6 text-sm",
        icon: "size-9 p-0 pointer-coarse:min-w-11",
        "icon-sm": "size-8 p-0 pointer-coarse:min-w-11",
      },
    },
    compoundVariants: [
      { variant: "link", className: "h-auto px-0 text-sm pointer-coarse:min-h-0" },
      // The signal dot belongs to a labelled key, not a square icon button.
      { variant: "primary", size: ["icon", "icon-sm"], className: "after:hidden" },
    ],
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
