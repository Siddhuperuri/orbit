import type { Document } from "@/features/documents/types";
import type { Rejection } from "@/features/documents/upload/validate";
import { isAbortError } from "@/lib/api/errors";
import type { UploadProgress } from "@/lib/api/upload";

/**
 * The upload queue: a small state machine with bounded concurrency, independent of
 * React so it can be tested without rendering anything.
 *
 * It lives above the documents page (in the workspace layout) so that navigating
 * to Search mid-upload does not cancel a 50 MiB transfer. Files are held outside
 * the observable state -- they are large opaque objects, and a retry needs the
 * original.
 *
 * What it reports is only what it knows:
 *
 * - `rejected`: the file never left the browser -- a type or size the server would
 *   refuse -- with the reason. Nothing to retry, so it can only be dismissed.
 * - `uploading` / `sending`: bytes are going out. `progress` is `0..1` when the browser
 *   can compute it and **`null` when it cannot** -- an unknown fraction is shown as
 *   unknown, never as 0%.
 * - `uploading` / `saving`: every byte has been sent, but the server has not answered
 *   yet -- it is hashing, storing, and committing. "100%" would be a lie here (the file
 *   is not yet safe), and so would "done", so it is its own phase.
 * - `done`: the server confirmed. What happens to the document *after* that (queued,
 *   processing, indexing, ready) is the document's own state, not the upload's.
 */

export type UploadStatus = "queued" | "uploading" | "done" | "failed" | "rejected";
export type UploadPhase = "sending" | "saving";

export interface UploadItem {
  id: string;
  fileName: string;
  size: number;
  status: UploadStatus;
  /** 0..1 while sending; `null` when the browser cannot say how much has been sent. */
  progress: number | null;
  /** Only while `uploading`. */
  phase?: UploadPhase;
  /** Where the document is being filed. Absent means the top level. */
  folderId?: string;
  error?: unknown;
  /** Set on `rejected`. */
  rejection?: Rejection;
  /** Set on success. */
  document?: Document;
  /** True when the server recognised identical content and reused the existing document. */
  deduplicated?: boolean;
}

export interface UploadResult {
  document: Document;
  deduplicated: boolean;
}

export interface UploadQueueOptions {
  /** Performs one upload. Injected so the queue has no knowledge of HTTP. */
  upload: (
    file: File,
    hooks: {
      onProgress: (progress: UploadProgress) => void;
      signal: AbortSignal;
      folderId: string | undefined;
    },
  ) => Promise<UploadResult>;
  /** Refuses a file before it is sent. Injected so the queue has no knowledge of policy. */
  validate?: (file: File) => Rejection | null;
  concurrency?: number;
  onDone?: (item: UploadItem) => void;
  onFailed?: (item: UploadItem) => void;
  onRejected?: (items: readonly UploadItem[]) => void;
}

const DEFAULT_CONCURRENCY = 2;
/** Progress events fire every few KB; emitting each would re-render the panel hundreds of times. */
const PROGRESS_STEP = 0.01;

let nextId = 0;

/** Two selections of the same file look identical: same name, size, and modification time. */
const identityOf = (file: File) => `${file.name}\u0000${file.size}\u0000${file.lastModified}`;

export class UploadQueue {
  private items: UploadItem[] = [];
  private readonly files = new Map<string, File>();
  private readonly controllers = new Map<string, AbortController>();
  private readonly listeners = new Set<() => void>();
  private readonly concurrency: number;
  private validate: ((file: File) => Rejection | null) | undefined;

  constructor(private readonly options: UploadQueueOptions) {
    this.concurrency = options.concurrency ?? DEFAULT_CONCURRENCY;
    this.validate = options.validate;
  }

  /**
   * Replaces the rule files are checked against. The rule can change after the queue exists
   * -- the deployment's limits arrive from `/meta` a moment after the page -- and recreating
   * the queue to change it would drop whatever is uploading.
   */
  setValidator(validate: ((file: File) => Rejection | null) | undefined): void {
    this.validate = validate;
  }

  // -- useSyncExternalStore contract --------------------------------------

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): readonly UploadItem[] => this.items;

  // -- Commands -----------------------------------------------------------

  add(files: readonly File[], options: { folderId?: string } = {}): void {
    const inFlight = new Set(
      this.items
        .filter((item) => item.status === "queued" || item.status === "uploading")
        .map((item) => {
          const file = this.files.get(item.id);
          return file ? identityOf(file) : "";
        }),
    );

    const added: UploadItem[] = [];
    for (const file of files) {
      nextId += 1;
      const id = `upload-${nextId}`;
      const base = { id, fileName: file.name, size: file.size, folderId: options.folderId };

      const rejection = this.validate?.(file) ?? this.duplicate(file, inFlight);
      if (rejection) {
        added.push({ ...base, status: "rejected", progress: 0, rejection });
        continue;
      }

      inFlight.add(identityOf(file));
      this.files.set(id, file);
      added.push({ ...base, status: "queued", progress: 0 });
    }
    if (added.length === 0) return;

    this.items = [...this.items, ...added];
    this.emit();

    const rejected = added.filter((item) => item.status === "rejected");
    if (rejected.length > 0) this.options.onRejected?.(rejected);
    this.pump();
  }

  /** Only a failed upload can be retried: a rejection would be refused again, the same way. */
  retry(id: string): void {
    const item = this.items.find((candidate) => candidate.id === id);
    if (item?.status !== "failed") return;
    this.update(id, { status: "queued", progress: 0, phase: undefined, error: undefined });
    this.pump();
  }

  /** Aborts an in-flight upload, or removes a queued or finished one. */
  cancel(id: string): void {
    const controller = this.controllers.get(id);
    if (controller) {
      controller.abort();
      return;
    }
    this.remove(id);
  }

  dismiss(id: string): void {
    this.remove(id);
  }

  clearFinished(): void {
    const finished = this.items.filter(
      (item) => item.status !== "queued" && item.status !== "uploading",
    );
    for (const item of finished) this.files.delete(item.id);
    this.items = this.items.filter(
      (item) => item.status === "queued" || item.status === "uploading",
    );
    this.emit();
  }

  /**
   * Drops the entries that point at a document that no longer exists. An "Uploaded ·
   * Open" row whose link now leads to a 404 is a broken promise.
   */
  forgetDocument(documentId: string): void {
    const before = this.items.length;
    this.items = this.items.filter((item) => item.document?.id !== documentId);
    if (this.items.length !== before) this.emit();
  }

  /** Whether anything is still queued or in flight. */
  hasActive(): boolean {
    return this.items.some((item) => item.status === "queued" || item.status === "uploading");
  }

  // -- Internals ----------------------------------------------------------

  private duplicate(file: File, inFlight: ReadonlySet<string>): Rejection | null {
    return inFlight.has(identityOf(file))
      ? { code: "duplicate", message: `${file.name} is already being uploaded.` }
      : null;
  }

  private pump(): void {
    let running = this.items.filter((item) => item.status === "uploading").length;
    for (const item of this.items) {
      if (running >= this.concurrency) break;
      if (item.status !== "queued") continue;
      running += 1;
      void this.run(item.id);
    }
  }

  private async run(id: string): Promise<void> {
    const file = this.files.get(id);
    const folderId = this.items.find((item) => item.id === id)?.folderId;
    if (!file) return;

    const controller = new AbortController();
    this.controllers.set(id, controller);
    this.update(id, { status: "uploading", progress: 0, phase: "sending" });

    let lastEmitted = 0;
    let saving = false;
    try {
      const result = await this.options.upload(file, {
        signal: controller.signal,
        folderId,
        onProgress: ({ fraction }) => {
          if (saving) return;
          if (fraction === null) {
            // The browser cannot compute a total: say so, rather than sit at a number.
            this.update(id, { progress: null });
            return;
          }
          if (fraction >= 1) {
            // Everything is sent. Until the server answers it is not stored, so this is
            // its own phase rather than "100%".
            saving = true;
            this.update(id, { progress: 1, phase: "saving" });
          } else if (fraction - lastEmitted >= PROGRESS_STEP) {
            lastEmitted = fraction;
            this.update(id, { progress: fraction });
          }
        },
      });
      this.update(id, {
        status: "done",
        progress: 1,
        phase: undefined,
        document: result.document,
        deduplicated: result.deduplicated,
      });
      this.notify("onDone", id);
    } catch (error) {
      if (isAbortError(error)) {
        // Cancelled by the user: it simply disappears; there is nothing to retry.
        this.remove(id);
      } else {
        this.update(id, { status: "failed", phase: undefined, error });
        this.notify("onFailed", id);
      }
    } finally {
      this.controllers.delete(id);
      this.pump();
    }
  }

  private notify(kind: "onDone" | "onFailed", id: string): void {
    const item = this.items.find((candidate) => candidate.id === id);
    if (item) this.options[kind]?.(item);
  }

  private update(id: string, changes: Partial<UploadItem>): void {
    this.items = this.items.map((item) => (item.id === id ? { ...item, ...changes } : item));
    this.emit();
  }

  private remove(id: string): void {
    this.files.delete(id);
    this.items = this.items.filter((item) => item.id !== id);
    this.emit();
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }
}
