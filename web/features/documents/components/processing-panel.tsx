"use client";

import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { documentKeys } from "@/features/documents/api/keys";
import { useProcessingReport, useReprocessDocument } from "@/features/documents/api/use-documents";
import { LifecycleStepper } from "@/features/documents/components/lifecycle-stepper";
import { STAGE_LABELS } from "@/features/documents/lib/lifecycle";
import { statusOf } from "@/features/documents/status";
import type { Document, ProcessingAttempt } from "@/features/documents/types";
import { useDocumentAccess } from "@/features/documents/hooks/use-document-access";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { formatDateTime } from "@/lib/utils/format";

const ATTEMPT_LABELS: Record<ProcessingAttempt["status"], string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
};

function AttemptRow({ attempt }: { attempt: ProcessingAttempt }) {
  const when = attempt.finished_at ?? attempt.started_at ?? attempt.scheduled_for;
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-2 text-sm">
      <span className="text-fg">
        <span className="font-medium">Attempt {attempt.attempt}</span>
        <span className="text-fg-muted"> · {ATTEMPT_LABELS[attempt.status]}</span>
        {attempt.stage ? (
          <span className="text-fg-muted"> · {STAGE_LABELS[attempt.stage]}</span>
        ) : null}
      </span>
      <span className="text-fg-muted flex items-baseline gap-3">
        {attempt.error_code ? (
          <code className="font-mono text-xs">{attempt.error_code}</code>
        ) : null}
        <time dateTime={when}>{formatDateTime(when)}</time>
      </span>
    </li>
  );
}

/**
 * Where the document is in the pipeline -- the step it is on, never how far through it is,
 * because the backend does not report that -- why it failed if it did (in words written for
 * the person who uploaded it), and every attempt made, with a way to try again.
 *
 * "Try again" is offered only to roles that may update the document: restarting processing
 * changes it, and a viewer has no business seeing a button the server would refuse.
 */
export function ProcessingPanel({ document }: { document: Document }) {
  const { workspace } = useWorkspace();
  const { canUpdate } = useDocumentAccess();
  const queryClient = useQueryClient();
  const status = statusOf(document);
  const report = useProcessingReport(workspace.id, document.id);
  const reprocess = useReprocessDocument(workspace.id);

  // The document is refreshed by the watcher; when its status changes, the attempt
  // history beside it is stale too.
  useEffect(() => {
    void queryClient.invalidateQueries({
      queryKey: documentKeys.processing(workspace.id, document.id),
    });
  }, [status, queryClient, workspace.id, document.id]);

  const attempts = report.data?.attempts ?? [];
  const failureReason = document.current_version?.failure_reason;

  return (
    <section aria-labelledby="processing-heading">
      <h2 id="processing-heading" className="label-micro text-fg mb-5">
        Processing
      </h2>

      <LifecycleStepper version={document.current_version} />

      {status === "failed" ? (
        <div
          role="alert"
          className="border-danger/25 bg-danger-soft mt-3 flex gap-2.5 rounded-lg border px-3.5 py-3 text-base"
        >
          <AlertTriangle className="text-danger mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div className="min-w-0 flex-1 space-y-2">
            <p className="text-fg">{failureReason ?? "Processing failed."}</p>
            {canUpdate ? (
              <Button
                size="sm"
                loading={reprocess.isPending}
                onClick={() => reprocess.mutate(document.id)}
              >
                {reprocess.isPending ? "Restarting" : "Try again"}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {attempts.length > 0 ? (
        <details className="group mt-3">
          <summary className="text-fg-muted hover:text-fg cursor-pointer rounded-xs text-sm font-medium">
            {attempts.length === 1 ? "1 attempt" : `${attempts.length} attempts`}
          </summary>
          <ol className="divide-line border-line mt-1 divide-y border-y">
            {attempts.map((attempt) => (
              <AttemptRow key={attempt.attempt} attempt={attempt} />
            ))}
          </ol>
        </details>
      ) : null}
    </section>
  );
}
