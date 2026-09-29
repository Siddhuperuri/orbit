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
import { FileKindIcon } from "@/features/documents/components/file-kind-icon";
import { StatusBadge } from "@/features/documents/components/status-badge";
import { fileKindLabel, fileKindOf } from "@/features/documents/lib/file-kind";
import { SORT_OPTIONS } from "@/features/documents/lib/list-params";
import { statusOf } from "@/features/documents/status";
import type { Document, DocumentSort } from "@/features/documents/types";
import { pathLabel } from "@/features/folders/lib/tree";
import type { Folder } from "@/features/folders/types";
import { TagChip } from "@/features/tags/components/tag-chip";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";
import { formatBytes, formatRelativeTime, pluralize } from "@/lib/utils/format";

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
    <div className="border-line bg-canvas border-t">
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
            <TableHead className="hidden w-32 lg:table-cell">Updated</TableHead>
            <TableHead className="w-12">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {documents.map((document) => {
            const version = document.current_version;
            const kind = fileKindOf(version);
            const folder = document.folder_id
              ? (folders && pathLabel(folders, document.folder_id)) || null
              : null;

            return (
              // `relative` + the link's `after:` overlay make the whole row a target for
              // the pointer while the title stays the one real link a reader tabs to.
              <TableRow
                key={document.id}
                className={cn(
                  "scroll-reveal group relative hover:bg-transparent",
                  // A fill sweeps across the row from the left under the pointer. It is
                  // the row's own background growing -- a pseudo-element on a table row
                  // would be laid out as an extra cell.
                  "from-fill to-fill bg-linear-to-r bg-size-[0%_100%] bg-no-repeat transition-[background-size] duration-700 ease-out hover:bg-size-[100%_100%]",
                )}
              >
                <TableCell className="max-w-0 min-w-40">
                  <div className="flex items-center gap-4">
                    <FileKindIcon kind={kind} className="hidden sm:inline-flex" />
                    <div className="min-w-0 flex-1 transition-transform duration-500 ease-out group-hover:translate-x-1.5">
                      <Link
                        href={routes.document(workspaceId, document.id)}
                        className="text-fg line-clamp-2 rounded-xs text-lg leading-snug tracking-tight [overflow-wrap:anywhere] after:absolute after:inset-0 after:content-[''] sm:line-clamp-1 pointer-coarse:-my-2 pointer-coarse:py-2"
                      >
                        {document.title}
                      </Link>
                      <span className="label-micro text-fg-subtle mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span>{fileKindLabel(kind)}</span>
                        {version?.page_count ? (
                          <>
                            <span aria-hidden="true">·</span>
                            <span>{pluralize(version.page_count, "page")}</span>
                          </>
                        ) : null}
                        {folder ? (
                          <>
                            <span aria-hidden="true">·</span>
                            <span className="inline-flex min-w-0 items-center gap-1">
                              <FolderIcon className="size-3.5 shrink-0" aria-hidden="true" />
                              <span className="sr-only">In folder </span>
                              <span className="truncate">{folder}</span>
                            </span>
                          </>
                        ) : null}
                        {document.archived_at ? (
                          <Badge>
                            <Archive aria-hidden="true" />
                            Archived
                          </Badge>
                        ) : null}
                      </span>
                      {document.tags.length > 0 ? (
                        <ul aria-label="Tags" className="relative mt-2 flex flex-wrap gap-1">
                          {document.tags.map((tag) => (
                            <li key={tag.id} className="max-w-full">
                              <TagChip tag={tag} />
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  </div>
                </TableCell>
                <TableCell>
                  <StatusBadge status={statusOf(document)} stage={version?.processing_stage} />
                </TableCell>
                <TableCell className="text-fg-muted hidden text-right font-mono text-xs tabular-nums md:table-cell">
                  {version ? formatBytes(version.byte_size) : "—"}
                </TableCell>
                <TableCell className="text-fg-muted hidden font-mono text-xs lg:table-cell">
                  <time
                    dateTime={document.updated_at}
                    title={new Date(document.updated_at).toLocaleString()}
                  >
                    {formatRelativeTime(document.updated_at)}
                  </time>
                </TableCell>
                {/* Above the row-wide link overlay, so the menu stays pressable. */}
                <TableCell className="relative text-right">
                  <DocumentRowMenu document={document} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
