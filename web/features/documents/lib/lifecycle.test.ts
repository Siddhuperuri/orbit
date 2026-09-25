import { describe, expect, it } from "vitest";

import {
  LIFECYCLE_DESCRIPTIONS,
  LIFECYCLE_LABELS,
  PIPELINE_STEPS,
  STAGE_LABELS,
  isSettled,
  pipelineStep,
  stepIndex,
} from "@/features/documents/lib/lifecycle";
import type { PipelineStage } from "@/features/documents/types";
import { makeVersion } from "@/test/factories";

describe("pipelineStep", () => {
  it("maps each coarse status to its step", () => {
    expect(pipelineStep(makeVersion({ status: "pending" }))).toBe("queued");
    expect(pipelineStep(makeVersion({ status: "ready" }))).toBe("ready");
    expect(pipelineStep(makeVersion({ status: "failed" }))).toBe("failed");
  });

  it("treats a document with no version yet as queued rather than guessing", () => {
    expect(pipelineStep(null)).toBe("queued");
    expect(pipelineStep(undefined)).toBe("queued");
  });

  it.each<[PipelineStage, string]>([
    ["claimed", "processing"],
    ["fetch", "processing"],
    ["parse", "processing"],
    ["normalize", "processing"],
    ["chunk", "processing"],
    ["embed", "indexing"],
    ["index", "indexing"],
    ["done", "processing"],
  ])("a processing version in stage %s is %s", (stage, expected) => {
    expect(pipelineStep(makeVersion({ status: "processing", processing_stage: stage }))).toBe(
      expected,
    );
  });

  it("is plain 'processing' when the worker has not reported a stage yet", () => {
    expect(pipelineStep(makeVersion({ status: "processing", processing_stage: null }))).toBe(
      "processing",
    );
  });

  it("ignores a stale stage once the version is no longer being processed", () => {
    // The API only sends a stage while in progress, but the mapping must not depend on it.
    expect(pipelineStep(makeVersion({ status: "ready", processing_stage: "embed" }))).toBe("ready");
    expect(pipelineStep(makeVersion({ status: "pending", processing_stage: "embed" }))).toBe(
      "queued",
    );
  });
});

describe("the lifecycle vocabulary", () => {
  it("has a label and a description for every state, including the client-side ones", () => {
    for (const state of [
      "uploading",
      "saving",
      "queued",
      "processing",
      "indexing",
      "ready",
      "failed",
    ] as const) {
      expect(LIFECYCLE_LABELS[state]).toBeTruthy();
      expect(LIFECYCLE_DESCRIPTIONS[state]).toBeTruthy();
    }
  });

  it("names every pipeline stage the backend can report", () => {
    const stages: PipelineStage[] = [
      "claimed",
      "fetch",
      "parse",
      "normalize",
      "chunk",
      "embed",
      "index",
      "done",
    ];
    for (const stage of stages) expect(STAGE_LABELS[stage]).toBeTruthy();
  });

  it("makes no promise about how far along processing is", () => {
    // There is no percentage in the contract; nothing here may imply one.
    const text = [...Object.values(LIFECYCLE_DESCRIPTIONS), ...Object.values(STAGE_LABELS)].join(
      " ",
    );
    expect(text).not.toMatch(/%|percent|\d+ of \d+/i);
  });

  it("draws the happy path in order and does not include failure as a step", () => {
    expect(PIPELINE_STEPS).toEqual(["queued", "processing", "indexing", "ready"]);
    expect(stepIndex("queued")).toBe(0);
    expect(stepIndex("indexing")).toBe(2);
    expect(stepIndex("ready")).toBe(3);
  });

  it("only ready and failed are settled", () => {
    expect(isSettled("ready")).toBe(true);
    expect(isSettled("failed")).toBe(true);
    expect(isSettled("queued")).toBe(false);
    expect(isSettled("processing")).toBe(false);
    expect(isSettled("indexing")).toBe(false);
  });
});
