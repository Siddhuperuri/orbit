"use client";

import { RouteError } from "@/components/feedback/route-error";

/**
 * Catches a render error inside the authenticated shell. Because it sits *below*
 * the shell's layout, the sidebar and top bar stay usable: someone can navigate
 * away from a broken page instead of being left with a blank screen.
 */
export default function AppError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <RouteError {...props} />;
}
