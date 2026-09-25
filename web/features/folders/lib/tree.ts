import type { Folder } from "@/features/folders/types";

/**
 * The API returns folders as one flat list; the tree is built here.
 *
 * Built defensively, because the list can be a moment stale: a folder whose parent
 * has just been deleted (by someone else) is shown at the top level rather than
 * dropped, so it never becomes unreachable, and a parent cycle -- which the API cannot
 * produce but a hand-edited cache could -- terminates instead of looping.
 */

export interface FolderNode {
  folder: Folder;
  children: FolderNode[];
}

const collator = new Intl.Collator("en", { sensitivity: "base", numeric: true });

function byName(a: FolderNode, b: FolderNode): number {
  return collator.compare(a.folder.name, b.folder.name);
}

export function buildTree(folders: readonly Folder[]): FolderNode[] {
  const nodes = new Map<string, FolderNode>(
    folders.map((folder) => [folder.id, { folder, children: [] }]),
  );
  const roots: FolderNode[] = [];

  for (const node of nodes.values()) {
    const parent = node.folder.parent_id ? nodes.get(node.folder.parent_id) : undefined;
    if (parent && !isAncestor(nodes, node.folder.id, parent.folder.id)) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  const sortDeep = (list: FolderNode[]): FolderNode[] => {
    list.sort(byName);
    for (const node of list) sortDeep(node.children);
    return list;
  };
  return sortDeep(roots);
}

/** Whether `candidate` is `id` itself or one of its ancestors -- the only way attaching `id` under it would loop. */
function isAncestor(nodes: Map<string, FolderNode>, id: string, candidate: string): boolean {
  const seen = new Set<string>();
  let current: string | null | undefined = candidate;
  while (current && !seen.has(current)) {
    if (current === id) return true;
    seen.add(current);
    current = nodes.get(current)?.folder.parent_id;
  }
  return false;
}

export interface FlatFolder {
  folder: Folder;
  depth: number;
  /** "Reports / 2026" -- the full path, so two folders named alike are told apart. */
  path: string;
}

/** The tree in reading order, each with its depth and full path. Used for pickers. */
export function flattenTree(nodes: readonly FolderNode[]): FlatFolder[] {
  const flat: FlatFolder[] = [];
  const visit = (list: readonly FolderNode[], parents: readonly string[]) => {
    for (const node of list) {
      const names = [...parents, node.folder.name];
      flat.push({ folder: node.folder, depth: parents.length, path: names.join(" / ") });
      visit(node.children, names);
    }
  };
  visit(nodes, []);
  return flat;
}

/** The folders from the top level down to `id`, or `[]` if it is not in the list. */
export function pathTo(folders: readonly Folder[], id: string): Folder[] {
  const byId = new Map(folders.map((folder) => [folder.id, folder]));
  const path: Folder[] = [];
  const seen = new Set<string>();
  let current = byId.get(id);
  while (current && !seen.has(current.id)) {
    path.unshift(current);
    seen.add(current.id);
    current = current.parent_id ? byId.get(current.parent_id) : undefined;
  }
  return path;
}

export function pathLabel(folders: readonly Folder[], id: string): string | null {
  const path = pathTo(folders, id);
  return path.length > 0 ? path.map((folder) => folder.name).join(" / ") : null;
}

/** Everything a folder holds, for deciding whether it can be deleted and saying why not. */
export function contentsOf(folder: Folder): { documents: number; subfolders: number } {
  return {
    documents: folder.document_count + folder.archived_document_count,
    subfolders: folder.child_count,
  };
}

export function isEmpty(folder: Folder): boolean {
  const { documents, subfolders } = contentsOf(folder);
  return documents === 0 && subfolders === 0;
}
