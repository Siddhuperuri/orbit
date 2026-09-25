"use client";

import { useMemo } from "react";

import { NativeSelect } from "@/components/ui/input";
import { buildTree, flattenTree } from "@/features/folders/lib/tree";
import type { Folder } from "@/features/folders/types";

/**
 * Pick a folder -- or none. A native `<select>`: the platform's own picker is the most
 * accessible option and the best on touch. Each option is the folder's *full path*
 * ("Reports / 2026"), because two folders called "Invoices" under different parents are
 * otherwise indistinguishable in a flat list.
 */
export function FolderSelect({
  id,
  folders,
  value,
  onChange,
  noneLabel = "No folder",
  disabled,
  ...aria
}: {
  id?: string;
  folders: readonly Folder[];
  /** `null` is "no folder". */
  value: string | null;
  onChange: (folderId: string | null) => void;
  noneLabel?: string;
  disabled?: boolean;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
}) {
  const options = useMemo(() => flattenTree(buildTree(folders)), [folders]);

  return (
    <NativeSelect
      id={id}
      value={value ?? ""}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value || null)}
      {...aria}
    >
      <option value="">{noneLabel}</option>
      {options.map(({ folder, path }) => (
        <option key={folder.id} value={folder.id}>
          {path}
        </option>
      ))}
    </NativeSelect>
  );
}
