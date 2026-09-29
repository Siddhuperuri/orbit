"use client";

import { Folder as FolderIcon } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { CopyButton } from "@/components/feedback/copy-button";
import { Button } from "@/components/ui/button";
import { useUser } from "@/features/auth/hooks/use-user";
import { DocumentTags } from "@/features/documents/components/document-tags";
import { MoveDocumentDialog } from "@/features/documents/components/move-document-dialog";
import { useDocumentAccess } from "@/features/documents/hooks/use-document-access";
import { scopeHref, DEFAULT_LIST_STATE } from "@/features/documents/lib/list-params";
import type { Document } from "@/features/documents/types";
import { useFolders } from "@/features/folders/api/use-folders";
import { pathLabel } from "@/features/folders/lib/tree";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { formatBytes, formatDateTime, pluralize, shortId } from "@/lib/utils/format";

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid min-w-0 grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 py-2">
      <dt className="text-fg-subtle pt-px text-sm">{label}</dt>
      <dd className="text-fg text-base [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

/**
 * Everything the API knows about the document, as a description list a screen reader
 * announces term by term: the file, when and by whom it was added, where it is filed, and
 * how it is tagged.
 *
 * The API records *who* by id and has no names to give, so an uploader is "you", or
 * "member" plus a short id, or "a deleted account" -- and never a guess.
 */
export function DocumentMetadata({ document }: { document: Document }) {
  const { workspace } = useWorkspace();
  const { canUpdate } = useDocumentAccess();
  const user = useUser();
  const folders = useFolders(workspace.id);
  const [moving, setMoving] = useState(false);
  const moveTrigger = useRef<HTMLButtonElement>(null);
  const version = document.current_version;

  const folderPath =
    document.folder_id === null
      ? null
      : ((folders.data && pathLabel(folders.data, document.folder_id)) ?? null);

  const addedBy =
    document.created_by_user_id === null
      ? "a deleted account"
      : document.created_by_user_id === user.id
        ? "you"
        : `member ${shortId(document.created_by_user_id)}`;

  return (
    <section aria-labelledby="details-heading">
      <h2 id="details-heading" className="label-micro text-fg mb-4">
        Details
      </h2>

      <dl className="divide-line -my-2 divide-y">
        <Fact label="Folder">
          {document.folder_id === null ? (
            <span className="text-fg-muted">Not in a folder</span>
          ) : folderPath ? (
            <Link
              href={scopeHref(workspace.id, DEFAULT_LIST_STATE, {
                kind: "folder",
                id: document.folder_id,
              })}
              className="text-accent inline-flex items-center gap-1 rounded-xs hover:underline"
            >
              <FolderIcon className="size-3.5 shrink-0" aria-hidden="true" />
              {folderPath}
            </Link>
          ) : (
            <span className="text-fg-muted">
              {folders.isPending ? "Loading…" : "A folder you can't see"}
            </span>
          )}
          {canUpdate ? (
            <Button
              ref={moveTrigger}
              size="sm"
              variant="ghost"
              className="mt-0.5 -ml-2 h-7"
              onClick={() => setMoving(true)}
            >
              Move…
            </Button>
          ) : null}
        </Fact>

        <Fact label="Added">
          <time dateTime={document.created_at}>{formatDateTime(document.created_at)}</time>
          <span className="text-fg-muted block text-sm">by {addedBy}</span>
        </Fact>

        {version ? (
          <>
            <Fact label="File">{version.original_filename}</Fact>
            <Fact label="Size">{formatBytes(version.byte_size)}</Fact>
            <Fact label="Type">
              <span className="font-mono text-sm">{version.content_type}</span>
            </Fact>
            <Fact label="Pages">{version.page_count === null ? "—" : version.page_count}</Fact>
            <Fact label="Passages">
              {version.status === "ready" ? pluralize(version.chunk_count, "passage") : "—"}
            </Fact>
            <Fact label="Processed">
              {version.processed_at ? (
                <time dateTime={version.processed_at}>{formatDateTime(version.processed_at)}</time>
              ) : (
                "—"
              )}
            </Fact>
            <Fact label="Updated">
              <time dateTime={document.updated_at}>{formatDateTime(document.updated_at)}</time>
            </Fact>
            <Fact label="Checksum">
              <span className="inline-flex items-center gap-1">
                <code className="font-mono text-xs" title={version.content_sha256}>
                  {version.content_sha256.slice(0, 12)}…
                </code>
                <CopyButton value={version.content_sha256} label="Copy SHA-256 checksum" />
              </span>
            </Fact>
          </>
        ) : null}
      </dl>

      <div className="border-line mt-4 border-t pt-4">
        <h3 className="text-fg-subtle mb-2 text-sm">Tags</h3>
        <DocumentTags document={document} />
      </div>

      {canUpdate ? (
        <MoveDocumentDialog
          document={document}
          open={moving}
          onOpenChange={setMoving}
          returnFocusRef={moveTrigger}
        />
      ) : null}
    </section>
  );
}
