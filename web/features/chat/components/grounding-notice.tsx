import { AlertTriangle, Info } from "lucide-react";

import { groundingNotice } from "@/features/chat/lib/citations";
import type { Grounding } from "@/features/chat/types";
import { cn } from "@/lib/utils/cn";

/**
 * Says so when an answer is not fully backed by the user's documents.
 *
 * ADR-0006 binds citations to retrieved chunks precisely so a reader can trust the
 * ones they see; the flip side is that an answer *without* them must not look the
 * same. This is that signal, stated in words and with an icon -- not by colour alone.
 */
export function GroundingNotice({ grounding }: { grounding: Grounding | null | undefined }) {
  const notice = groundingNotice(grounding);
  if (!notice) return null;
  const Icon = notice.tone === "warning" ? AlertTriangle : Info;

  return (
    <div
      className={cn(
        "mb-3 flex gap-2.5 rounded-md border px-3 py-2 text-sm",
        notice.tone === "warning"
          ? "border-warning/30 bg-warning-soft text-warning"
          : "border-line bg-sunken text-fg-muted",
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <p>
        <span className="font-medium">{notice.title}.</span> {notice.detail}
      </p>
    </div>
  );
}
