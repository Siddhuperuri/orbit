/**
 * The test fixtures every spec builds on.
 *
 * Each test gets an account and a workspace of its own. That is not only
 * hygiene: it is the same tenancy boundary the product enforces, so tests are
 * isolated from each other by the mechanism `security.spec.ts` verifies, and a
 * leak between tests would be the product's bug rather than the suite's.
 *
 * The browser page and the API client share one browser context, and therefore
 * one cookie jar -- see the note in `api.ts` on why a standalone request
 * context cannot hold this application's `Secure` session cookies.
 */

import { test as base, expect, type BrowserContext, type Page } from "@playwright/test";

import { webBaseUrl } from "../env";
import { type Credentials, newCredentials, OrbitApi } from "./api";

interface OrbitFixtures {
  /** Credentials for this test's account. */
  account: Credentials;
  /** A browser context whose jar already holds a session for `account`. */
  authed: BrowserContext;
  /** An API client on that same session. */
  api: OrbitApi;
  /** A page on that same session. */
  signedIn: Page;
  /** A workspace owned by `account`. */
  workspace: { id: string; name: string };
}

export const test = base.extend<OrbitFixtures>({
  account: async ({}, use, testInfo) => {
    await use(newCredentials(testInfo.title));
  },

  authed: async ({ browser, account }, use) => {
    const context = await browser.newContext({ baseURL: webBaseUrl });
    const api = new OrbitApi(context);
    await api.register(account);
    await api.login(account);
    await use(context);
    await context.close();
  },

  api: async ({ authed }, use) => {
    // Not disposed here: the context owns it, and `authed` closes that.
    await use(new OrbitApi(authed));
  },

  signedIn: async ({ authed }, use) => {
    const page = await authed.newPage();
    await use(page);
    await page.close();
  },

  workspace: async ({ api }, use, testInfo) => {
    const name = `WS ${testInfo.title}`.slice(0, 120);
    const id = await api.createWorkspace(name);
    await use({ id, name });
  },
});

export { expect };

/** Signs in through the login form and waits for the app to take over. */
export async function signIn(page: Page, credentials: Credentials): Promise<void> {
  await page.goto("/auth/login");
  await page.getByLabel("Email", { exact: true }).fill(credentials.email);
  await page.getByLabel("Password", { exact: true }).fill(credentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/auth\/login/);
}

/**
 * Signs out from the account screen and waits to land back on sign-in.
 *
 * Two waits, each earning its place. `page.goto` resolves when the document has
 * loaded, which on a Next app is *before* React has hydrated; a click that
 * lands on a not-yet-hydrated button hits an element with no handler attached,
 * and nothing happens at all. And waiting for the logout *response* rather than
 * only for the URL splits the two ways this can fail: a click that never
 * reached the server, and a server round trip that did not redirect.
 */
export async function signOut(page: Page): Promise<void> {
  await page.goto("/account");
  const button = page.getByRole("button", { name: "Sign out" });
  await expect(button).toBeEnabled();
  await page.waitForLoadState("networkidle");

  const loggedOut = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/auth/logout") && response.request().method() === "POST",
    { timeout: 20_000 },
  );
  await button.click();
  expect((await loggedOut).status()).toBe(204);

  await expect(page).toHaveURL(/\/auth\/login/);
}

/** Registers through the form. The app signs the new account in for them. */
export async function registerThroughForm(page: Page, credentials: Credentials): Promise<void> {
  await page.goto("/auth/register");
  await page.getByLabel("Full name", { exact: true }).fill(credentials.fullName);
  await page.getByLabel("Email", { exact: true }).fill(credentials.email);
  await page.getByLabel("Password", { exact: true }).fill(credentials.password);
  await page.getByRole("button", { name: "Create account" }).click();
}
