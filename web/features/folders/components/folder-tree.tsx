"use client";

import { ChevronRight, Folder as FolderIcon, FolderOpen, MoreHorizontal } from "lucide-react";
import Link from "next/link";
import { useMemo, useRef, useState } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { scopeHref, type DocumentListState } from "@/features/documents/lib/list-params";
import { DeleteFolderDialog } from "@/features/folders/components/delete-folder-dialog";
import {
  FolderFormDialog,
  type FolderDialogMode,
} from "@/features/folders/components/folder-form-dialog";
import { buildTree, pathTo, type FolderNode } from "@/features/folders/lib/tree";
import type { Folder } from "@/features/folders/types";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { cn } from "@/lib/utils/cn";

/** Indentation stops growing here; a folder 16 levels deep must not push its name off the rail. */
const MAX_VISUAL_DEPTH = 6;
const INDENT_PX = 12;

interface Row {
  node: FolderNode;
  depth: number;
}

/** The rows currently visible: a folder's children are listed only while it is expanded. */
function visibleRows(
  nodes: readonly FolderNode[],
  expanded: ReadonlySet<string>,
  depth = 0,
): Row[] {
  return nodes.flatMap((node) => [
    { node, depth },
    ...(expanded.has(node.folder.id) ? visibleRows(node.children, expanded, depth + 1) : []),
  ]);
}

type Dialog = { kind: "form"; mode: FolderDialogMode } | { kind: "delete"; folder: Folder } | null;

/**
 * The folder tree, as navigation: each folder is a link to its documents, so it can be
 * opened in a new tab, bookmarked, and read by a screen reader as a list of links -- not
 * a widget to learn. Disclosure buttons open and close branches.
 *
 * The actions on a folder (new subfolder, rename, delete) exist only for roles that hold
 * `folder:write`; everyone else gets the links and nothing to press.
 */
export function FolderTree({
  folders,
  state,
  workspaceId,
}: {
  folders: readonly Folder[];
  state: DocumentListState;
  workspaceId: string;
}) {
  const { can } = useWorkspace();
  const canManage = can("folder:write");
  const tree = useMemo(() => buildTree(folders), [folders]);

  const activeId = state.folder.kind === "folder" ? state.folder.id : null;
  const ancestorIds = useMemo(
    () => (activeId ? pathTo(folders, activeId).map((folder) => folder.id) : []),
    [folders, activeId],
  );

  // What is open is *derived*: the path to the folder being viewed is always open, so the
  // current place is never hidden, plus whatever the user opened -- minus whatever they
  // closed. Two small sets of choices rather than one set that an effect must keep in
  // sync with the URL.
  const [opened, setOpened] = useState<ReadonlySet<string>>(new Set());
  const [closed, setClosed] = useState<ReadonlySet<string>>(new Set());
  const expanded = useMemo(() => {
    const open = new Set([...opened, ...ancestorIds]);
    for (const id of closed) open.delete(id);
    return open;
  }, [opened, closed, ancestorIds]);

  const [dialog, setDialog] = useState<Dialog>(null);
  // Where focus goes when a dialog closes: the folder's "actions" button, which outlives the
  // menu. (The menu *item* that was chosen does not -- it is unmounted with the menu, and
  // returning focus to a detached node silently drops it on <body>.)
  const list = useRef<HTMLUListElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  function toggle(id: string) {
    const open = expanded.has(id);
    setOpened((current) => {
      const next = new Set(current);
      if (open) next.delete(id);
      else next.add(id);
      return next;
    });
    setClosed((current) => {
      const next = new Set(current);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function openFrom(folderId: string, next: Dialog) {
    returnFocus.current =
      list.current?.querySelector<HTMLElement>(`[data-folder-actions="${folderId}"]`) ?? null;
    setDialog(next);
  }

  return (
    <>
      <ul ref={list} className="space-y-px">
        {visibleRows(tree, expanded).map(({ node, depth }) => {
          const { folder, children } = node;
          const active = folder.id === activeId;
          const open = expanded.has(folder.id);
          const Icon = active ? FolderOpen : FolderIcon;

          return (
            <li
              key={folder.id}
              style={{ paddingInlineStart: Math.min(depth, MAX_VISUAL_DEPTH) * INDENT_PX }}
            >
              <div
                className={cn(
                  "group flex items-center rounded-md",
                  active ? "bg-accent-soft text-accent-soft-fg" : "hover:bg-fill",
                )}
              >
                {children.length > 0 ? (
                  <button
                    type="button"
                    onClick={() => toggle(folder.id)}
                    aria-expanded={open}
                    aria-label={`${open ? "Collapse" : "Expand"} ${folder.name}`}
                    className="text-fg-muted hover:text-fg flex size-7 shrink-0 items-center justify-center rounded-md pointer-coarse:size-11"
                  >
                    <ChevronRight
                      className={cn("size-3.5 transition-transform", open && "rotate-90")}
                      aria-hidden="true"
                    />
                  </button>
                ) : (
                  <span className="size-7 shrink-0 pointer-coarse:size-4" aria-hidden="true" />
                )}

                <Link
                  href={scopeHref(workspaceId, state, { kind: "folder", id: folder.id })}
                  aria-current={active ? "page" : undefined}
                  className="flex min-h-7 min-w-0 flex-1 items-center gap-2 rounded-md py-1 pr-1 text-base pointer-coarse:min-h-11"
                >
                  <Icon className="size-4 shrink-0" aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate">{folder.name}</span>
                  {folder.document_count > 0 ? (
                    <span className="text-fg-muted text-xs tabular-nums">
                      <span className="sr-only">, </span>
                      {folder.document_count}
                      <span className="sr-only">
                        {folder.document_count === 1 ? " document" : " documents"}
                      </span>
                    </span>
                  ) : null}
                </Link>

                {canManage ? (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        data-folder-actions={folder.id}
                        aria-label={`Actions for folder ${folder.name}`}
                        className="text-fg-muted hover:text-fg flex size-7 shrink-0 items-center justify-center rounded-md opacity-0 group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100 pointer-coarse:size-11 pointer-coarse:opacity-100"
                      >
                        <MoreHorizontal className="size-4" aria-hidden="true" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start">
                      <DropdownMenuItem
                        onSelect={() =>
                          openFrom(folder.id, {
                            kind: "form",
                            mode: { kind: "create", parent: folder },
                          })
                        }
                      >
                        New subfolder…
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() =>
                          openFrom(folder.id, {
                            kind: "form",
                            mode: { kind: "rename", folder },
                          })
                        }
                      >
                        Rename…
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        destructive
                        onSelect={() => openFrom(folder.id, { kind: "delete", folder })}
                      >
                        Delete…
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>

      {dialog?.kind === "form" ? (
        <FolderFormDialog
          open
          mode={dialog.mode}
          returnFocusRef={returnFocus}
          onOpenChange={(open) => !open && setDialog(null)}
        />
      ) : null}
      {dialog?.kind === "delete" ? (
        <DeleteFolderDialog
          open
          folder={freshest(folders, dialog.folder)}
          returnFocusRef={returnFocus}
          onOpenChange={(open) => !open && setDialog(null)}
        />
      ) : null}
    </>
  );
}

/** The dialog holds a snapshot; if the tree has refetched since, its counts are what to trust. */
function freshest(folders: readonly Folder[], snapshot: Folder): Folder {
  return folders.find((folder) => folder.id === snapshot.id) ?? snapshot;
}
