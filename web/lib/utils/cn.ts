import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind classes with correct precedence.
 *
 * `clsx` resolves conditionals; `twMerge` then discards earlier classes that a
 * later one overrides. Without the merge step, `cn("p-2", "p-4")` would emit
 * both and the winner would depend on stylesheet order rather than on the call.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
