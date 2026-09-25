/**
 * Builds genuine PDF files in-process, with no PDF-authoring dependency.
 *
 * A TypeScript sibling of `backend/tests/fixtures/pdf.py`, and for the same
 * reason: committing a binary fixture hides the text the test then asserts on.
 * Here the document's words sit in the spec next to the search that must find
 * them, and a malformed variant is a one-line change rather than a hex editor.
 *
 * The output is a real PDF -- catalog, page tree, content stream with text
 * operators, font resource, byte-accurate cross-reference table -- which pypdf
 * on the server parses exactly as it would a file from a word processor. The
 * streams are left uncompressed, which is valid and keeps the bytes readable
 * when a test fails and someone opens the artifact.
 */

function escapeText(text: string): string {
  return text.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
}

function contentStream(lines: string[], fontSize = 11, leading = 14): Buffer {
  const parts = ["BT", `/F1 ${fontSize} Tf`, `${leading} TL`, "72 760 Td"];
  for (const line of lines) {
    // An empty line still advances, which is how a blank line between
    // paragraphs survives into the extracted text.
    parts.push(line ? `(${escapeText(line)}) Tj T*` : "T*");
  }
  parts.push("ET");
  return Buffer.from(parts.join("\n"), "latin1");
}

/** A PDF with one page per entry; each entry is that page's lines of text. */
export function buildPdf(pages: string[][], options: { title?: string } = {}): Buffer {
  const objects: Buffer[] = [];
  const add = (body: Buffer): number => objects.push(body);

  const catalog = add(Buffer.alloc(0)); // placeholder; needs the page-tree id
  const pageTree = add(Buffer.alloc(0));
  const font = add(
    Buffer.from(
      "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
      "latin1",
    ),
  );

  const pageIds: number[] = [];
  for (const lines of pages) {
    let contents = "";
    if (lines.length > 0) {
      const data = contentStream(lines);
      const stream = add(
        Buffer.concat([
          Buffer.from(`<< /Length ${data.length} >>\nstream\n`, "latin1"),
          data,
          Buffer.from("\nendstream", "latin1"),
        ]),
      );
      contents = ` /Contents ${stream} 0 R`;
    }
    pageIds.push(
      add(
        Buffer.from(
          `<< /Type /Page /Parent ${pageTree} 0 R /MediaBox [0 0 612 792]` +
            ` /Resources << /Font << /F1 ${font} 0 R >> >>${contents} >>`,
          "latin1",
        ),
      ),
    );
  }

  objects[pageTree - 1] = Buffer.from(
    `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}]` +
      ` /Count ${pageIds.length} >>`,
    "latin1",
  );
  objects[catalog - 1] = Buffer.from(`<< /Type /Catalog /Pages ${pageTree} 0 R >>`, "latin1");

  let info: number | undefined;
  if (options.title !== undefined) {
    info = add(Buffer.from(`<< /Title (${escapeText(options.title)}) >>`, "latin1"));
  }

  const header = Buffer.from("%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", "latin1");
  const chunks: Buffer[] = [header];
  let offset = header.length;
  const offsets: number[] = [];

  objects.forEach((body, index) => {
    offsets.push(offset);
    const piece = Buffer.concat([
      Buffer.from(`${index + 1} 0 obj\n`, "latin1"),
      body,
      Buffer.from("\nendobj\n", "latin1"),
    ]);
    chunks.push(piece);
    offset += piece.length;
  });

  const startxref = offset;
  const xref = [`xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`];
  for (const value of offsets) xref.push(`${String(value).padStart(10, "0")} 00000 n \n`);

  let trailer = `trailer\n<< /Size ${objects.length + 1} /Root ${catalog} 0 R`;
  if (info !== undefined) trailer += ` /Info ${info} 0 R`;
  trailer += ` >>\nstartxref\n${startxref}\n%%EOF\n`;

  chunks.push(Buffer.from(xref.join("") + trailer, "latin1"));
  return Buffer.concat(chunks);
}

/** Hard-wraps prose the way a PDF lays it out, line by line. */
export function paragraphLines(text: string, width = 80): string[] {
  const lines: string[] = [];
  let current = "";
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length > width && current) {
      lines.push(current);
      current = word;
    } else {
      current = candidate;
    }
  }
  if (current) lines.push(current);
  return lines;
}

/**
 * The document every journey test uploads.
 *
 * Its wording is load-bearing. Retrieval under the `fake` provider is feature-hashed
 * lexical overlap (ADR-0007), so a query matches a passage when they share words --
 * which makes the result of a search a property of this text and the query, not of a
 * model. Each section therefore owns a distinctive, unambiguous term, and the spec
 * searches for that term.
 */
export const handbookPages: string[][] = [
  [
    "NORTHWIND FIELD OPERATIONS HANDBOOK",
    "",
    ...paragraphLines(
      "This handbook governs field operations. It is issued to every operations " +
        "technician and supersedes all previous editions.",
    ),
    "",
    "SECTION 1. EQUIPMENT CALIBRATION",
    "",
    ...paragraphLines(
      "Every calibration of a flowmeter must be recorded in the calibration register " +
        "before the instrument returns to service. A flowmeter whose calibration has " +
        "lapsed is withdrawn from service immediately.",
    ),
  ],
  [
    "SECTION 2. SAFETY LOCKOUT",
    "",
    ...paragraphLines(
      "A lockout tag is applied by the technician performing the work and removed by " +
        "that same technician. No lockout tag may be removed by a supervisor without a " +
        "written lockout release authorisation.",
    ),
    "",
    "SECTION 3. INCIDENT REPORTING",
    "",
    ...paragraphLines(
      "An incident is reported within four hours. The incident report names the " +
        "equipment, the technician, and the corrective action taken.",
    ),
  ],
];

export const handbookTitle = "Northwind Field Operations Handbook";

/**
 * The file name it is uploaded under.
 *
 * ORBIT titles a document from its **sanitized filename**, not from the PDF's
 * internal `/Title` -- metadata inside an untrusted file is not a name to show
 * people. So an upload that passes no `title` is listed under this, and only a
 * caller that sends one explicitly gets {@link handbookTitle}.
 */
export const handbookFilename = "northwind-handbook.pdf";

/** A term that appears on page 1 only, and nowhere else in the corpus. */
export const page1Term = "flowmeter";
/** A term that appears on page 2 only. */
export const page2Term = "lockout";
