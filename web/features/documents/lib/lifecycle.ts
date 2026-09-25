import type { DocumentVersion, PipelineStage } from "@/features/documents/types";

/**
 * What a document is doing, in the words a person uses -- derived from what the
 * backend actually reports, and never from elapsed time.
 *
 * The API gives two real signals: the version's coarse `status`
 * (pending / processing / ready / failed) and, while it is in the pipeline, the
 * `processing_stage` the worker last reported. "Indexing" is the `embed` and `index`
 * stages. There is **no percentage** anywhere in this, because the backend has none
 * to give: a progress bar drawn from stages would be a guess dressed up as a
 * measurement, so processing is shown as *which step*, never *how far*.
 *
 * `uploading` and `saving` are client-side: they exist only while the browser is
 * sending the file (see `UploadQueue`), before there is a document to ask about.
 */

export type PipelineStep = "queued" | "processing" | "indexing" | "ready" | "failed";
export type Lifecycle = "uploading" | "saving" | PipelineStep;

const INDEXING_STAGES: readonly PipelineStage[] = ["embed", "index"];

/** Where a version is. A document with no version yet is queued: it is about to be. */
export function pipelineStep(
  version: Pick<DocumentVersion, "status" | "processing_stage"> | null | undefined,
): PipelineStep {
  if (!version) return "queued";
  switch (version.status) {
    case "ready":
      return "ready";
    case "failed":
      return "failed";
    case "pending":
      return "queued";
    case "processing":
      return version.processing_stage && INDEXING_STAGES.includes(version.processing_stage)
        ? "indexing"
        : "processing";
  }
}

export const LIFECYCLE_LABELS: Record<Lifecycle, string> = {
  uploading: "Uploading",
  saving: "Saving",
  queued: "Queued",
  processing: "Processing",
  indexing: "Indexing",
  ready: "Ready",
  failed: "Failed",
};

export const LIFECYCLE_DESCRIPTIONS: Record<Lifecycle, string> = {
  uploading: "Sending the file to ORBIT.",
  saving: "The whole file has arrived and is being stored.",
  queued: "Stored, and waiting for a worker to pick it up.",
  processing: "Being read and split into passages.",
  indexing: "Being embedded and added to the search index.",
  ready: "Indexed. It can be searched and cited in answers.",
  failed: "Processing didn't complete. It isn't searchable.",
};

/** The finer-grained step names the worker reports, for detail beneath the headline state. */
export const STAGE_LABELS: Record<PipelineStage, string> = {
  claimed: "Picked up by a worker",
  fetch: "Fetching the file",
  parse: "Reading the text",
  normalize: "Cleaning up the text",
  chunk: "Splitting into passages",
  embed: "Computing embeddings",
  index: "Writing to the search index",
  done: "Finished",
};

/** The happy path, in order: the steps the stepper draws. `failed` is not a step; it interrupts one. */
export const PIPELINE_STEPS = ["queued", "processing", "indexing", "ready"] as const;

export function stepIndex(step: PipelineStep): number {
  const index = (PIPELINE_STEPS as readonly string[]).indexOf(step);
  // A failure has no position of its own; where it *happened* is not recorded on the
  // version, so callers that need it pass what they know.
  return index === -1 ? 0 : index;
}

/** Whether the pipeline can still change this on its own. Polling stops when this is false. */
export function isSettled(step: PipelineStep): boolean {
  return step === "ready" || step === "failed";
}
