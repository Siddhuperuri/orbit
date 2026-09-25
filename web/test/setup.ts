import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// A real URL behind `useRouter` / `useSearchParams`; see `test/navigation.ts`.
vi.mock("next/navigation", () => import("./navigation"));

// Testing Library only auto-registers cleanup when the runner exposes globals;
// this project keeps them off, so it is wired explicitly.
afterEach(() => {
  cleanup();
  // Spies on the API objects must not leak into the next test's expectations.
  vi.restoreAllMocks();
});

// jsdom has no `matchMedia`. Components that ask about the viewport get a desktop-width,
// motion-allowed answer unless a test says otherwise.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: /min-width/.test(query),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  });
}

// jsdom does not implement these either; Radix and scroll-into-view code call them.
if (typeof window !== "undefined") {
  window.HTMLElement.prototype.scrollIntoView ??= () => undefined;
  window.HTMLElement.prototype.hasPointerCapture ??= () => false;
  window.HTMLElement.prototype.releasePointerCapture ??= () => undefined;
  window.HTMLElement.prototype.setPointerCapture ??= () => undefined;
}
