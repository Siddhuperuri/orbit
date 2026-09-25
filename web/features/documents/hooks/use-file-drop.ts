"use client";

import { useState, type DragEvent } from "react";

/**
 * Drag-and-drop file intake for any element.
 *
 * Only reacts to drags that actually carry files -- dragging a text selection
 * across the page should not light up an upload target. Drag-and-drop is a
 * convenience layered over the "Upload" button, never the only way in.
 */
export function useFileDrop(onFiles: (files: File[]) => void) {
  const [dragging, setDragging] = useState(false);

  const carriesFiles = (event: DragEvent) => event.dataTransfer.types.includes("Files");

  return {
    dragging,
    dropProps: {
      onDragEnter: (event: DragEvent) => {
        if (!carriesFiles(event)) return;
        event.preventDefault();
        setDragging(true);
      },
      onDragOver: (event: DragEvent) => {
        if (carriesFiles(event)) event.preventDefault();
      },
      onDragLeave: (event: DragEvent) => {
        // Moving onto a child fires dragleave on the parent too; only a real exit counts.
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
      },
      onDrop: (event: DragEvent) => {
        if (!carriesFiles(event)) return;
        event.preventDefault();
        setDragging(false);
        const files = Array.from(event.dataTransfer.files);
        if (files.length > 0) onFiles(files);
      },
    },
  };
}
