"""Build real PDF files for tests, with no PDF-authoring dependency.

These are genuine PDFs -- a catalog, a page tree, content streams with text
operators, a font resource, and a byte-accurate cross-reference table -- that
pypdf (and any viewer) opens normally. Building them by hand, rather than
committing binaries, keeps each fixture's content visible in the test that uses
it and makes malformed variants (truncated, bad xref, garbage streams) a
one-line change.
"""

from __future__ import annotations

import io
import zlib
from collections.abc import Sequence


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream(lines: Sequence[str], *, font_size: int = 11, leading: int = 14) -> bytes:
    parts = ["BT", f"/F1 {font_size} Tf", f"{leading} TL", "72 760 Td"]
    for line in lines:
        # An empty line still advances, which is how a blank line between
        # paragraphs reaches the extracted text.
        parts.append(f"({_escape(line)}) Tj T*" if line else "T*")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def build_pdf(
    pages: Sequence[Sequence[str]],
    *,
    compress: bool = True,
    title: str | None = None,
) -> bytes:
    """A PDF with one page per entry; each entry is that page's lines of text.

    A page given as an empty sequence has no text layer at all -- what a scanned
    page looks like to a text extractor.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # placeholder, filled once the page tree id is known
    page_tree = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")

    page_ids: list[int] = []
    for lines in pages:
        if lines:
            raw = _content_stream(lines)
            data = zlib.compress(raw) if compress else raw
            filter_entry = b" /Filter /FlateDecode" if compress else b""
            stream = add(
                b"<< /Length %d%s >>\nstream\n" % (len(data), filter_entry) + data + b"\nendstream"
            )
            contents = b" /Contents %d 0 R" % stream
        else:
            contents = b""
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792]"
                b" /Resources << /Font << /F1 %d 0 R >> >>%s >>" % (page_tree, font, contents)
            )
        )

    kids = b" ".join(b"%d 0 R" % page for page in page_ids)
    objects[page_tree - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % page_tree

    info = None
    if title is not None:
        info = add(b"<< /Title (%s) >>" % _escape(title).encode("latin-1"))

    out = io.BytesIO()
    out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    trailer = b"<< /Size %d /Root %d 0 R" % (len(objects) + 1, catalog)
    if info is not None:
        trailer += b" /Info %d 0 R" % info
    out.write(b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref)
    return out.getvalue()


def encrypt_pdf(pdf: bytes, *, user_password: str, owner_password: str = "owner") -> bytes:
    """Encrypt with pypdf. An empty user password yields a PDF that opens
    without a password but carries an encryption dictionary -- the common
    "permissions only" case."""
    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415 -- test helper only

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(user_password=user_password, owner_password=owner_password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def paragraph_lines(text: str, *, width: int = 80) -> list[str]:
    """Hard-wrap prose the way a PDF lays it out, line by line."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
