import { describe, expect, it } from "vitest";

import { pollDelay } from "@/lib/utils/polling";

describe("pollDelay", () => {
  it("starts quick and grows", () => {
    expect(pollDelay(0)).toBe(1_500);
    expect(pollDelay(1)).toBeGreaterThan(pollDelay(0));
  });
  it("is capped so a stuck job costs a bounded request rate", () => {
    expect(pollDelay(50)).toBe(15_000);
  });
  it("tolerates a negative attempt", () => {
    expect(pollDelay(-3)).toBe(1_500);
  });
});
