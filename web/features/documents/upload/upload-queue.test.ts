import { describe, expect, it, vi } from "vitest";

import { UploadQueue, type UploadResult } from "@/features/documents/upload/upload-queue";
import { validateFile, type UploadPolicy } from "@/features/documents/upload/validate";
import { ApiError } from "@/lib/api/errors";
import { makeDocument } from "@/test/factories";

function fileNamed(name: string, size = 10): File {
  return new File([new Uint8Array(size)], name, { type: "text/plain" });
}

function fakeDocument(id: string): UploadResult["document"] {
  return makeDocument({ id, title: id, current_version: null });
}

/** An upload the test controls: it settles only when told to. */
function controllableUpload() {
  const pending: Array<{
    file: File;
    resolve: (result: UploadResult) => void;
    reject: (error: unknown) => void;
    signal: AbortSignal;
    folderId: string | undefined;
    progress: (fraction: number | null) => void;
  }> = [];

  const upload = vi.fn(
    (
      file: File,
      hooks: {
        onProgress: (p: { loaded: number; total: number; fraction: number | null }) => void;
        signal: AbortSignal;
        folderId: string | undefined;
      },
    ) =>
      new Promise<UploadResult>((resolve, reject) => {
        pending.push({
          file,
          resolve,
          reject,
          signal: hooks.signal,
          folderId: hooks.folderId,
          progress: (fraction) =>
            hooks.onProgress({
              loaded: (fraction ?? 0) * 100,
              total: fraction === null ? 0 : 100,
              fraction,
            }),
        });
        hooks.signal.addEventListener("abort", () =>
          reject(new DOMException("cancelled", "AbortError")),
        );
      }),
  );
  return { upload, pending };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const serverError = () => new ApiError({ code: "X", status: 500, message: "boom", requestId: "r" });

describe("UploadQueue", () => {
  it("runs at most `concurrency` uploads at once and starts the next as one finishes", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload, concurrency: 2 });

    queue.add([fileNamed("a.txt"), fileNamed("b.txt"), fileNamed("c.txt")]);
    await tick();

    expect(pending).toHaveLength(2);
    expect(queue.getSnapshot().map((item) => item.status)).toEqual([
      "uploading",
      "uploading",
      "queued",
    ]);

    pending[0]!.resolve({ document: fakeDocument("a"), deduplicated: false });
    await tick();

    expect(pending).toHaveLength(3);
    expect(queue.getSnapshot().map((item) => item.status)).toEqual([
      "done",
      "uploading",
      "uploading",
    ]);
  });

  it("reports the document and whether the server deduplicated it", async () => {
    const { upload, pending } = controllableUpload();
    const onDone = vi.fn();
    const queue = new UploadQueue({ upload, onDone });

    queue.add([fileNamed("a.txt")]);
    await tick();
    pending[0]!.resolve({ document: fakeDocument("existing"), deduplicated: true });
    await tick();

    expect(onDone).toHaveBeenCalledOnce();
    expect(queue.getSnapshot()[0]).toMatchObject({ status: "done", deduplicated: true });
  });

  it("marks a failure, keeps the file, and retries it", async () => {
    const { upload, pending } = controllableUpload();
    const onFailed = vi.fn();
    const queue = new UploadQueue({ upload, onFailed });

    queue.add([fileNamed("a.txt")]);
    await tick();
    pending[0]!.reject(
      new ApiError({ code: "UPLOAD_TOO_LARGE", status: 413, message: "too big", requestId: "r" }),
    );
    await tick();

    const failed = queue.getSnapshot()[0]!;
    expect(failed.status).toBe("failed");
    expect(onFailed).toHaveBeenCalledOnce();

    queue.retry(failed.id);
    await tick();
    expect(pending).toHaveLength(2);
    expect(pending[1]!.file.name).toBe("a.txt");
    expect(queue.getSnapshot()[0]!.status).toBe("uploading");
  });

  it("removes a cancelled upload instead of reporting it as a failure", async () => {
    const { upload } = controllableUpload();
    const onFailed = vi.fn();
    const queue = new UploadQueue({ upload, onFailed });

    queue.add([fileNamed("a.txt")]);
    await tick();
    queue.cancel(queue.getSnapshot()[0]!.id);
    await tick();

    expect(queue.getSnapshot()).toHaveLength(0);
    expect(onFailed).not.toHaveBeenCalled();
  });

  it("throttles progress so a fast upload does not re-render on every event", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload });
    const listener = vi.fn();

    queue.add([fileNamed("a.txt")]);
    await tick();
    queue.subscribe(listener);

    for (let step = 1; step <= 1000; step += 1) pending[0]!.progress(step / 1000);

    // 1000 events at 0.1% each collapse to about one emission per 1%.
    expect(listener.mock.calls.length).toBeLessThan(120);
    expect(queue.getSnapshot()[0]!.progress).toBe(1);
  });

  it("reports whether anything is still active, for the unload warning", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload });

    expect(queue.hasActive()).toBe(false);
    queue.add([fileNamed("a.txt")]);
    await tick();
    expect(queue.hasActive()).toBe(true);

    pending[0]!.resolve({ document: fakeDocument("a"), deduplicated: false });
    await tick();
    expect(queue.hasActive()).toBe(false);
  });

  it("clears finished items but keeps active ones", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload, concurrency: 1 });

    queue.add([fileNamed("a.txt"), fileNamed("b.txt")]);
    await tick();
    pending[0]!.resolve({ document: fakeDocument("a"), deduplicated: false });
    await tick();
    queue.clearFinished();

    expect(queue.getSnapshot().map((item) => item.fileName)).toEqual(["b.txt"]);
  });
});

describe("UploadQueue.forgetDocument", () => {
  it("drops finished entries for a deleted document and leaves the rest", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload, concurrency: 2 });

    queue.add([fileNamed("a.txt"), fileNamed("b.txt")]);
    await tick();
    pending[0]!.resolve({ document: fakeDocument("doc-a"), deduplicated: false });
    pending[1]!.resolve({ document: fakeDocument("doc-b"), deduplicated: false });
    await tick();

    queue.forgetDocument("doc-a");

    expect(queue.getSnapshot().map((item) => item.fileName)).toEqual(["b.txt"]);
  });

  it("does not notify subscribers when nothing matched", async () => {
    const { upload } = controllableUpload();
    const queue = new UploadQueue({ upload });
    const listener = vi.fn();
    queue.subscribe(listener);

    queue.forgetDocument("nothing-here");

    expect(listener).not.toHaveBeenCalled();
  });
});

describe("UploadQueue progress honesty", () => {
  it("reports unknown progress as unknown, never as a number", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload });

    queue.add([fileNamed("a.txt")]);
    await tick();
    pending[0]!.progress(null);

    expect(queue.getSnapshot()[0]).toMatchObject({ status: "uploading", progress: null });
  });

  it("recovers a known fraction if the browser starts reporting one", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload });

    queue.add([fileNamed("a.txt")]);
    await tick();
    pending[0]!.progress(null);
    pending[0]!.progress(0.5);

    expect(queue.getSnapshot()[0]!.progress).toBe(0.5);
  });

  it("starts by sending, with nothing sent yet", async () => {
    const { upload } = controllableUpload();
    const queue = new UploadQueue({ upload });

    queue.add([fileNamed("a.txt")]);
    await tick();

    expect(queue.getSnapshot()[0]).toMatchObject({
      status: "uploading",
      phase: "sending",
      progress: 0,
    });
  });

  it("is 'saving', not done, once every byte is sent but the server has not answered", async () => {
    const { upload, pending } = controllableUpload();
    const onDone = vi.fn();
    const queue = new UploadQueue({ upload, onDone });

    queue.add([fileNamed("a.txt")]);
    await tick();
    pending[0]!.progress(1);

    // All bytes are out, but the file is not stored until the server says so.
    expect(queue.getSnapshot()[0]).toMatchObject({
      status: "uploading",
      phase: "saving",
      progress: 1,
    });
    expect(onDone).not.toHaveBeenCalled();
  });

  it("does not slip back to 'sending' if a late progress event arrives while saving", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload });

    queue.add([fileNamed("a.txt")]);
    await tick();
    pending[0]!.progress(1);
    pending[0]!.progress(0.4);
    pending[0]!.progress(null);

    expect(queue.getSnapshot()[0]).toMatchObject({ phase: "saving", progress: 1 });
  });

  it("clears the phase when the upload finishes or fails", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload, concurrency: 2 });

    queue.add([fileNamed("a.txt"), fileNamed("b.txt")]);
    await tick();
    pending[0]!.progress(1);
    pending[1]!.progress(1);
    pending[0]!.resolve({ document: fakeDocument("a"), deduplicated: false });
    pending[1]!.reject(serverError());
    await tick();

    const [done, failed] = queue.getSnapshot();
    expect(done).toMatchObject({ status: "done" });
    expect(done?.phase).toBeUndefined();
    expect(failed).toMatchObject({ status: "failed" });
    expect(failed?.phase).toBeUndefined();
  });
});

describe("UploadQueue validation", () => {
  const POLICY: UploadPolicy = { maxBytes: 100, extensions: [".pdf", ".txt"] };
  const validate = (file: File) => validateFile(file, POLICY);

  it("rejects a file before it is sent, with the reason, and never calls upload", async () => {
    const { upload } = controllableUpload();
    const onRejected = vi.fn();
    const queue = new UploadQueue({ upload, validate, onRejected });

    queue.add([fileNamed("budget.xlsx")]);
    await tick();

    expect(upload).not.toHaveBeenCalled();
    expect(queue.getSnapshot()[0]).toMatchObject({
      status: "rejected",
      rejection: { code: "unsupported-type" },
    });
    expect(onRejected).toHaveBeenCalledOnce();
  });

  it("rejects an empty file and an oversized one", async () => {
    const { upload } = controllableUpload();
    const queue = new UploadQueue({ upload, validate });

    queue.add([fileNamed("empty.txt", 0), fileNamed("huge.pdf", 101)]);
    await tick();

    expect(queue.getSnapshot().map((item) => item.rejection?.code)).toEqual(["empty", "too-large"]);
    expect(upload).not.toHaveBeenCalled();
  });

  it("uploads the good files in a mixed selection and rejects only the bad ones", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload, validate, concurrency: 5 });

    queue.add([fileNamed("ok.pdf"), fileNamed("bad.exe"), fileNamed("also-ok.txt")]);
    await tick();

    expect(queue.getSnapshot().map((item) => item.status)).toEqual([
      "uploading",
      "rejected",
      "uploading",
    ]);
    expect(pending.map((entry) => entry.file.name)).toEqual(["ok.pdf", "also-ok.txt"]);
  });

  it("cannot retry a rejection: it would be refused the same way", async () => {
    const { upload } = controllableUpload();
    const queue = new UploadQueue({ upload, validate });

    queue.add([fileNamed("bad.exe")]);
    queue.retry(queue.getSnapshot()[0]!.id);
    await tick();

    expect(upload).not.toHaveBeenCalled();
    expect(queue.getSnapshot()[0]!.status).toBe("rejected");
  });

  it("does not count a rejection as active, so it never blocks leaving the page", () => {
    const { upload } = controllableUpload();
    const queue = new UploadQueue({ upload, validate });

    queue.add([fileNamed("bad.exe")]);

    expect(queue.hasActive()).toBe(false);
  });

  it("clears rejections along with other finished items", () => {
    const { upload } = controllableUpload();
    const queue = new UploadQueue({ upload, validate });

    queue.add([fileNamed("bad.exe")]);
    queue.clearFinished();

    expect(queue.getSnapshot()).toHaveLength(0);
  });

  it("refuses the same file selected twice while the first is still in flight", async () => {
    const { upload } = controllableUpload();
    const queue = new UploadQueue({ upload });
    const file = fileNamed("a.txt");

    queue.add([file]);
    queue.add([file]);
    await tick();

    expect(upload).toHaveBeenCalledTimes(1);
    expect(queue.getSnapshot()[1]).toMatchObject({
      status: "rejected",
      rejection: { code: "duplicate" },
    });
  });

  it("refuses a duplicate inside a single selection", async () => {
    const { upload } = controllableUpload();
    const queue = new UploadQueue({ upload });
    const file = fileNamed("a.txt");

    queue.add([file, file]);
    await tick();

    expect(queue.getSnapshot().map((item) => item.status)).toEqual(["uploading", "rejected"]);
  });

  it("allows the same file again once the first has finished", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload });
    const file = fileNamed("a.txt");

    queue.add([file]);
    await tick();
    pending[0]!.resolve({ document: fakeDocument("a"), deduplicated: false });
    await tick();
    queue.add([file]);
    await tick();

    expect(upload).toHaveBeenCalledTimes(2);
  });

  it("treats files with the same name but different content as different", async () => {
    const { upload } = controllableUpload();
    const queue = new UploadQueue({ upload, concurrency: 5 });

    queue.add([fileNamed("a.txt", 10), fileNamed("a.txt", 20)]);
    await tick();

    expect(upload).toHaveBeenCalledTimes(2);
  });
});

describe("UploadQueue destination", () => {
  it("files each upload where it was added, even if the page has moved since", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload, concurrency: 1 });

    queue.add([fileNamed("a.txt")], { folderId: "folder-a" });
    queue.add([fileNamed("b.txt")], { folderId: "folder-b" });
    queue.add([fileNamed("c.txt")]);
    await tick();
    expect(pending[0]!.folderId).toBe("folder-a");
    pending[0]!.resolve({ document: fakeDocument("a"), deduplicated: false });
    await tick();
    expect(pending[1]!.folderId).toBe("folder-b");
    pending[1]!.resolve({ document: fakeDocument("b"), deduplicated: false });
    await tick();
    expect(pending[2]!.folderId).toBeUndefined();
  });

  it("keeps the destination when a failed upload is retried", async () => {
    const { upload, pending } = controllableUpload();
    const queue = new UploadQueue({ upload });

    queue.add([fileNamed("a.txt")], { folderId: "folder-a" });
    await tick();
    pending[0]!.reject(serverError());
    await tick();
    queue.retry(queue.getSnapshot()[0]!.id);
    await tick();

    expect(pending[1]!.folderId).toBe("folder-a");
  });
});
