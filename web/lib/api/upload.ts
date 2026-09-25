import { buildUrl } from "@/lib/api/client";
import { apiErrorFromRaw, networkError } from "@/lib/api/errors";
import { withSessionRefresh } from "@/lib/api/session";
import type { PathParamsOf, PathsWith, ResponseBody } from "@/lib/api/types";

/**
 * Upload transport.
 *
 * The API accepts the file as the **raw request body**, not multipart (see the
 * backend's documents router for why: it is what makes the size limit a real
 * control rather than a check after the bytes are already spooled). `fetch` cannot
 * report upload progress, and a 50 MiB upload with no progress bar reads as a hung
 * page, so this uses `XMLHttpRequest`, whose `upload` events can.
 *
 * It still goes through the same rules as every other call: path templating, the
 * error envelope, and the transparent session refresh. A `File` is re-readable, so
 * replaying it after a refresh is safe.
 */

export interface UploadProgress {
  loaded: number;
  total: number;
  /** 0..1, or `null` when the browser cannot compute the total. */
  fraction: number | null;
}

type UploadPath = PathsWith<"post">;

export interface UploadOptions<P extends UploadPath> {
  /** Path parameters of the route, e.g. `{ workspace_id }`. */
  path: PathParamsOf<P, "post">;
  query?: Record<string, string | number | boolean | undefined | null>;
  file: Blob;
  /** Sent as the `filename` query parameter the backend requires. */
  filename: string;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

function parseHeaders(raw: string): Headers {
  const headers = new Headers();
  for (const line of raw.trim().split(/[\r\n]+/)) {
    const separator = line.indexOf(":");
    if (separator > 0) {
      headers.append(line.slice(0, separator).trim(), line.slice(separator + 1).trim());
    }
  }
  return headers;
}

function sendOnce<T>(
  url: string,
  options: Pick<UploadOptions<UploadPath>, "file" | "onProgress" | "signal">,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", url);
    request.setRequestHeader("Accept", "application/json");
    request.setRequestHeader("X-Requested-With", "orbit-web");
    // The server sniffs the real type from the bytes and ignores this header; it
    // is set only so the request is well-formed.
    request.setRequestHeader("Content-Type", options.file.type || "application/octet-stream");

    request.upload.onprogress = (event) => {
      options.onProgress?.({
        loaded: event.loaded,
        total: event.total,
        fraction: event.lengthComputable && event.total > 0 ? event.loaded / event.total : null,
      });
    };

    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        try {
          resolve(JSON.parse(request.responseText) as T);
        } catch (cause) {
          reject(networkError(cause));
        }
        return;
      }
      reject(
        apiErrorFromRaw({
          status: request.status,
          headers: parseHeaders(request.getAllResponseHeaders()),
          text: request.responseText,
        }),
      );
    };
    request.onerror = () => reject(networkError(new Error("Upload connection failed")));
    request.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));

    const signal = options.signal;
    if (signal) {
      if (signal.aborted) {
        reject(new DOMException("Upload cancelled", "AbortError"));
        return;
      }
      signal.addEventListener("abort", () => request.abort(), { once: true });
    }

    request.send(options.file);
  });
}

/**
 * POST a file as the raw body. The response type is inferred from the route.
 */
export function uploadFile<P extends UploadPath>(
  path: P,
  options: UploadOptions<P>,
): Promise<ResponseBody<P, "post">> {
  const url = buildUrl(path, options.path as Record<string, string | number>, {
    ...options.query,
    filename: options.filename,
  });
  return withSessionRefresh(() => sendOnce<ResponseBody<P, "post">>(url, options));
}
