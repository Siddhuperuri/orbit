import { cn } from "@/lib/utils/cn";

/**
 * The workspace's initial on a tinted tile, so a workspace is recognisable at a
 * glance in the switcher. The tint is derived from the name -- stable, never
 * chosen -- and drawn only from theme tones, so it is readable in both themes.
 * Decorative: the workspace's name is always beside it as text.
 */
const TINTS = [
  "bg-accent-soft text-accent-soft-fg",
  "bg-success-soft text-success",
  "bg-warning-soft text-warning",
  "bg-danger-soft text-danger",
  "bg-fill text-fg-muted",
] as const;

function tintFor(name: string): string {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return TINTS[hash % TINTS.length]!;
}

export function WorkspaceAvatar({
  name,
  className,
}: {
  name: string | undefined;
  className?: string;
}) {
  const initial = name?.trim().charAt(0).toUpperCase() || "·";
  return (
    <span
      aria-hidden="true"
      className={cn(
        "text-md inline-flex size-6 shrink-0 items-center justify-center font-medium",
        name ? tintFor(name) : "bg-fill text-fg-subtle",
        className,
      )}
    >
      {initial}
    </span>
  );
}
