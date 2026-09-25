import { toast } from "sonner";

import { describeError } from "@/lib/api/describe-error";

/**
 * The application's notification API. Features call `notify.*`, never `toast`
 * directly, so the presentation rules -- how long an error stays, when a retry is
 * offered, that a request reference is included -- live in one place.
 *
 * Toasts are for *outcomes of something the user just did*. Persistent problems
 * (a list that failed to load) belong inline where the user is looking, not in a
 * message that disappears.
 */

const SUCCESS_MS = 4_000;
const ERROR_MS = 8_000;
/** Longer when there is an action to take, so it can be reached by keyboard. */
const ACTIONABLE_MS = 12_000;

export const notify = {
  success(message: string, options: { description?: string } = {}) {
    return toast.success(message, { description: options.description, duration: SUCCESS_MS });
  },

  info(message: string, options: { description?: string } = {}) {
    return toast(message, { description: options.description, duration: SUCCESS_MS });
  },

  /**
   * Report a failed action. Includes the request reference when there is one and,
   * for transient failures, a Retry action wired to `retry`.
   */
  error(error: unknown, options: { title?: string; retry?: () => void } = {}) {
    const description = describeError(error);
    const canRetry = Boolean(options.retry) && description.retryable;

    return toast.error(options.title ?? description.title, {
      description: (
        <span className="block">
          <span className="block">{description.message}</span>
          {description.requestId ? (
            <span className="text-fg-subtle mt-1 block font-mono text-xs">
              Ref {description.requestId}
            </span>
          ) : null}
        </span>
      ),
      duration: canRetry ? ACTIONABLE_MS : ERROR_MS,
      action: canRetry ? { label: "Retry", onClick: () => options.retry?.() } : undefined,
    });
  },

  dismiss(id?: string | number) {
    toast.dismiss(id);
  },
};
