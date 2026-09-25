import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { authApi } from "@/features/auth/api/endpoints";
import { RegisterForm } from "@/features/auth/components/register-form";
import { MIN_PASSWORD_LENGTH } from "@/features/auth/schemas/auth-schemas";
import { ApiError, ErrorCode, type FieldError } from "@/lib/api/errors";
import { navigation } from "@/test/navigation";
import { makeQueryClient, makeUser } from "@/test/render";

/**
 * Registration is two calls behind one button: create the account, then sign it
 * in so the person does not retype what they just entered. Most of what is
 * asserted here is about that seam -- in particular that a failure of the
 * *second* call never looks like a failure of the first, because the account
 * does exist by then and telling them otherwise would send them to create it
 * again.
 */

function renderRegisterForm() {
  navigation.reset("/auth/register");
  const client = makeQueryClient();
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return { user: userEvent.setup(), ...render(<RegisterForm />, { wrapper: Wrapper }) };
}

type RegisterResult = Awaited<ReturnType<typeof authApi.register>>;
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
  {
    fullName = "Ada Lovelace",
    email = "ada@example.com",
    password = "an-entirely-ordinary-passphrase",
  } = {},
) {
  await user.type(screen.getByLabelText("Full name"), fullName);
  await user.type(screen.getByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Create account" }));
}

describe("RegisterForm", () => {
  it("reports every empty required field at once, without calling the API", async () => {
    const register = vi.spyOn(authApi, "register");
    const { user } = renderRegisterForm();

    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(register).not.toHaveBeenCalled();
    // All three, not one per submit: fixing a form should not be a guessing game.
    await waitFor(() => {
      expect(screen.getByLabelText("Full name")).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    });
  });

  it("refuses a password under the published minimum", async () => {
    const register = vi.spyOn(authApi, "register");
    const { user } = renderRegisterForm();

    await fillAndSubmit(user, { password: "a".repeat(MIN_PASSWORD_LENGTH - 1) });

    expect(register).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    });
  });

  it("signs the new account in and opens the app", async () => {
    const register = vi.spyOn(authApi, "register").mockResolvedValue(makeUser() as RegisterResult);
    const login = vi.spyOn(authApi, "login").mockResolvedValue({ user: makeUser() } as LoginResult);

    const { user } = renderRegisterForm();
    await fillAndSubmit(user);

    await waitFor(() => expect(register).toHaveBeenCalledOnce());
    // Signed in with the credentials just entered, so nothing is retyped.
    expect(login).toHaveBeenCalledWith({
      email: "ada@example.com",
      password: "an-entirely-ordinary-passphrase",
    });
    await waitFor(() => expect(navigation.router.replace).toHaveBeenCalledWith("/"));
  });

  it("puts an already-taken address on the email field and focuses it", async () => {
    vi.spyOn(authApi, "register").mockRejectedValue(
      apiError(ErrorCode.Conflict, "That email is already registered.", 409),
    );
    const login = vi.spyOn(authApi, "login");

    const { user } = renderRegisterForm();
    await fillAndSubmit(user);

    expect(await screen.findByText("That email is already registered.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Email")).toHaveFocus());
    // No account was created, so there is nothing to sign in to.
    expect(login).not.toHaveBeenCalled();
    expect(navigation.router.replace).not.toHaveBeenCalled();
  });

  it("attaches server field errors to their fields", async () => {
    vi.spyOn(authApi, "register").mockRejectedValue(
      apiError(ErrorCode.Validation, "Validation failed.", 422, [
        { field: "body.password", message: "That password is too common." },
      ]),
    );

    const { user } = renderRegisterForm();
    await fillAndSubmit(user);

    expect(await screen.findByText("That password is too common.")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    });
  });

  it("sends them to sign in -- not to an error -- when only the convenience login fails", async () => {
    vi.spyOn(authApi, "register").mockResolvedValue(makeUser() as RegisterResult);
    vi.spyOn(authApi, "login").mockRejectedValue(
      apiError(ErrorCode.RateLimited, "Too many attempts.", 429),
    );

    const { user } = renderRegisterForm();
    await fillAndSubmit(user);

    // The account exists. Showing a failure here would be a lie that sends them
    // to create it a second time, which then fails as a duplicate.
    await waitFor(() => {
      const target = vi.mocked(navigation.router.replace).mock.calls.at(-1)?.[0];
      expect(target).toContain("/auth/login");
      expect(target).toContain("reason=registered");
    });
  });

  it("shows a pending state while the account is being created", async () => {
    let release: (value: RegisterResult) => void = () => undefined;
    vi.spyOn(authApi, "register").mockReturnValue(
      new Promise<RegisterResult>((resolve) => {
        release = resolve;
      }),
    );
    vi.spyOn(authApi, "login").mockResolvedValue({ user: makeUser() } as LoginResult);

    const { user } = renderRegisterForm();
    await fillAndSubmit(user);

    expect(await screen.findByRole("button", { name: "Creating account" })).toBeInTheDocument();

    release(makeUser() as RegisterResult);
    await waitFor(() => expect(navigation.router.replace).toHaveBeenCalled());
  });
});
