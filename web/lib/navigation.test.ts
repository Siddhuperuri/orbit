import { describe, expect, it } from "vitest";

import { loginPath, routes, safeNextPath } from "@/lib/navigation";

describe("safeNextPath", () => {
  it("follows an ordinary in-app path, keeping its query and hash", () => {
    expect(safeNextPath("/workspaces/abc/documents?status=ready#top")).toBe(
      "/workspaces/abc/documents?status=ready#top",
    );
  });

  it.each([
    ["absolute URL", "https://evil.example/phish"],
    ["protocol-relative", "//evil.example"],
    ["backslash trick", "/\\evil.example"],
    ["scheme without slashes", "javascript:alert(1)"],
    ["tab-smuggled host", "/\t/evil.example"],
    ["newline-smuggled host", "/\n/evil.example"],
    ["no leading slash", "workspaces/abc"],
    ["auth loop", "/auth/login"],
    ["api path", "/api/v1/auth/me"],
    ["empty", ""],
  ])("refuses %s", (_label, input) => {
    expect(safeNextPath(input)).toBe("/");
  });

  it("does not treat percent-encoded slashes as a host boundary: the result stays on this origin", () => {
    // `%2F` is never decoded into a separator by the URL parser, so this is just an
    // odd path on our own site (a 404), not a redirect anywhere.
    const result = safeNextPath("/%2F%2Fevil.example");
    expect(result.startsWith("//")).toBe(false);
    expect(new URL(result, "http://orbit.invalid").origin).toBe("http://orbit.invalid");
  });

  it("refuses paths that only become protocol-relative after normalisation", () => {
    // Each resolves to the pathname `//evil.example`, which a router would follow off-site.
    for (const input of ["/.//evil.example", "/..//evil.example", "/a/..//evil.example"]) {
      expect(safeNextPath(input)).toBe("/");
    }
  });

  it("returns the fallback for null and undefined", () => {
    expect(safeNextPath(null)).toBe("/");
    expect(safeNextPath(undefined, "/x")).toBe("/x");
  });

  it("never returns an off-origin value for any of a large set of hostile inputs", () => {
    const attempts = ["///x", "/\\/x", "/.//x", "/..//x", "\\\\x", "/\r/x", "/ /x", "/%09/x"];
    for (const attempt of attempts) {
      const result = safeNextPath(attempt);
      expect(result.startsWith("/")).toBe(true);
      expect(result.startsWith("//")).toBe(false);
    }
  });
});

describe("loginPath", () => {
  it("carries a safe destination and the reason", () => {
    expect(loginPath({ next: "/workspaces/w/chat", reason: "expired" })).toBe(
      "/auth/login?next=%2Fworkspaces%2Fw%2Fchat&reason=expired",
    );
  });

  it("drops an unsafe destination rather than passing it on", () => {
    expect(loginPath({ next: "//evil.example" })).toBe("/auth/login");
  });

  it("omits next when it would just be home", () => {
    expect(loginPath({ next: "/" })).toBe("/auth/login");
  });
});

describe("routes", () => {
  it("builds search and chat URLs with optional parameters", () => {
    expect(routes.search("w1")).toBe("/workspaces/w1/search");
    expect(routes.search("w1", { q: "a b", mode: "lexical" })).toBe(
      "/workspaces/w1/search?q=a+b&mode=lexical",
    );
    expect(routes.chat("w1", { doc: "d1" })).toBe("/workspaces/w1/chat?doc=d1");
  });
});
