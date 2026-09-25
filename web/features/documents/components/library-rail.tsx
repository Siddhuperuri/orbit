"use client";

import { Archive, Files, FolderPlus, Inbox, Tags } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { ErrorState } from "@/components/feedback/error-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  MAX_TAG_FILTERS,
  scopeHref,
  type DocumentListState,
} from "@/features/documents/lib/list-params";
import { useFolders } from "@/features/folders/api/use-folders";
import { FolderFormDialog } from "@/features/folders/components/folder-form-dialog";
import { FolderTree } from "@/features/folders/components/folder-tree";
import { useTags } from "@/features/tags/api/use-tags";
import { TagChip } from "@/features/tags/components/tag-chip";
import { TagManagerDialog } from "@/features/tags/components/tag-manager-dialog";
import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { cn } from "@/lib/utils/cn";
import { pluralize } from "@/lib/utils/format";

const linkClasses =
  "flex min-h-8 items-center gap-2.5 rounded-md px-2.5 text-base font-medium pointer-coarse:min-h-11";

function ScopeLink({
  href,
  icon: Icon,
  active,
  children,
}: {
  href: string;
  icon: typeof Files;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        linkClasses,
        active
          ? "bg-accent-soft text-accent-soft-fg"
          : "text-fg-muted hover:bg-line/60 hover:text-fg",
      )}
    >
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      {children}
    </Link>
  );
}

function SectionHeading({
  id,
  children,
  action,
}: {
  id: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-1 flex items-center justify-between px-2.5">
      <h2 id={id} className="text-fg-muted text-xs font-medium tracking-wide uppercase">
        {children}
      </h2>
      {action}
    </div>
  );
}

/**
 * How a workspace's documents are organised, as navigation: where you are (all, unfiled,
 * archived, a folder) and which tags to narrow by.
 *
 * Everything that changes the organisation -- new folder, manage tags -- is offered only
 * to roles that hold the matching permission. A viewer gets the same navigation and
 * nothing to press that would only produce a refusal.
 */
export function LibraryRail({
  state,
  onToggleTag,
}: {
  state: DocumentListState;
  onToggleTag: (tagId: string) => void;
}) {
  const { workspace, can } = useWorkspace();
  const folders = useFolders(workspace.id);
  const tags = useTags(workspace.id);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [managingTags, setManagingTags] = useState(false);
  // The buttons that open the dialogs, so closing one puts focus back where it came from.
  const newFolderButton = useRef<HTMLButtonElement>(null);
  const manageTagsButton = useRef<HTMLButtonElement>(null);

  const atTagLimit = state.tags.length >= MAX_TAG_FILTERS;
  const inArchive = state.archive === "archived";

  return (
    <nav aria-label="Library" className="space-y-6">
      <ul className="space-y-px">
        <li>
          <ScopeLink
            href={scopeHref(workspace.id, state, { kind: "all" })}
            icon={Files}
            active={!inArchive && state.folder.kind === "all"}
          >
            All documents
          </ScopeLink>
        </li>
        <li>
          <ScopeLink
            href={scopeHref(workspace.id, state, { kind: "unfiled" })}
            icon={Inbox}
            active={!inArchive && state.folder.kind === "unfiled"}
          >
            Unfiled
          </ScopeLink>
        </li>
        <li>
          <ScopeLink
            href={scopeHref(workspace.id, state, { kind: "all" }, "archived")}
            icon={Archive}
            active={inArchive}
          >
            Archived
          </ScopeLink>
        </li>
      </ul>

      <section aria-labelledby="rail-folders">
        <SectionHeading
          id="rail-folders"
          action={
            can("folder:write") ? (
              <Button
                ref={newFolderButton}
                size="icon"
                variant="ghost"
                aria-label="New folder"
                className="-mr-1.5 size-6"
                onClick={() => setCreatingFolder(true)}
              >
                <FolderPlus aria-hidden="true" />
              </Button>
            ) : null
          }
        >
          Folders
        </SectionHeading>

        {folders.isPending ? (
          <div role="status" aria-busy="true" className="space-y-2 px-2.5 py-1">
            <span className="sr-only">Loading folders…</span>
            <Skeleton className="h-5 w-3/4" />
            <Skeleton className="h-5 w-1/2" />
          </div>
        ) : folders.data === undefined ? (
          <ErrorState
            compact
            error={folders.error}
            title="Couldn't load folders"
            onRetry={() => void folders.refetch()}
            retrying={folders.isFetching}
            className="px-2.5"
          />
        ) : folders.data.length === 0 ? (
          <p className="text-fg-muted px-2.5 py-1 text-sm">
            No folders yet.
            {can("folder:write") ? " Use the + button to create one." : null}
          </p>
        ) : (
          <FolderTree folders={folders.data} state={state} workspaceId={workspace.id} />
        )}
      </section>

      <section aria-labelledby="rail-tags">
        <SectionHeading
          id="rail-tags"
          action={
            can("tag:write") ? (
              <Button
                ref={manageTagsButton}
                size="sm"
                variant="ghost"
                className="-mr-1.5 h-6 px-1.5 text-xs"
                onClick={() => setManagingTags(true)}
              >
                <Tags aria-hidden="true" />
                Manage
              </Button>
            ) : null
          }
        >
          Tags
        </SectionHeading>

        {tags.isPending ? (
          <div role="status" aria-busy="true" className="space-y-2 px-2.5 py-1">
            <span className="sr-only">Loading tags…</span>
            <Skeleton className="h-5 w-2/3" />
          </div>
        ) : tags.data === undefined ? (
          <ErrorState
            compact
            error={tags.error}
            title="Couldn't load tags"
            onRetry={() => void tags.refetch()}
            retrying={tags.isFetching}
            className="px-2.5"
          />
        ) : tags.data.length === 0 ? (
          <p className="text-fg-muted px-2.5 py-1 text-sm">
            No tags yet.{can("tag:write") ? " Use Manage to add one." : null}
          </p>
        ) : (
          <>
            <ul className="flex flex-wrap gap-1.5 px-2.5 py-1">
              {tags.data.map((tag) => {
                const selected = state.tags.includes(tag.id);
                return (
                  <li key={tag.id} className="max-w-full">
                    <button
                      type="button"
                      aria-pressed={selected}
                      disabled={!selected && atTagLimit}
                      onClick={() => onToggleTag(tag.id)}
                      className={cn(
                        "max-w-full rounded-sm text-left disabled:opacity-50 pointer-coarse:py-1.5",
                        selected && "ring-accent ring-offset-canvas ring-2 ring-offset-1",
                      )}
                    >
                      <TagChip tag={tag} />
                      <span className="sr-only">
                        {" "}
                        ({pluralize(tag.document_count, "document")}
                        {selected ? ", filtering by this tag" : ""})
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {atTagLimit ? (
              <p className="text-fg-muted px-2.5 text-xs">
                A list can be narrowed by up to {MAX_TAG_FILTERS} tags.
              </p>
            ) : null}
          </>
        )}
      </section>

      <FolderFormDialog
        open={creatingFolder}
        onOpenChange={setCreatingFolder}
        mode={{ kind: "create", parent: null }}
        returnFocusRef={newFolderButton}
      />
      <TagManagerDialog
        open={managingTags}
        onOpenChange={setManagingTags}
        returnFocusRef={manageTagsButton}
      />
    </nav>
  );
}
