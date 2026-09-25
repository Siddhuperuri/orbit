import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { documentApi } from "@/features/documents/api/endpoints";
import { documentKeys } from "@/features/documents/api/keys";
import { useProcessingWatcher } from "@/features/documents/hooks/use-processing-watcher";
import { makeDocumentIn } from "@/test/factories";

const WS = "ws-1";

function harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

describe("useProcessingWatcher", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("makes its next check soon after a new stretch of processing starts, however busy the cache entry has been", async () => {
    // A document someone has been reading: its detail entry has been written many times
    // (page loads, renames, tag edits, focus refetches). Backing off by that count would
    // put the first check ~15 seconds out.
    const pending = makeDocumentIn("pending", { id: "doc-1" });
    const ready = makeDocumentIn("ready", { id: "doc-1" });
    const { client, wrapper } = harness();
    for (let write = 0; write < 8; write += 1) {
      client.setQueryData(documentKeys.detail(WS, "doc-1"), { ...pending, version: write + 1 });
    }

    const get = vi
      .spyOn(documentApi, "get")
      .mockResolvedValueOnce(pending)
      .mockResolvedValue(ready);
    renderHook(() => useProcessingWatcher(WS, [pending]), { wrapper });

    // The mount-time fetch.
    await vi.advanceTimersByTimeAsync(0);
    expect(get).toHaveBeenCalledTimes(1);

    // The first backoff step is 1.5s. Within two seconds it must have checked again.
    await vi.advanceTimersByTimeAsync(2_000);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("stops polling once the document is ready", async () => {
    const pending = makeDocumentIn("pending", { id: "doc-1" });
    const ready = makeDocumentIn("ready", { id: "doc-1" });
    const { wrapper } = harness();

    const get = vi
      .spyOn(documentApi, "get")
      .mockResolvedValueOnce(pending)
      .mockResolvedValue(ready);
    renderHook(() => useProcessingWatcher(WS, [pending]), { wrapper });

    await vi.advanceTimersByTimeAsync(2_000);
    expect(get).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(120_000);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("backs off while a document stays in progress, so a stuck one does not cost a request every 2 seconds", async () => {
    const processing = makeDocumentIn("processing", { id: "doc-1" }, "parse");
    const { wrapper } = harness();

    const get = vi.spyOn(documentApi, "get").mockResolvedValue(processing);
    renderHook(() => useProcessingWatcher(WS, [processing]), { wrapper });

    await vi.advanceTimersByTimeAsync(60_000);

    // 1.5s, 2.4s, 3.8s, 6.1s, 9.8s, 15s, 15s, ... : a handful, not thirty.
    expect(get.mock.calls.length).toBeGreaterThan(4);
    expect(get.mock.calls.length).toBeLessThan(12);
  });

  it("runs no queries at all when nothing is in progress", async () => {
    const { wrapper } = harness();
    const get = vi.spyOn(documentApi, "get");

    renderHook(
      () => useProcessingWatcher(WS, [makeDocumentIn("ready"), makeDocumentIn("failed")]),
      {
        wrapper,
      },
    );
    await vi.advanceTimersByTimeAsync(60_000);

    expect(get).not.toHaveBeenCalled();
  });

  it("watches at most a handful of documents at once", async () => {
    const { wrapper } = harness();
    const docs = Array.from({ length: 25 }, (_, index) =>
      makeDocumentIn("processing", { id: `doc-${index}` }, "parse"),
    );
    const get = vi.spyOn(documentApi, "get").mockImplementation((_ws, id) => {
      return Promise.resolve(docs.find((doc) => doc.id === id)!);
    });

    const { result } = renderHook(() => useProcessingWatcher(WS, docs), { wrapper });
    await vi.advanceTimersByTimeAsync(0);

    expect(result.current.watching).toBe(10);
    expect(get).toHaveBeenCalledTimes(10);
  });

  it("starts a fresh, quick schedule when a finished document goes back into processing", async () => {
    // e.g. "Try again", or a new version: same document id, a new stretch of work.
    const ready = makeDocumentIn("ready", { id: "doc-1" });
    const retrying = makeDocumentIn("pending", { id: "doc-1" });
    const { wrapper } = harness();
    const get = vi.spyOn(documentApi, "get").mockResolvedValue(retrying);

    const { rerender } = renderHook(({ documents }) => useProcessingWatcher(WS, documents), {
      wrapper,
      initialProps: { documents: [ready] },
    });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(get).not.toHaveBeenCalled();

    rerender({ documents: [retrying] });
    await vi.advanceTimersByTimeAsync(0);
    expect(get).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("checks straight away when the user returns to the tab, instead of waiting out a long backoff", async () => {
    const processing = makeDocumentIn("processing", { id: "doc-1" }, "parse");
    const { wrapper } = harness();
    const get = vi.spyOn(documentApi, "get").mockResolvedValue(processing);
    renderHook(() => useProcessingWatcher(WS, [processing]), { wrapper });

    // Long enough for the backoff to have reached its ceiling.
    await vi.advanceTimersByTimeAsync(40_000);
    const before = get.mock.calls.length;

    focusManager.setFocused(false);
    focusManager.setFocused(true);
    await vi.advanceTimersByTimeAsync(0);

    expect(get.mock.calls.length).toBe(before + 1);
  });
});
