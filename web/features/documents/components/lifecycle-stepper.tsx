import { AlertTriangle, Check, Circle } from "lucide-react";

import { Spinner } from "@/components/ui/spinner";
import {
  LIFECYCLE_DESCRIPTIONS,
  LIFECYCLE_LABELS,
  PIPELINE_STEPS,
  STAGE_LABELS,
  pipelineStep,
  stepIndex,
  type PipelineStep,
} from "@/features/documents/lib/lifecycle";
import type { DocumentVersion } from "@/features/documents/types";
import { cn } from "@/lib/utils/cn";

type StepState = "done" | "current" | "upcoming";

function Marker({ state, active }: { state: StepState; active: boolean }) {
  if (state === "done") {
    return (
      <span className="bg-success text-on-accent flex size-5 items-center justify-center rounded-full">
        <Check className="size-3" strokeWidth={3} aria-hidden="true" />
      </span>
    );
  }
  if (state === "current") {
    return (
      <span className="border-accent text-accent flex size-5 items-center justify-center rounded-full border-2">
        {/* Work is happening only in the middle steps; "queued" is waiting, not working. */}
        {active ? (
          <Spinner className="size-3" />
        ) : (
          <Circle className="size-2 fill-current" aria-hidden="true" />
        )}
      </span>
    );
  }
  return <span className="border-line-strong size-5 rounded-full border-2" aria-hidden="true" />;
}

/**
 * Where a document is on its way to being searchable: which step, never how far.
 *
 * The backend reports a coarse status and, while processing, the stage the worker last
 * reported -- and nothing finer. So there is no percentage here and no progress bar,
 * because either would be invented. A step that is not finished is shown as "in
 * progress" (spinner) or "waiting" (a dot), and that is all the data supports.
 *
 * When processing failed, the steps are all muted and the last one is replaced by
 * "Failed": the version does not record *where* it stopped, so the stepper does not
 * claim to know.
 */
export function LifecycleStepper({
  version,
}: {
  version: Pick<DocumentVersion, "status" | "processing_stage"> | null;
}) {
  const step = pipelineStep(version);
  const failed = step === "failed";
  const currentIndex = stepIndex(step);
  const stage = version?.processing_stage ?? null;

  const steps: readonly (PipelineStep | "failed")[] = failed
    ? [...PIPELINE_STEPS.slice(0, -1), "failed"]
    : PIPELINE_STEPS;

  return (
    <div>
      <ol aria-label="Processing steps" className="flex flex-wrap items-center gap-x-2 gap-y-2">
        {steps.map((name, index) => {
          const isFailedMarker = name === "failed";
          const state: StepState = failed
            ? "upcoming"
            : index < currentIndex || step === "ready"
              ? "done"
              : index === currentIndex
                ? "current"
                : "upcoming";
          const active = state === "current" && (name === "processing" || name === "indexing");

          return (
            <li
              key={name}
              aria-current={state === "current" ? "step" : undefined}
              className="flex items-center gap-2"
            >
              {isFailedMarker ? (
                <span className="bg-danger text-on-danger flex size-5 items-center justify-center rounded-full">
                  <AlertTriangle className="size-3" aria-hidden="true" />
                </span>
              ) : (
                <Marker state={state} active={active} />
              )}
              <span
                className={cn(
                  "text-sm",
                  isFailedMarker
                    ? "text-danger font-medium"
                    : state === "upcoming"
                      ? "text-fg-subtle"
                      : "text-fg font-medium",
                )}
              >
                {LIFECYCLE_LABELS[name]}
                {/* After a failure the position is unknown, so no step is announced as done or not. */}
                <span className="sr-only">
                  {failed
                    ? ""
                    : state === "done"
                      ? " (done)"
                      : state === "current"
                        ? " (current step)"
                        : " (not reached yet)"}
                </span>
              </span>
              {index < steps.length - 1 ? (
                <span aria-hidden="true" className="bg-line-strong hidden h-px w-5 sm:block" />
              ) : null}
            </li>
          );
        })}
      </ol>

      <p className="text-fg-muted mt-3 text-base">
        {LIFECYCLE_DESCRIPTIONS[step]}
        {(step === "processing" || step === "indexing") && stage ? (
          <span className="text-fg-subtle"> Right now: {STAGE_LABELS[stage].toLowerCase()}.</span>
        ) : null}
      </p>
    </div>
  );
}
