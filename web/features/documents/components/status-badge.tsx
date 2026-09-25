import { AlertTriangle, CheckCircle2, Clock, type LucideIcon } from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import { LIFECYCLE_LABELS, pipelineStep, type Lifecycle } from "@/features/documents/lib/lifecycle";
import type { PipelineStage, ProcessingStatus } from "@/features/documents/types";

const TONES: Record<Lifecycle, NonNullable<BadgeProps["tone"]>> = {
  uploading: "accent",
  saving: "accent",
  queued: "neutral",
  processing: "accent",
  indexing: "accent",
  ready: "success",
  failed: "danger",
};

/** States that are work in progress show a spinner; the rest show a still icon. */
const ICONS: Partial<Record<Lifecycle, LucideIcon>> = {
  queued: Clock,
  ready: CheckCircle2,
  failed: AlertTriangle,
};

/**
 * A lifecycle state as an icon, a word, and a tone -- never a coloured dot alone. The
 * design brief requires status to survive colour blindness and a monochrome display,
 * and the word is what a screen reader reads.
 */
export function LifecycleBadge({ state }: { state: Lifecycle }) {
  const Icon = ICONS[state];
  return (
    <Badge tone={TONES[state]}>
      {Icon ? <Icon aria-hidden="true" /> : <Spinner className="size-3" />}
      {LIFECYCLE_LABELS[state]}
    </Badge>
  );
}

/** A document's state, from its version's status and (while processing) the stage the worker reported. */
export function StatusBadge({
  status,
  stage = null,
}: {
  status: ProcessingStatus;
  stage?: PipelineStage | null;
}) {
  return <LifecycleBadge state={pipelineStep({ status, processing_stage: stage })} />;
}
