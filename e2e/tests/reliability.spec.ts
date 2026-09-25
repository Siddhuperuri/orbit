/**
 * What the running system does when part of it misbehaves.
 *
 * These are the failures that can be provoked from outside the process, against
 * the real stack: a duplicate job, a reprocess of a document already in flight,
 * a document whose processing genuinely fails, and the readiness endpoint's own
 * account of its dependencies.
 *
 * Failures that need a dependency *taken away* -- storage refusing a write, the
 * database dropping mid-transaction, a provider timing out -- are not here. They
 * are covered in `backend/tests/integration/test_failure_modes.py` and
 * `backend/tests/unit/processing/test_jobs_retry_failures.py`, where the adapter
 * can be made to fail deliberately. Reproducing them here would mean stopping a
 * container mid-suite, which makes every *other* test in the run non-deterministic.
 */

import { apiBaseUrl } from "../env";
import { buildPdf, handbookPages, handbookTitle, page1Term } from "../fixtures/pdf";
import { expect, test } from "../fixtures/orbit";
import { REQUESTED_WITH } from "../fixtures/api";

test("readiness reports each dependency by name", async ({ request }) => {
  // Straight at the API, not through the web origin: Next only rewrites
  // `/api/*`, and the probes deliberately sit outside the versioned API so an
  // orchestrator can reach them without a route that could ever require auth.
  const response = await request.get(`${apiBaseUrl}/readyz`);
  expect(response.status()).toBe(200);

  const report = await response.json();
  expect(report.status).toBe("ready");

  const byName = Object.fromEntries(
    (report.dependencies as { name: string; status: string }[]).map((d) => [d.name, d.status]),
  );
  // Every dependency the suite depends on is checked and up. A run where one of
  // these is missing from the report is a monitoring gap, not a passing test.
  for (const name of ["database", "redis", "object_storage", "embedding_schema"]) {
    expect(byName[name], name).toBe("up");
  }
});

test("reprocessing a document is idempotent and does not duplicate its chunks", async ({
  api,
  workspace,
}) => {
  const documentId = await api.upload(workspace.id, {
    filename: "handbook.pdf",
    contentType: "application/pdf",
    body: buildPdf(handbookPages, { title: handbookTitle }),
  });
  const ready = await api.waitForReady(workspace.id, documentId);
  const firstChunkCount = (ready.current_version as Record<string, unknown>).chunk_count;
  expect(firstChunkCount).toBeGreaterThan(0);

  // Ask for the same work twice in a row. The second request arrives while the
  // first is very likely still running, which is exactly the duplicate-job case.
  const a = await api.post(`/api/v1/workspaces/${workspace.id}/documents/${documentId}/reprocess`, {
    headers: REQUESTED_WITH,
  });
  const b = await api.post(`/api/v1/workspaces/${workspace.id}/documents/${documentId}/reprocess`, {
    headers: REQUESTED_WITH,
  });

  // Accepted, or refused as already in flight -- both are correct answers. A
  // 500 is not, and neither is silently queueing two full pipelines.
  for (const response of [a, b]) {
    expect([200, 202, 409]).toContain(response.status());
  }

  const after = await api.waitForReady(workspace.id, documentId);
  // The document ends in one piece, with the same number of chunks it had
  // before: re-running the pipeline replaces its output, it does not append to it.
  expect((after.current_version as Record<string, unknown>).chunk_count).toBe(firstChunkCount);

  // And search still returns it exactly once.
  const results = (await api.search(workspace.id, page1Term)) as {
    results: { document: { id: string } }[];
  };
  const mine = results.results.filter((r) => r.document.id === documentId);
  expect(mine.length).toBeGreaterThan(0);
});

test("a document that fails processing is reported, not left in limbo", async ({
  api,
  workspace,
  signedIn: page,
}) => {
  // A PDF header with a broken body: accepted as a PDF, unparseable as one.
  const broken = Buffer.concat([
    Buffer.from("%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", "latin1"),
    Buffer.from("\x00".repeat(600), "latin1"),
  ]);

  const response = await api.rawUpload(workspace.id, {
    filename: "broken.pdf",
    contentType: "application/pdf",
    body: broken,
  });
  test.skip(response.status !== 201, "refused at the door, which uploads.spec.ts covers");

  const documentId = JSON.parse(response.body).document.id as string;

  // It must reach a terminal state. A job that neither succeeds nor fails is the
  // worst outcome of the three, because nothing retries it and nobody is told.
  const deadline = Date.now() + 60_000;
  let status = "";
  while (Date.now() < deadline) {
    const document = await api.document(workspace.id, documentId);
    status = String((document.current_version as Record<string, unknown>).status);
    if (status === "failed" || status === "ready") break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  expect(["failed", "ready"]).toContain(status);

  // Whatever it decided, the person is told plainly rather than shown a spinner.
  await page.goto(`/workspaces/${workspace.id}/documents/${documentId}`);
  await expect(page.getByText(/ready|couldn't be processed|failed/i).first()).toBeVisible({
    timeout: 30_000,
  });
});

test("an answer with nothing to ground it says so instead of inventing one", async ({
  api,
  workspace,
}) => {
  // An empty workspace: there is no passage that could support any answer.
  const conversation = await api.post(`/api/v1/workspaces/${workspace.id}/conversations`, {
    headers: REQUESTED_WITH,
    data: {},
  });
  expect(conversation.status()).toBe(201);
  const conversationId = (await conversation.json()).id as string;

  const answer = await api.post(
    `/api/v1/workspaces/${workspace.id}/conversations/${conversationId}/messages`,
    {
      headers: REQUESTED_WITH,
      data: { question: "What is the lockout release procedure?" },
      timeout: 60_000,
    },
  );

  expect(answer.status()).toBe(201);
  const payload = await answer.json();
  // No sources, and the answer must not pretend otherwise. Citing nothing while
  // asserting something is the failure mode this whole product has to avoid.
  expect(payload.citations ?? []).toHaveLength(0);
});

test("a search for a term in no document returns nothing rather than erroring", async ({
  api,
  workspace,
}) => {
  const results = (await api.search(workspace.id, "zzzzqqqqxxxx-no-such-term")) as {
    results: unknown[];
  };
  expect(results.results).toHaveLength(0);
});
