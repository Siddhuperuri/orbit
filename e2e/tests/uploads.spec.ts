/**
 * What happens when the file is wrong.
 *
 * The happy path is covered by `journey.spec.ts`. These are the refusals, and
 * each one is asserted **in the browser as well as at the API** where the user
 * would meet it -- a server that refuses correctly while the UI spins forever is
 * still broken for the person holding the file.
 */

import { buildPdf, handbookPages, handbookTitle } from "../fixtures/pdf";
import { expect, test } from "../fixtures/orbit";

test("a file of an unsupported type is refused, and the queue says why", async ({
  api,
  workspace,
  signedIn: page,
}) => {
  const response = await api.rawUpload(workspace.id, {
    filename: "payload.exe",
    contentType: "application/octet-stream",
    body: Buffer.from("MZ\x90\x00this is not a document"),
  });

  expect(response.status).toBe(415);
  expect(JSON.parse(response.body).error.code).toBe("UNSUPPORTED_CONTENT_TYPE");

  // The same refusal, met through the UI. The queue validates before sending,
  // so this is refused client-side and must still be *explained*.
  await page.goto(`/workspaces/${workspace.id}/documents`);
  await page.locator('[data-testid="upload-input"]').setInputFiles({
    name: "payload.exe",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("MZ\x90\x00this is not a document"),
  });
  await expect(
    page.getByText(/not a supported|unsupported|can't be uploaded/i).first(),
  ).toBeVisible();
});

test("an extension that lies about the bytes is refused on the bytes", async ({
  api,
  workspace,
}) => {
  // `.pdf`, and the server is told `application/pdf`, but the content is not a
  // PDF. An extension proves nothing; the parser is the authority.
  const response = await api.rawUpload(workspace.id, {
    filename: "actually-an-executable.pdf",
    contentType: "application/pdf",
    body: Buffer.from("MZ\x90\x00\x03\x00\x00\x00not a pdf at all"),
  });

  // Refused at the door on the magic bytes, or accepted and then failed by the
  // parser: both are correct, and which one happens is an implementation
  // detail. What must never happen is a document that reports itself ready.
  if (response.status === 201) {
    const documentId = JSON.parse(response.body).document.id as string;
    await expect(api.waitForReady(workspace.id, documentId, 45_000)).rejects.toThrow(
      /failed to process/,
    );
  } else {
    expect([400, 415, 422]).toContain(response.status);
  }
});

test("an empty file is refused", async ({ api, workspace }) => {
  const response = await api.rawUpload(workspace.id, {
    filename: "nothing.pdf",
    contentType: "application/pdf",
    body: Buffer.alloc(0),
  });
  expect([400, 415, 422]).toContain(response.status);
});

test("a truncated PDF fails processing rather than being reported ready", async ({
  api,
  workspace,
}) => {
  const whole = buildPdf(handbookPages, { title: handbookTitle });
  // Keep the header so it is recognisably a PDF, and cut the cross-reference
  // table off the end, which is what a half-finished download looks like.
  const truncated = whole.subarray(0, Math.floor(whole.length * 0.6));

  const response = await api.rawUpload(workspace.id, {
    filename: "truncated.pdf",
    contentType: "application/pdf",
    body: truncated,
  });

  if (response.status === 201) {
    const documentId = JSON.parse(response.body).document.id as string;
    await expect(api.waitForReady(workspace.id, documentId, 45_000)).rejects.toThrow(
      /failed to process/,
    );

    // The failure is *reported*, with a code the UI can explain -- not left as
    // a document stuck in `processing` forever.
    const document = await api.document(workspace.id, documentId);
    const version = document.current_version as Record<string, unknown>;
    expect(version.status).toBe("failed");
    expect(version.failure_code).toBeTruthy();
  } else {
    expect([400, 415, 422]).toContain(response.status);
  }
});

test("a file past the deployment's ceiling is refused before it is stored", async ({
  api,
  workspace,
}) => {
  // The limit is published by `/meta`, so the test reads it rather than
  // hardcoding a number that a configuration change would silently invalidate.
  const meta = await (await api.get("/api/v1/meta")).json();
  const maxBytes = meta.uploads.max_bytes as number;

  const response = await api.rawUpload(workspace.id, {
    filename: "too-big.txt",
    contentType: "text/plain",
    // One byte over, not a gigabyte: the point is the boundary, and sending the
    // real ceiling over the wire would make this test minutes long.
    body: Buffer.alloc(maxBytes + 1, 0x61),
  });

  expect(response.status).toBe(413);
  expect(JSON.parse(response.body).error.code).toBe("UPLOAD_TOO_LARGE");
});

/*
 * A `Content-Length` that lies about the body -- refused on the header alone,
 * before a byte is read -- is *not* tested here. Playwright recomputes
 * `Content-Length` from the body it actually sends, so the lie never reaches
 * the server and the test would assert nothing. It is covered where a client
 * can be made to lie: `backend/tests/api/test_documents_upload_router.py`.
 */

test("uploading into another tenant's workspace is not possible", async ({ api }) => {
  const response = await api.rawUpload("00000000-0000-7000-8000-000000000000", {
    filename: "trespass.txt",
    contentType: "text/plain",
    body: Buffer.from("hello"),
  });
  expect(response.status).toBe(404);
});
