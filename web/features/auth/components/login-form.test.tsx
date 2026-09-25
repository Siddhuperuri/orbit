import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { authApi } from "@/features/auth/api/endpoints";
import { LoginForm } from "@/features/auth/components/login-form";
import { ApiError, ErrorCode, type FieldError } from "@/lib/api/errors";
import { navigation } from "@/test/navigation";
import { makeQueryClient, makeUser } from "@/test/render";

/**
 * The sign-in form's behaviour, not its schema -- `auth-schemas.test.ts` owns the
 * rules. What is asserted here is what the person sees and where the keyboard
 * goes, because those are the parts a schema cannot describe and a passing
 * validator can still get wrong.
 *
 * Rendered without the workspace providers it never uses: an auth screen is
 * reached with no session, so wrapping it in one would test a state it cannot
 * be in.
 */

function renderLoginForm(url = "/auth/login") {
  navigation.reset(url);
  const client = makeQueryClient();
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return { user: userEvent.setup(), ...render(<LoginForm />, { wrapper: Wrapper }) };
}

type LoginResult = Awaited<ReturnType<typeof authApi.login>>;

function apiError(
  code: string,
  message: string,
  status: number,
  details: FieldError[] = [],
): ApiError {
  return new ApiError({ code, message, status, requestId: "01TESTREQUESTID0000000000", details });
}

async function fillAndSubmit(
  user: ReturnType<typeof userEvent.setup>,
  { email = "ada@example.com", password = "a-long-enough-passphrase" } = {},
) {
  await user.type(screen.getByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginForm", () => {
  it("does not call the API until the fields are valid", async () => {
    const login = vi.spyOn(authApi, "login");
    const { user } = renderLoginForm();

    await user.click(screen.getByRole("button", { name: "Sign in" }));

    // A round trip to be told what the browser already knew wastes the person's
    // time and spends a request against the rate limiter for nothing.
    expect(login).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    });
  });

  it("shows the server's message and clears the password after a wrong one", async () => {
    vi.spyOn(authApi, "login").mockRejectedValue(
      apiError(ErrorCode.InvalidCredentials, "Email or password is incorrect.", 401),
    );
    const { user } = renderLoginForm();

    await fillAndSubmit(user, { password: "wrong-password" });

    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");

    // Cleared and focused: the next attempt is one keystroke away rather than a
    // hunt, and a known-wrong value is not left sitting on screen.
    await waitFor(() => {
      expect(screen.getByLabelText("Password")).toHaveValue("");
      expect(screen.getByLabelText("Password")).toHaveFocus();
    });
    // The email is kept -- it was almost certainly right.
    expect(screen.getByLabelText("Email")).toHaveValue("ada@example.com");
  });

  it("attaches a server field error to the field it belongs to", async () => {
    vi.spyOn(authApi, "login").mockRejectedValue(
      apiError(ErrorCode.Validation, "Validation failed.", 422, [
        { field: "body.email", message: "This address is not allowed." },
      ]),
    );
    const { user } = renderLoginForm();

    await fillAndSubmit(user);

    // The backend's `body.` prefix is stripped so the message lands on the
    // field the form actually has.
    expect(await screen.findByText("This address is not allowed.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    });
  });

  it("reports a network failure as a form-level error rather than blaming a field", async () => {
    vi.spyOn(authApi, "login").mockRejectedValue(
      apiError(ErrorCode.NetworkUnreachable, "Couldn't reach the server.", 0),
    );
    const { user } = renderLoginForm();

    await fillAndSubmit(user);

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // Nothing the person typed is marked invalid for an outage.
    expect(screen.getByLabelText("Email")).not.toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Password")).not.toHaveAttribute("aria-invalid", "true");
  });

  it("shows a pending state while the request is in flight", async () => {
    let release: (value: LoginResult) => void = () => undefined;
    vi.spyOn(authApi, "login").mockReturnValue(
      new Promise<LoginResult>((resolve) => {
        release = resolve;
      }),
    );
    const { user } = renderLoginForm();

    await fillAndSubmit(user);

    // The label changes, so the state is legible to a screen reader as well as
    // to someone watching a spinner.
    expect(await screen.findByRole("button", { name: "Signing in" })).toBeInTheDocument();

    release({ user: makeUser() } as LoginResult);
    await waitFor(() => expect(navigation.router.replace).toHaveBeenCalled());
  });

  it("follows a safe next path after signing in", async () => {
    vi.spyOn(authApi, "login").mockResolvedValue({ user: makeUser() } as LoginResult);

    const { user } = renderLoginForm("/auth/login?next=%2Fworkspaces%2Fws-1%2Fdocuments");
    await fillAndSubmit(user);

    await waitFor(() => {
      expect(navigation.router.replace).toHaveBeenCalledWith("/workspaces/ws-1/documents");
    });
  });

  it("refuses to follow an off-origin next path", async () => {
    vi.spyOn(authApi, "login").mockResolvedValue({ user: makeUser() } as LoginResult);

    // An attacker-supplied `next` in a phishing link. Following it would be an
    // open redirect on a domain the person trusts.
    const { user } = renderLoginForm("/auth/login?next=https%3A%2F%2Fevil.example%2Fsteal");
    await fillAndSubmit(user);

    await waitFor(() => expect(navigation.router.replace).toHaveBeenCalled());
    const target = vi.mocked(navigation.router.replace).mock.calls.at(-1)?.[0];
    expect(target).not.toContain("evil.example");
    expect(target).toBe("/");
  });

  it("explains why the session ended when it was not the person's doing", async () => {
    renderLoginForm("/auth/login?reason=expired");
    // Someone bounced here mid-task needs to know why, or the sign-in form reads
    // as the app having lost their work.
    expect(await screen.findByText(/session|signed out|expired/i)).toBeInTheDocument();
  });
});
