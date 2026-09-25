import type { Schema } from "@/lib/api/types";

/** A folder as listed: with what is in it. */
export type Folder = Schema<"FolderNodeResponse">;
/** A folder as returned by create/rename: no counts (they would be stale the moment they arrive). */
export type FolderRecord = Schema<"FolderResponse">;
