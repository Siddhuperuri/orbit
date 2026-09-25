"""Upload validation: filenames, extensions, magic-byte sniffing, streaming
size enforcement -- all offline, no storage or database involved.

These pin the decisions ADR-0011 makes about hostile input, each as a
reproducible input/output pair rather than a description:

* a filename is sanitized, never trusted as a path
* the extension gates which content type is even possible
* the *content* -- not the extension, not a declared header -- decides
  whether the upload proceeds
* size is enforced the instant it is crossed, mid-stream
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from orbit.core.uploads import (
    ContentTypeRejectedError,
    FilenameRejectedError,
    UploadSizeExceededError,
    expected_content_type_for_extension,
    extension_of,
    prepare_upload,
    sanitize_filename,
    sniff_and_validate,
)
from tests.conftest import build_settings

_PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\nrest of a pdf..."
_MARKDOWN_BYTES = b"# Title\n\nSome *markdown* text.\n"
_TEXT_BYTES = b"Just plain text.\n"


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_a_normal_filename_passes_through(self) -> None:
        assert sanitize_filename("report.pdf") == "report.pdf"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32\\config", "config"),
            ("/etc/shadow", "shadow"),
            ("C:\\Users\\ada\\report.pdf", "report.pdf"),
        ],
    )
    def test_path_components_are_discarded_not_escaped(self, raw: str, expected: str) -> None:
        """Only the final path segment survives. This is defense in depth for
        `Content-Disposition`, not the path-traversal defence -- the storage
        key never contains any part of a filename, sanitized or not."""
        assert sanitize_filename(raw) == expected

    def test_control_characters_are_stripped(self) -> None:
        assert sanitize_filename("report\r\n.pdf") == "report.pdf"

    def test_a_crlf_injection_attempt_cannot_survive_into_a_header(self) -> None:
        result = sanitize_filename('evil\r\nSet-Cookie: session=stolen".pdf')
        assert "\r" not in result
        assert "\n" not in result

    @pytest.mark.parametrize("raw", ["", "   ", ".", "..", "\r\n\t"])
    def test_nothing_left_after_cleanup_is_rejected(self, raw: str) -> None:
        with pytest.raises(FilenameRejectedError):
            sanitize_filename(raw)

    def test_an_overlong_filename_is_truncated_preserving_the_extension(self) -> None:
        result = sanitize_filename("a" * 500 + ".pdf")
        assert len(result.encode("utf-8")) <= 255
        assert result.endswith(".pdf")

    def test_unicode_is_normalized_to_nfc(self) -> None:
        # "e" + combining acute accent (NFD) normalizes to "é" (NFC, one
        # codepoint) -- two visually identical filenames must compare equal.
        decomposed = "caf\u0065\u0301.txt"
        assert sanitize_filename(decomposed) == "café.txt"


class TestExtensionOf:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("report.pdf", ".pdf"),
            ("README.MD", ".md"),
            ("archive.tar.gz", ".gz"),
            ("no-extension", ""),
            # A leading-dot filename has exactly one "." and nothing before
            # it; `extension_of` does not special-case Unix dotfile
            # convention, so the whole name is the extension -- a degenerate
            # but harmless case, since `expected_content_type_for_extension`
            # simply rejects it if it is not in the allowlist.
            (".hidden", ".hidden"),
        ],
    )
    def test_extracts_the_lowercased_extension(self, filename: str, expected: str) -> None:
        assert extension_of(filename) == expected


class TestExpectedContentTypeForExtension:
    @pytest.mark.parametrize(
        ("extension", "content_type"),
        [
            (".pdf", "application/pdf"),
            (".md", "text/markdown"),
            (".markdown", "text/markdown"),
            (".txt", "text/plain"),
        ],
    )
    def test_supported_extensions_map_to_their_content_type(
        self, extension: str, content_type: str
    ) -> None:
        assert expected_content_type_for_extension(extension) == content_type

    @pytest.mark.parametrize("extension", [".exe", ".sh", ".docx", ".jpg", "", ".pdf.exe"])
    def test_unsupported_extensions_are_rejected(self, extension: str) -> None:
        with pytest.raises(ContentTypeRejectedError):
            expected_content_type_for_extension(extension)


# ---------------------------------------------------------------------------
# Magic-byte sniffing -- the content decides, never the extension alone and
# never a declared header.
# ---------------------------------------------------------------------------


class TestSniffAndValidate:
    def test_a_real_pdf_header_is_accepted(self) -> None:
        assert sniff_and_validate(_PDF_BYTES[:64], extension=".pdf") == "application/pdf"

    def test_text_claiming_to_be_a_pdf_is_rejected(self) -> None:
        """Spoofed MIME, the extension-and-content-disagree case: a `.pdf`
        extension over content with no PDF signature."""
        with pytest.raises(ContentTypeRejectedError):
            sniff_and_validate(_TEXT_BYTES, extension=".pdf")

    def test_a_truncated_or_corrupt_pdf_is_rejected(self) -> None:
        """Malformed file: looks like it should be a PDF, is missing the
        signature that would prove it."""
        with pytest.raises(ContentTypeRejectedError):
            sniff_and_validate(b"this is not actually a pdf", extension=".pdf")

    def test_plain_text_is_accepted_as_txt(self) -> None:
        assert sniff_and_validate(_TEXT_BYTES, extension=".txt") == "text/plain"

    def test_markdown_text_is_accepted_as_markdown(self) -> None:
        assert sniff_and_validate(_MARKDOWN_BYTES, extension=".md") == "text/markdown"

    def test_binary_content_disguised_as_txt_is_rejected(self) -> None:
        """Malformed / spoofed: binary garbage (a NUL byte) claiming `.txt`."""
        with pytest.raises(ContentTypeRejectedError):
            sniff_and_validate(b"\x00\x01\x02binary garbage", extension=".txt")

    def test_a_pdf_disguised_as_txt_is_rejected(self) -> None:
        """The content is genuinely a PDF, but the extension says text --
        rejected because it fails the "is this text" check, not because
        anything looked specifically like a PDF."""
        with pytest.raises(ContentTypeRejectedError):
            sniff_and_validate(_PDF_BYTES, extension=".txt")

    def test_invalid_utf8_disguised_as_txt_is_rejected(self) -> None:
        with pytest.raises(ContentTypeRejectedError):
            sniff_and_validate(b"\xff\xfe\x00\x01not valid utf-8", extension=".txt")


# ---------------------------------------------------------------------------
# prepare_upload: the full pipeline, streaming.
# ---------------------------------------------------------------------------


class TestPrepareUpload:
    async def test_a_valid_pdf_is_accepted_and_hashed(self) -> None:
        settings = build_settings()
        prepared = await prepare_upload("report.pdf", _stream(_PDF_BYTES), settings=settings)
        assert prepared.stats.content_type == "application/pdf"

        collected = bytearray()
        async for chunk in prepared.stream:
            collected += chunk
        assert bytes(collected) == _PDF_BYTES
        assert prepared.stats.byte_size == len(_PDF_BYTES)
        assert prepared.stats.content_sha256 is not None
        assert len(prepared.stats.content_sha256) == 64

    async def test_a_valid_markdown_file_is_accepted(self) -> None:
        settings = build_settings()
        prepared = await prepare_upload("notes.md", _stream(_MARKDOWN_BYTES), settings=settings)
        assert prepared.stats.content_type == "text/markdown"

    async def test_a_valid_text_file_is_accepted(self) -> None:
        settings = build_settings()
        prepared = await prepare_upload("notes.txt", _stream(_TEXT_BYTES), settings=settings)
        assert prepared.stats.content_type == "text/plain"

    async def test_an_unsupported_extension_is_rejected_before_the_stream_is_touched(
        self,
    ) -> None:
        settings = build_settings()
        touched = False

        async def _tracking_stream() -> AsyncIterator[bytes]:
            nonlocal touched
            touched = True
            yield b"unused"

        with pytest.raises(ContentTypeRejectedError):
            await prepare_upload("malware.exe", _tracking_stream(), settings=settings)
        assert not touched, "an unsupported extension must be rejected without reading the body"

    async def test_invalid_mime_content_is_rejected(self) -> None:
        settings = build_settings()
        with pytest.raises(ContentTypeRejectedError):
            await prepare_upload("fake.pdf", _stream(_TEXT_BYTES), settings=settings)

    async def test_an_empty_file_is_rejected(self) -> None:
        settings = build_settings()
        with pytest.raises(ContentTypeRejectedError):
            await prepare_upload("empty.txt", _stream(b""), settings=settings)

    async def test_a_filename_with_no_usable_name_is_rejected(self) -> None:
        """A path ending in a separator leaves nothing after the last
        component -- the degenerate case `sanitize_filename` rejects, as
        distinct from a path merely *containing* separators (which it
        cleans up, see `TestSanitizeFilename`)."""
        settings = build_settings()
        with pytest.raises(FilenameRejectedError):
            await prepare_upload("../../etc/passwd/", _stream(_PDF_BYTES), settings=settings)

    async def test_an_oversized_file_is_rejected_mid_stream_not_after(self) -> None:
        """The size limit is enforced the instant it is crossed: the
        generator raises partway through, before every chunk has been
        consumed -- proving the check runs live, not as a final tally."""
        # 1024 is `max_upload_bytes`'s configured floor (Settings enforces
        # ge=1024), so the smallest limit a test can configure at all.
        settings = build_settings(max_upload_bytes=1024)
        chunks_consumed = 0

        async def _oversized_stream() -> AsyncIterator[bytes]:
            nonlocal chunks_consumed
            for _ in range(10):
                chunks_consumed += 1
                yield b"x" * 1024  # 10 * 1024 > the 1024-byte limit

        prepared = await prepare_upload("big.txt", _oversized_stream(), settings=settings)

        with pytest.raises(UploadSizeExceededError):
            async for _ in prepared.stream:
                pass

        # The oversized condition trips on the second chunk (first chunk is
        # exactly at the limit, second pushes it over) -- not all ten.
        assert chunks_consumed < 10

    async def test_a_file_at_exactly_the_limit_is_accepted(self) -> None:
        settings = build_settings(max_upload_bytes=1024)
        body = b"x" * 1024
        prepared = await prepare_upload("notes.txt", _stream(body), settings=settings)
        collected = bytearray()
        async for chunk in prepared.stream:
            collected += chunk
        assert prepared.stats.byte_size == 1024

    async def test_a_file_one_byte_over_the_limit_is_rejected(self) -> None:
        settings = build_settings(max_upload_bytes=1024)
        body = b"x" * 1025
        prepared = await prepare_upload("notes.txt", _stream(body), settings=settings)
        with pytest.raises(UploadSizeExceededError):
            async for _ in prepared.stream:
                pass

    async def test_declared_content_type_never_influences_the_decision(self) -> None:
        """There is no parameter for a declared `Content-Type` anywhere in
        this pipeline -- this test exists to make that contract explicit: two
        calls differing only in what a client might have claimed produce an
        identical outcome, because nothing here ever reads such a claim."""
        settings = build_settings()
        first = await prepare_upload("report.pdf", _stream(_PDF_BYTES), settings=settings)
        second = await prepare_upload("report.pdf", _stream(_PDF_BYTES), settings=settings)
        assert first.stats.content_type == second.stats.content_type == "application/pdf"
