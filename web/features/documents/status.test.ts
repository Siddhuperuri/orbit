import { describe, expect, it } from "vitest";

import { isInProgress, isTerminalStatus, statusOf } from "@/features/documents/status";
import type { Document } from "@/features/documents/types";
import { makeDocument, makeDocumentIn } from "@/test/factories";

function documentWith(status: "pending" | "processing" | "ready" | "failed" | null): Document {
  return status ? makeDocumentIn(status) : makeDocument({ current_version: null });
}

describe("document status", () => {
  it("treats only ready and failed as terminal, so polling stops there", () => {
    expect(isTerminalStatus("ready")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
    expect(isTerminalStatus("pending")).toBe(false);
    expect(isTerminalStatus("processing")).toBe(false);
  });

  it("keeps polling a document until it reaches a terminal state", () => {
    expect(isInProgress(documentWith("pending"))).toBe(true);
    expect(isInProgress(documentWith("processing"))).toBe(true);
    expect(isInProgress(documentWith("ready"))).toBe(false);
    expect(isInProgress(documentWith("failed"))).toBe(false);
  });

  it("treats a document with no current version as queued rather than crashing", () => {
    expect(statusOf(documentWith(null))).toBe("pending");
  });
});
