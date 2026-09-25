"use client";

import { Archive, Folder as FolderIcon } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { DocumentRowMenu } from "@/features/documents/components/document-row-menu";
import { StatusBadge } from "@/features/documents/components/status-badge";
import { SORT_OPTIONS } from "@/features/documents/lib/list-params";
import { statusOf } from "@/features/documents/status";
import type { Document, DocumentSort } from "@/features/documents/types";
import { pathLabel } from "@/features/folders/lib/tree";
import type { Folder } from "@/features/folders/types";
import { TagChip } from "@/features/tags/components/tag-chip";
import { routes } from "@/lib/navigation";
import { formatBytes, formatRelativeTime } from "@/lib/utils/format";

/**
 * The document list as a real table: screen readers get row and column
 * navigation, and a document's *name* is the link (rather than a click handler on
 * the row), so it is reachable and announceable like any other link.
 *
 * Titles are set in the serif face because they are the user's own words; the
 * columns that are about the file (size, date) are interface text.
 */
export function DocumentsTable({
  workspaceId,
  documents,
  folders,
  sort,
  busy = false,
}: {
  workspaceId: string;
  documents: readonly Document[];
  /** For naming each document's folder. `undefined` while loading, or if it failed. */
  folders: readonly Folder[] | undefined;
  sort: DocumentSort;
  /** Showing the previous results while new ones load. */
  busy?: boolean;
}) {
  const sortLabel = SORT_OPTIONS.find((option) => option.value === sort)?.label ?? "";

  return (
    <Table
      aria-busy={busy || undefined}
      className={busy ? "opacity-60 transition-opacity" : undefined}
    >
      <TableCaption>Documents in this workspace. Sort order: {sortLabel}.</TableCaption>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead>Name</TableHead>
          <TableHead className="w-28 md:w-32">Status</TableHead>
          <TableHead className="hidden w-24 text-right md:table-cell">Size</TableHead>
          <TableHead className="hidden w-36 md:table-cell">Updated</TableHead>
          <TableHead className="w-10 md:w-12">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((document) => {
          const version = document.current_version;
          const folder = document.folder_id
            ? (folders && pathLabel(folders, document.folder_id)) || null
            : null;

          return (
            <TableRow key={document.id}>
              <TableCell className="max-w-0 min-w-32">
                <Link
                  href={routes.document(workspaceId, document.id)}
                  className="text-md text-fg hover:text-accent line-clamp-2 rounded-xs font-serif font-medium [overflow-wrap:anywhere] hover:underline sm:line-clamp-1 pointer-coarse:-my-2 pointer-coarse:py-2"
                >
                  {document.title}
                </Link>
                <span className="text-fg-muted mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  {version ? <span className="truncate">{version.original_filename}</span> : null}
                  {folder ? (
                    <span className="inline-flex min-w-0 items-center gap-1">
                      <FolderIcon className="size-3.5 shrink-0" aria-hidden="true" />
                      <span className="sr-only">In folder </span>
                      <span className="truncate">{folder}</span>
                    </span>
                  ) : null}
                  {document.archived_at ? (
                    <Badge>
                      <Archive aria-hidden="true" />
                      Archived
                    </Badge>
                  ) : null}
                </span>
                {document.tags.length > 0 ? (
                  <ul aria-label="Tags" className="mt-1.5 flex flex-wrap gap-1">
                    {document.tags.map((tag) => (
                      <li key={tag.id} className="max-w-full">
                        <TagChip tag={tag} />
                      </li>
                    ))}
                  </ul>
                ) : null}
              </TableCell>
              <TableCell>
                <StatusBadge status={statusOf(document)} stage={version?.processing_stage} />
              </TableCell>
              <TableCell className="text-fg-muted hidden text-right tabular-nums md:table-cell">
                {version ? formatBytes(version.byte_size) : "—"}
              </TableCell>
              <TableCell className="text-fg-muted hidden md:table-cell">
                <time
                  dateTime={document.updated_at}
                  title={new Date(document.updated_at).toLocaleString()}
                >
                  {formatRelativeTime(document.updated_at)}
                </time>
              </TableCell>
              <TableCell className="text-right">
                <DocumentRowMenu document={document} />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
