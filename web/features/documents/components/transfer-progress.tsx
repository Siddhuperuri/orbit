import type { UploadPhase } from "@/features/documents/upload/upload-queue";

/**
 * A transfer's progress, drawn only from what it can report.
 *
 * - A known fraction is a determinate bar with its percentage.
 * - An unknown fraction (the browser could not compute a total) is an *indeterminate* bar
 *   with no number -- a bar stuck at 0% would claim something that is not known.
 * - Once every byte is sent the bar is full and the label says "Saving": the file is not
 *   stored until the server answers, so neither "100%" nor "done" is true yet.
 */
export function TransferProgress({
  fileName,
  progress,
  phase,
}: {
  fileName: string;
  /** 0..1, or `null` when unknown. */
  progress: number | null;
  phase: UploadPhase | undefined;
}) {
  const saving = phase === "saving";
  const known = progress !== null && !saving;
  const percent = Math.round((progress ?? 0) * 100);
  const label = saving
    ? `Saving ${fileName}`
    : known
      ? `Uploading ${fileName}`
      : `Uploading ${fileName}, amount sent unknown`;

  return (
    <div className="space-y-1">
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={known ? percent : undefined}
        aria-valuetext={known ? `${percent}%` : saving ? "Saving" : "Uploading"}
        className="bg-line h-1 overflow-hidden rounded-full"
      >
        <div
          className={
            known
              ? "bg-accent-solid h-full transition-[width] duration-150"
              : "bg-accent-solid animate-skeleton h-full"
          }
          style={{ width: known ? `${percent}%` : "100%" }}
        />
      </div>
      <p className="text-fg-muted text-sm">
        {saving
          ? "Saving… the whole file has been sent."
          : known
            ? `Uploading · ${percent}%`
            : "Uploading…"}
      </p>
    </div>
  );
}
