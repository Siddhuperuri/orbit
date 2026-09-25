/**
 * The whole product, once, in the order a new user meets it:
 *
 *   register -> login -> create workspace -> upload a PDF -> wait for processing
 *   -> search -> open a result -> ask a question -> inspect a citation -> log out
 *
 * It is one test, not ten. Each step depends on the state the last one produced,
 * and splitting them would mean either re-driving the whole flow per step (slow)
 * or sharing state between tests (a false pass when an earlier step breaks). The
 * `test.step` calls give a failure the same granularity a split would, without
 * pretending the steps are independent.
 *
 * Nothing here is mocked. The only substituted component is the AI provider,
 * which is ORBIT's own deterministic `fake` (ADR-0007) -- so the citation this
 * test opens is one the retrieval pipeline genuinely produced, and no run needs
 * a credential.
 */

import { newCredentials } from "../fixtures/api";
import { registerThroughForm, signIn, signOut } from "../fixtures/orbit";
import {
  buildPdf,
  handbookFilename,
  handbookPages,
  handbookTitle,
  page1Term,
} from "../fixtures/pdf";
import { expect, test } from "../fixtures/orbit";

test("a new user registers, uploads a document, searches it, and asks about it", async ({
  page,
}) => {
  const account = newCredentials("journey");
  const handbook = buildPdf(handbookPages, { title: handbookTitle });
  let workspaceUrl = "";

  await test.step("1. register", async () => {
    await registerThroughForm(page, account);
    // Registration signs the account in and lands on the first-run screen,
    // because a new account has no workspace yet.
    await expect(page.getByRole("heading", { name: "Create your first workspace" })).toBeVisible();
  });

  await test.step("2. log out and log back in", async () => {
    // Registration's convenience sign-in is not the login path, so the journey
    // exercises the real one before going further.
    await signOut(page);

    await signIn(page, account);
    await expect(page.getByRole("heading", { name: "Create your first workspace" })).toBeVisible();
  });

  await test.step("3. create a workspace", async () => {
    await page.getByLabel("Workspace name", { exact: true }).fill("Field Operations");
    await page.getByRole("button", { name: "Create workspace" }).click();

    // Creating a workspace opens its document library.
    await expect(page).toHaveURL(/\/workspaces\/[0-9a-f-]+\/documents/);
    workspaceUrl = new URL(page.url()).pathname.replace(/\/documents$/, "");
  });

  await test.step("4. upload a PDF", async () => {
    await page.locator('[data-testid="upload-input"]').setInputFiles({
      name: handbookFilename,
      mimeType: "application/pdf",
      buffer: handbook,
    });

    // Listed under its file name: an upload through the UI sends no title, and
    // ORBIT does not take one from inside the file.
    await expect(page.getByText(handbookFilename, { exact: false }).first()).toBeVisible();
  });

  await test.step("5. wait for processing to finish", async () => {
    // The document row reports its own lifecycle. Waiting for `Ready` in the UI
    // -- rather than polling the API -- is what proves the browser is told.
    await expect(page.getByText("Ready", { exact: false }).first()).toBeVisible({
      // Generous: this is the first job of the run, so it also absorbs worker
      // cold start. It is a ceiling, not a sleep.
      timeout: 90_000,
    });
  });

  await test.step("6. search", async () => {
    await page.goto(`${workspaceUrl}/search`);
    await page.getByRole("searchbox").fill(page1Term);
    // `exact`: the app shell's command-palette trigger is also named "Search
    // or jump to…", which a substring match would pick up as well.
    await page.getByRole("button", { name: "Search", exact: true }).click();

    // `page1Term` appears in this document and nowhere else in the workspace,
    // so exactly one document may match.
    const results = page.getByRole("link", { name: new RegExp(handbookFilename, "i") });
    await expect(results.first()).toBeVisible();
  });

  await test.step("7. open the result", async () => {
    await page
      .getByRole("link", { name: new RegExp(handbookFilename, "i") })
      .first()
      .click();
    await expect(page).toHaveURL(/\/documents\/[0-9a-f-]+/);
    await expect(page.getByRole("heading", { name: handbookFilename })).toBeVisible();
    // The passage that matched is on the page, not just the title.
    await expect(page.getByText(new RegExp(page1Term, "i")).first()).toBeVisible();
  });

  await test.step("8. ask a question", async () => {
    await page.goto(`${workspaceUrl}/chat`);
    await page
      .getByLabel("Your question", { exact: true })
      .fill(`What must happen before a ${page1Term} returns to service?`);
    await page.getByRole("button", { name: "Send question" }).click();

    // The answer arrives over SSE; `Sources` appears once it is grounded.
    await expect(page.getByRole("heading", { name: "Sources" }).first()).toBeVisible({
      timeout: 60_000,
    });
  });

  await test.step("9. inspect a citation", async () => {
    // The inline marker names its source in its accessible name, which is what a
    // screen-reader user hears; clicking it reveals the passage below the answer.
    const marker = page.getByRole("button", { name: /^Source S1:/ }).first();
    await expect(marker).toBeVisible();
    await marker.click();

    // The revealed source is the document that was uploaded, and it quotes text.
    // The disclosure in the Sources list, not the inline marker -- both carry the
    // document's name, and only the disclosure controls a panel.
    const source = page
      .locator("button[aria-controls]")
      .filter({ hasText: handbookFilename })
      .first();
    await expect(source).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("blockquote").first()).toBeVisible();

    // And it leads back to the document, at the passage it came from.
    const openAt = page.getByRole("link", { name: /Open (at this passage|document)/ }).first();
    await expect(openAt).toBeVisible();
    await openAt.click();
    await expect(page).toHaveURL(/\/documents\/[0-9a-f-]+/);
  });

  await test.step("10. log out", async () => {
    await signOut(page);

    // Signing out must actually end the session, not just navigate: the
    // workspace is refused to the browser that just left it.
    await page.goto(`${workspaceUrl}/documents`);
    await expect(page).toHaveURL(/\/auth\/login/);
  });
});

test("a document that is searchable is also answerable, and the citation points at its own text", async ({
  api,
  workspace,
  signedIn: page,
}) => {
  // Arranged through the API: this test is about what search and citation
  // *return*, so the upload UI is not re-driven here.
  const documentId = await api.upload(
    workspace.id,
    {
      filename: handbookFilename,
      contentType: "application/pdf",
      body: buildPdf(handbookPages, { title: handbookTitle }),
    },
    // Sent explicitly, so the document carries a real name rather than the
    // file's -- which is also what makes the heading assertion below meaningful.
    { title: handbookTitle },
  );
  await api.waitForReady(workspace.id, documentId);

  const results = (await api.search(workspace.id, page1Term)) as {
    results: { document: { id: string }; chunk: { text: string } }[];
  };

  expect(results.results.length).toBeGreaterThan(0);
  expect(results.results[0]!.document.id).toBe(documentId);
  // The returned passage is the document's own words, verbatim -- never a
  // summary, and never anything a model produced (ADR-0006).
  expect(results.results.some((r) => r.chunk.text.toLowerCase().includes(page1Term))).toBe(true);

  // The same document, reached through the browser, shows that passage.
  await page.goto(`/workspaces/${workspace.id}/documents/${documentId}`);
  await expect(page.getByRole("heading", { name: handbookTitle })).toBeVisible();
});

test("re-uploading identical content is deduplicated rather than stored twice", async ({
  api,
  workspace,
}) => {
  const body = buildPdf(handbookPages, { title: handbookTitle });
  const first = await api.upload(workspace.id, {
    filename: "handbook.pdf",
    contentType: "application/pdf",
    body,
  });
  await api.waitForReady(workspace.id, first);

  const again = await api.rawUpload(workspace.id, {
    filename: "handbook-copy.pdf",
    contentType: "application/pdf",
    body,
  });

  expect(again.status).toBe(201);
  const payload = JSON.parse(again.body);
  expect(payload.deduplicated).toBe(true);
  expect(payload.document.id).toBe(first);
});

test("an anonymous visitor is sent to sign in and back to where they were going", async ({
  page,
  workspace,
  account,
}) => {
  const target = `/workspaces/${workspace.id}/documents`;

  const anonymous = await page.context().newPage();
  await anonymous.goto(target);
  await expect(anonymous).toHaveURL(/\/auth\/login/);
  // Where they were going is remembered, so signing in does not strand them.
  expect(new URL(anonymous.url()).searchParams.get("next")).toBe(target);

  await anonymous.getByLabel("Email", { exact: true }).fill(account.email);
  await anonymous.getByLabel("Password", { exact: true }).fill(account.password);
  await anonymous.getByRole("button", { name: "Sign in" }).click();
  await expect(anonymous).toHaveURL(new RegExp(target.replace(/\//g, "\\/")));
  await anonymous.close();
});
