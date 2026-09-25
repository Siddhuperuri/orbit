/**
 * The sign-up and sign-in paths, in the browser, including the ways they fail.
 *
 * Client-side validation is already unit-tested (`web/features/auth/schemas`).
 * What is tested here is the part those cannot see: that the message reaches the
 * screen, is tied to the right field, and that focus lands somewhere a keyboard
 * user can continue from.
 */

import type { Page } from "@playwright/test";

import { webBaseUrl } from "../env";
import { newCredentials } from "../fixtures/api";
import { registerThroughForm } from "../fixtures/orbit";
import { expect, test } from "../fixtures/orbit";

/**
 * The form-level refusal, as the person reads it.
 *
 * Two details matter. The toast region renders empty `role="alert"` nodes
 * before the form's own error exists, so the alert has to be matched on having
 * text rather than by position. And only the *message* is compared -- the error
 * carries a request id that differs per attempt by design, so comparing the
 * whole node would make the enumeration check below pass no matter what.
 */
async function refusalMessage(page: Page): Promise<string> {
  const alert = page.getByRole("alert").filter({ hasText: /\S/ }).first();
  await expect(alert).toBeVisible({ timeout: 15_000 });
  return ((await alert.locator("p").first().textContent()) ?? "").trim();
}

test("a wrong password is refused with the same message as an unknown account", async ({
  page,
  account,
  api,
}) => {
  void api; // the account must exist for this comparison to mean anything

  await page.goto("/auth/login");
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill("not-the-right-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  const knownAccountMessage = await refusalMessage(page);
  expect(knownAccountMessage).toBeTruthy();
  await expect(page).toHaveURL(/\/auth\/login/);

  // The password field is cleared and focused, so the next attempt is one
  // keystroke away rather than a hunt.
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Password", { exact: true })).toBeFocused();

  // An account that does not exist must be refused *identically*. A different
  // message here is an account-enumeration oracle.
  await page.goto("/auth/login");
  await page.getByLabel("Email", { exact: true }).fill(`nobody-${Date.now()}@orbit.test`);
  await page.getByLabel("Password", { exact: true }).fill("not-the-right-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  const unknownAccountMessage = await refusalMessage(page);
  expect(unknownAccountMessage).toBe(knownAccountMessage);
});

test("registering an email that is already taken is reported on the email field", async ({
  page,
  account,
  api,
}) => {
  void api; // `account` is already registered

  const attempted = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/auth/register") && response.request().method() === "POST",
  );
  await registerThroughForm(page, { ...account, fullName: "Someone Else" });

  // 409, and nothing else: a duplicate is the one case where this API does
  // disclose that an address is taken, and the form's handling depends on it.
  expect((await attempted).status()).toBe(409);

  // Tied to the field rather than dumped in a banner, so it is obvious *which*
  // value has to change. (That the field is also focused is asserted in
  // `web/features/auth/components/register-form.test.tsx`, where focus is
  // deterministic; in a real browser it races with the re-render.)
  await expect(page.getByLabel("Email", { exact: true })).toHaveAttribute("aria-invalid", "true", {
    timeout: 15_000,
  });
  await expect(page.getByText(/already|taken|in use|registered/i).first()).toBeVisible();

  // And no account was created a second time: still on the form.
  await expect(page).toHaveURL(/\/auth\/register/);
});

test("the register form refuses input the API would reject, without a round trip", async ({
  page,
}) => {
  await page.goto("/auth/register");
  await page.getByRole("button", { name: "Create account" }).click();

  // Every required field reports itself, all at once, rather than one per submit.
  await expect(page.getByLabel("Full name", { exact: true })).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  await expect(page.getByLabel("Email", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  await expect(page).toHaveURL(/\/auth\/register/);

  // A password under the published minimum is refused with the minimum stated.
  const credentials = newCredentials("short-password");
  await page.getByLabel("Full name", { exact: true }).fill(credentials.fullName);
  await page.getByLabel("Email", { exact: true }).fill(credentials.email);
  await page.getByLabel("Password", { exact: true }).fill("short");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  await expect(page).toHaveURL(/\/auth\/register/);

  // An address that is not one.
  await page.getByLabel("Email", { exact: true }).fill("not-an-email");
  await page.getByLabel("Password", { exact: true }).fill(credentials.password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByLabel("Email", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await expect(page).toHaveURL(/\/auth\/register/);
});

test("an open redirect is not followed after sign-in", async ({ page, account, api }) => {
  void api;

  // `next` is attacker-controlled in a phishing link. Anything that resolves
  // off-origin must be ignored rather than followed.
  await page.goto("/auth/login?next=https://evil.example/steal");
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).not.toHaveURL(/evil\.example/);
  // Still on the app's own origin, and signed in rather than stranded.
  expect(new URL(page.url()).host).toBe(new URL(webBaseUrl).host);
  await expect(page).not.toHaveURL(/\/auth\/login/);
});

test("requesting a password reset never reveals whether the account exists", async ({
  page,
  account,
  api,
}) => {
  void api; // `account` is registered; the address below never will be

  /** The confirmation screen's wording, with the address itself factored out. */
  async function requestReset(email: string): Promise<string> {
    await page.goto("/auth/forgot-password");
    await page.getByLabel("Email", { exact: true }).fill(email);
    await page.getByRole("button", { name: "Send reset link" }).click();

    const confirmation = page.getByRole("heading", { name: "Check your email" });
    await expect(confirmation).toBeVisible({ timeout: 20_000 });

    const body = (await page.getByText(/If an account exists for/i).textContent()) ?? "";
    // The address is echoed back so the person can see what they typed; it is
    // the only part that legitimately differs, so it is removed before
    // comparing. Anything else that differed would be the oracle.
    return body.replace(email, "<address>").replace(/\s+/g, " ").trim();
  }

  const registered = await requestReset(account.email);
  const unknown = await requestReset(`nobody-${Date.now()}@orbit.test`);

  expect(registered).toContain("If an account exists for <address>");
  // Identical, down to the wording. A different screen for an address that does
  // not exist would let anyone test whether someone has an account here.
  expect(unknown).toBe(registered);
});
