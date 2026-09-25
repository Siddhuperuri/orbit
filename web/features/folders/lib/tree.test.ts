import { describe, expect, it } from "vitest";

import {
  buildTree,
  contentsOf,
  flattenTree,
  isEmpty,
  pathLabel,
  pathTo,
} from "@/features/folders/lib/tree";
import { makeFolder } from "@/test/factories";

const reports = makeFolder({ id: "reports", name: "Reports" });
const y2026 = makeFolder({ id: "y2026", name: "2026", parent_id: "reports", depth: 1 });
const invoices = makeFolder({ id: "invoices", name: "Invoices", parent_id: "y2026", depth: 2 });
const archive = makeFolder({ id: "archive", name: "archive" });

describe("buildTree", () => {
  it("nests children under their parents", () => {
    const [root] = buildTree([reports, y2026, invoices]);
    expect(root?.folder.id).toBe("reports");
    expect(root?.children[0]?.folder.id).toBe("y2026");
    expect(root?.children[0]?.children[0]?.folder.id).toBe("invoices");
  });

  it("does not depend on the order it is given", () => {
    const shuffled = buildTree([invoices, archive, y2026, reports]);
    expect(shuffled.map((node) => node.folder.id)).toEqual(["archive", "reports"]);
    expect(shuffled[1]?.children[0]?.children[0]?.folder.id).toBe("invoices");
  });

  it("sorts siblings by name, case- and number-aware", () => {
    const b = makeFolder({ id: "b", name: "Beta" });
    const a = makeFolder({ id: "a", name: "alpha" });
    const ten = makeFolder({ id: "n10", name: "Item 10" });
    const two = makeFolder({ id: "n2", name: "Item 2" });
    expect(buildTree([b, ten, a, two]).map((node) => node.folder.id)).toEqual([
      "a",
      "b",
      "n2",
      "n10",
    ]);
  });

  it("shows a folder whose parent has vanished at the top level instead of losing it", () => {
    // Someone deleted the parent between two fetches.
    const orphan = makeFolder({ id: "orphan", name: "Orphan", parent_id: "gone", depth: 1 });
    expect(buildTree([orphan]).map((node) => node.folder.id)).toEqual(["orphan"]);
  });

  it("terminates on a parent cycle rather than looping", () => {
    const a = makeFolder({ id: "a", name: "A", parent_id: "b" });
    const b = makeFolder({ id: "b", name: "B", parent_id: "a" });
    const tree = buildTree([a, b]);
    // Neither can be reached from the other, so at least one is a root and nothing hangs.
    expect(tree.length).toBeGreaterThan(0);
    expect(JSON.stringify(tree)).toBeTruthy();
  });

  it("is empty for no folders", () => {
    expect(buildTree([])).toEqual([]);
  });
});

describe("flattenTree", () => {
  it("lists folders in reading order with depth and full path", () => {
    const flat = flattenTree(buildTree([reports, y2026, invoices, archive]));
    expect(flat.map((entry) => [entry.folder.id, entry.depth, entry.path])).toEqual([
      ["archive", 0, "archive"],
      ["reports", 0, "Reports"],
      ["y2026", 1, "Reports / 2026"],
      ["invoices", 2, "Reports / 2026 / Invoices"],
    ]);
  });

  it("gives two like-named folders different paths, so a picker can tell them apart", () => {
    const a = makeFolder({ id: "a", name: "2025" });
    const b = makeFolder({ id: "b", name: "2026" });
    const invA = makeFolder({ id: "ia", name: "Invoices", parent_id: "a", depth: 1 });
    const invB = makeFolder({ id: "ib", name: "Invoices", parent_id: "b", depth: 1 });
    const paths = flattenTree(buildTree([a, b, invA, invB])).map((entry) => entry.path);
    expect(new Set(paths).size).toBe(4);
  });
});

describe("paths", () => {
  const all = [reports, y2026, invoices];

  it("walks from the top level down to a folder", () => {
    expect(pathTo(all, "invoices").map((folder) => folder.id)).toEqual([
      "reports",
      "y2026",
      "invoices",
    ]);
    expect(pathLabel(all, "invoices")).toBe("Reports / 2026 / Invoices");
  });

  it("is empty for a folder that is not in the list", () => {
    expect(pathTo(all, "missing")).toEqual([]);
    expect(pathLabel(all, "missing")).toBeNull();
  });

  it("stops at a missing ancestor instead of failing", () => {
    expect(pathTo([y2026, invoices], "invoices").map((folder) => folder.id)).toEqual([
      "y2026",
      "invoices",
    ]);
  });

  it("terminates on a cycle", () => {
    const a = makeFolder({ id: "a", parent_id: "b" });
    const b = makeFolder({ id: "b", parent_id: "a" });
    expect(pathTo([a, b], "a").length).toBeLessThanOrEqual(2);
  });
});

describe("what a folder holds", () => {
  it("counts archived documents, which still pin the folder", () => {
    const folder = makeFolder({ document_count: 2, archived_document_count: 3, child_count: 1 });
    expect(contentsOf(folder)).toEqual({ documents: 5, subfolders: 1 });
    expect(isEmpty(folder)).toBe(false);
  });

  it("is empty only when there are no documents, archived ones, or subfolders", () => {
    expect(isEmpty(makeFolder())).toBe(true);
    expect(isEmpty(makeFolder({ archived_document_count: 1 }))).toBe(false);
    expect(isEmpty(makeFolder({ child_count: 1 }))).toBe(false);
  });
});
