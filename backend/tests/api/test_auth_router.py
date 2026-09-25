"""Auth endpoints: HTTP contract, validation, and error mapping.

Use cases are overridden with stubs (`app.dependency_overrides`) -- these
tests are about the HTTP layer's behaviour, not the use cases' own logic,
which `tests/unit/test_use_cases_auth.py` already covers against the fake
repository. Overriding by the named dependency functions in `api/deps.py` is
what makes this possible without a real database.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orbit.api.deps import (
    get_authenticate_access_token,
    get_complete_password_reset,
    get_login_user,
    get_logout_session,
    get_refresh_session,
    get_register_user,
    get_request_email_verification,
    get_request_password_reset,
    get_verify_email,
)
from orbit.application.auth.session import IssuedSession
from orbit.domain.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ConflictError,
    InvalidCredentialsError,
    RateLimitedError,
)
from orbit.domain.models.entities import User

OverrideFn = Callable[..., None]


def _user(email: str = "ada@example.com") -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        full_name="Ada Lovelace",
        is_active=True,
        token_epoch=0,
        created_at=datetime.now(UTC),
    )


def _session(user: User | None = None) -> IssuedSession:
    now = datetime.now(UTC)
    return IssuedSession(
        user=user or _user(),
        access_token="stub-access-token",
        access_token_expires_at=now + timedelta(minutes=15),
        refresh_token="stub-refresh-token",
        refresh_token_expires_at=now + timedelta(days=30),
    )


class _StubRegisterUser:
    def __init__(self, result: User | Exception) -> None:
        self._result = result

    async def execute(self, **_kwargs: object) -> User:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _StubLoginUser:
    def __init__(self, result: IssuedSession | Exception) -> None:
        self._result = result

    async def execute(self, **_kwargs: object) -> IssuedSession:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _StubRefreshSession:
    def __init__(self, result: IssuedSession | Exception) -> None:
        self._result = result

    async def execute(self, **_kwargs: object) -> IssuedSession:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _StubLogoutSession:
    async def execute(self, **_kwargs: object) -> None:
        return None


class _StubNoOp:
    """Accepts any call and records it.

    Stands in for the account-lifecycle use cases, which return `None`
    for every input by design -- there is no result to stub, only the
    fact that the route reached them.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _StubAuthenticateAccessToken:
    def __init__(self, result: User | Exception) -> None:
        self._result = result

    async def execute(self, _token: str) -> User:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _provider(value: object) -> Callable[[], object]:
    """Wrap a stub in a zero-argument provider.

    FastAPI introspects a dependency override the same way it
    introspects a route handler, so `lambda stub=stub: stub` declares a
    parameter named `stub` -- which FastAPI reads as a request field and
    resolves itself, quietly handing the route something other than the
    stub the test bound. A closure has no parameters to misread.
    """
    return lambda: value


@pytest.fixture
def override(app: FastAPI) -> Iterator[OverrideFn]:
    def _apply(**overrides: object) -> None:
        mapping = {
            "register_user": get_register_user,
            "login_user": get_login_user,
            "refresh_session": get_refresh_session,
            "logout_session": get_logout_session,
            "authenticate_access_token": get_authenticate_access_token,
            "request_password_reset": get_request_password_reset,
            "complete_password_reset": get_complete_password_reset,
            "request_email_verification": get_request_email_verification,
            "verify_email": get_verify_email,
        }
        for name, stub in overrides.items():
            app.dependency_overrides[mapping[name]] = _provider(stub)

    # Registration also asks for a verification email, so every register
    # test would otherwise reach the real use case and, through it, a
    # database. Stubbed by default; a test that cares overrides it again.
    app.dependency_overrides[get_request_email_verification] = _StubNoOp

    yield _apply
    app.dependency_overrides.clear()


class TestRegister:
    def test_returns_201_with_the_created_user(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        override(register_user=_StubRegisterUser(_user()))
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "ada@example.com", "password": "a-long-passphrase", "full_name": "Ada"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "ada@example.com"
        assert "password" not in body
        assert "password_hash" not in body

    def test_duplicate_email_is_a_409_with_the_standard_envelope(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        override(
            register_user=_StubRegisterUser(
                ConflictError("An account with that email address already exists.")
            )
        )
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "ada@example.com", "password": "a-long-passphrase", "full_name": "Ada"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CONFLICT"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("email", "not-an-email"),
            ("password", "short"),
            ("full_name", "   "),
        ],
    )
    def test_invalid_input_is_a_422_before_the_use_case_ever_runs(
        self, client: TestClient, override: OverrideFn, field: str, value: str
    ) -> None:
        """The use case is stubbed to explode if called, so a 422 here proves
        Pydantic validation rejected the request first."""

        class _ExplodingRegisterUser:
            async def execute(self, **_kwargs: object) -> User:
                pytest.fail("the use case must not run when validation fails")

        override(register_user=_ExplodingRegisterUser())
        payload = {"email": "ada@example.com", "password": "a-long-passphrase", "full_name": "Ada"}
        payload[field] = value

        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert response.json()["error"]["details"]

    def test_registration_does_not_set_any_cookie(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        """Registration does not start a session (application/auth/register_user.py)."""
        override(register_user=_StubRegisterUser(_user()))
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "ada@example.com", "password": "a-long-passphrase", "full_name": "Ada"},
        )
        assert "set-cookie" not in {k.lower() for k in response.headers}


class TestLogin:
    def test_sets_httponly_cookies_and_returns_no_token_in_the_body(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        override(login_user=_StubLoginUser(_session()))
        response = client.post(
            "/api/v1/auth/login", json={"email": "ada@example.com", "password": "whatever12345"}
        )
        assert response.status_code == 200

        body = response.text
        assert "stub-access-token" not in body
        assert "stub-refresh-token" not in body

        access_cookie = response.cookies.get("orbit_access")
        refresh_cookie = response.cookies.get("orbit_refresh")
        assert access_cookie == "stub-access-token"
        assert refresh_cookie == "stub-refresh-token"

    def test_cookies_are_httponly(self, client: TestClient, override: OverrideFn) -> None:
        """The property the whole design depends on (ADR-0009): readable by
        the browser's cookie jar, never by JavaScript."""
        override(login_user=_StubLoginUser(_session()))
        response = client.post(
            "/api/v1/auth/login", json={"email": "ada@example.com", "password": "whatever12345"}
        )
        set_cookie_headers = response.headers.get_list("set-cookie")
        assert len(set_cookie_headers) == 2
        assert all("httponly" in header.lower() for header in set_cookie_headers)

    def test_wrong_credentials_return_401_with_a_generic_message(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        override(login_user=_StubLoginUser(InvalidCredentialsError("Incorrect email or password.")))
        response = client.post(
            "/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"

    def test_a_failed_login_sets_no_cookie(self, client: TestClient, override: OverrideFn) -> None:
        override(login_user=_StubLoginUser(InvalidCredentialsError("Incorrect email or password.")))
        response = client.post(
            "/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong"}
        )
        assert "set-cookie" not in {k.lower() for k in response.headers}

    def test_missing_fields_are_a_422(self, client: TestClient, override: OverrideFn) -> None:
        override(login_user=_StubLoginUser(_session()))
        response = client.post("/api/v1/auth/login", json={"email": "ada@example.com"})
        assert response.status_code == 422


class TestRefresh:
    def test_requires_the_refresh_cookie(self, client: TestClient, override: OverrideFn) -> None:
        override(refresh_session=_StubRefreshSession(_session()))
        response = client.post("/api/v1/auth/refresh")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_rotates_cookies_on_success(self, client: TestClient, override: OverrideFn) -> None:
        override(refresh_session=_StubRefreshSession(_session()))
        client.cookies.set("orbit_refresh", "presented-token")
        response = client.post("/api/v1/auth/refresh")
        assert response.status_code == 200
        assert response.cookies.get("orbit_access") == "stub-access-token"

    def test_reuse_detection_surfaces_as_401(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        override(
            refresh_session=_StubRefreshSession(
                AuthenticationRequiredError("Your session is no longer valid.")
            )
        )
        client.cookies.set("orbit_refresh", "already-used-token")
        response = client.post("/api/v1/auth/refresh")
        assert response.status_code == 401


class TestLogout:
    def test_clears_both_cookies(self, client: TestClient, override: OverrideFn) -> None:
        override(logout_session=_StubLogoutSession())
        client.cookies.set("orbit_refresh", "some-token")
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 204

        set_cookie_headers = response.headers.get_list("set-cookie")
        assert any('orbit_access=""' in h or "orbit_access=" in h for h in set_cookie_headers)
        assert any('orbit_refresh=""' in h or "orbit_refresh=" in h for h in set_cookie_headers)

    def test_requires_a_refresh_cookie_to_be_present(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        override(logout_session=_StubLogoutSession())
        response = client.post("/api/v1/auth/logout")
        assert response.status_code == 401


class TestMe:
    def test_returns_the_authenticated_user(self, client: TestClient, override: OverrideFn) -> None:
        user = _user()
        override(authenticate_access_token=_StubAuthenticateAccessToken(user))
        client.cookies.set("orbit_access", "a-valid-looking-token")
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 200
        assert response.json()["email"] == user.email

    def test_no_token_is_401(self, client: TestClient, override: OverrideFn) -> None:
        override(authenticate_access_token=_StubAuthenticateAccessToken(_user()))
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_an_invalid_token_is_401(self, client: TestClient, override: OverrideFn) -> None:
        override(
            authenticate_access_token=_StubAuthenticateAccessToken(
                AuthenticationRequiredError("Your session has expired.")
            )
        )
        client.cookies.set("orbit_access", "garbage")
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_authorization_header_works_for_non_browser_clients(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        user = _user()
        override(authenticate_access_token=_StubAuthenticateAccessToken(user))
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer a-valid-looking-token"}
        )
        assert response.status_code == 200

    def test_cookie_takes_precedence_over_the_header(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        """If a browser somehow sent both, the cookie -- its own transport
        (ADR-0009) -- must be the one actually used."""
        seen_tokens: list[str] = []

        class _RecordingAuthenticator:
            async def execute(self, token: str) -> User:
                seen_tokens.append(token)
                return _user()

        override(authenticate_access_token=_RecordingAuthenticator())
        client.cookies.set("orbit_access", "cookie-token")
        client.get("/api/v1/auth/me", headers={"Authorization": "Bearer header-token"})
        assert seen_tokens == ["cookie-token"]


class _StubRaising:
    """A use case that always raises. For asserting error mapping."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def execute(self, **_kwargs: object) -> None:
        raise self._error


class TestPasswordResetRequest:
    def test_returns_202_and_an_empty_body(self, client: TestClient, override: OverrideFn) -> None:
        """202, not 200 with a message.

        The status says "accepted, outcome not disclosed", which is exactly
        the guarantee: the response must not vary with whether the account
        exists, or the endpoint becomes an enumeration oracle.
        """
        stub = _StubNoOp()
        override(request_password_reset=stub)
        response = client.post("/api/v1/auth/password-reset", json={"email": "ada@example.com"})
        assert response.status_code == 202
        assert response.text == ""
        assert stub.calls[0]["email"] == "ada@example.com"

    def test_an_unknown_address_is_indistinguishable_from_a_known_one(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        """The use case returns `None` either way; the HTTP layer must not add
        a difference of its own."""
        override(request_password_reset=_StubNoOp())
        known = client.post("/api/v1/auth/password-reset", json={"email": "ada@example.com"})
        unknown = client.post("/api/v1/auth/password-reset", json={"email": "nobody@example.com"})

        assert known.status_code == unknown.status_code
        assert known.text == unknown.text

    def test_a_malformed_address_is_rejected_before_the_use_case_runs(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        class _Exploding:
            async def execute(self, **_kwargs: object) -> None:
                pytest.fail("the use case must not run when validation fails")

        override(request_password_reset=_Exploding())
        response = client.post("/api/v1/auth/password-reset", json={"email": "not-an-email"})
        assert response.status_code == 422

    def test_rate_limiting_maps_to_429_with_retry_after(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        """The header is the difference between a client backing off and a
        client hammering. Without it, blind retry is the only strategy."""
        override(
            request_password_reset=_StubRaising(
                RateLimitedError("Too many attempts. Try again later.", retry_after_seconds=900)
            )
        )
        response = client.post("/api/v1/auth/password-reset", json={"email": "ada@example.com"})

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "RATE_LIMITED"
        assert response.headers["retry-after"] == "900"

    def test_the_error_body_never_names_the_account(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        """A 429 already leaks that this address was asked about recently; the
        body must not confirm the account exists on top of that."""
        override(
            request_password_reset=_StubRaising(
                RateLimitedError(
                    "Too many attempts. Try again later.",
                    retry_after_seconds=900,
                    scope="password_reset",
                )
            )
        )
        response = client.post("/api/v1/auth/password-reset", json={"email": "ada@example.com"})
        assert "ada@example.com" not in response.text
        # `scope` is operator context bound onto the log line, never the body.
        assert "password_reset" not in response.text


class TestPasswordResetConfirm:
    def test_returns_204_and_clears_the_session_cookies(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        """Every session is dead server-side after a reset, so leaving the
        browser holding cookies only produces guaranteed 401s."""
        override(complete_password_reset=_StubNoOp())
        response = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "a-token", "new_password": "a-brand-new-passphrase"},
        )
        assert response.status_code == 204
        set_cookie = " ".join(
            value for key, value in response.headers.items() if key.lower() == "set-cookie"
        )
        assert "orbit_access=" in set_cookie
        assert "orbit_refresh=" in set_cookie

    def test_an_invalid_token_is_a_400_with_no_detail_about_why(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        """One message for unknown, expired, spent, and wrong-purpose alike.

        Distinguishing them tells an attacker holding a guessed or stale token
        which part to change.
        """
        override(
            complete_password_reset=_StubRaising(
                BadRequestError("This password reset link is invalid or has expired.")
            )
        )
        response = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "wrong", "new_password": "a-brand-new-passphrase"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "BAD_REQUEST"

    @pytest.mark.parametrize(
        ("field", "value"),
        [("new_password", "short"), ("token", "")],
    )
    def test_invalid_input_is_a_422(
        self, client: TestClient, override: OverrideFn, field: str, value: str
    ) -> None:
        class _Exploding:
            async def execute(self, **_kwargs: object) -> None:
                pytest.fail("the use case must not run when validation fails")

        override(complete_password_reset=_Exploding())
        payload = {"token": "a-token", "new_password": "a-brand-new-passphrase"}
        payload[field] = value
        response = client.post("/api/v1/auth/password-reset/confirm", json=payload)
        assert response.status_code == 422

    def test_the_password_is_never_echoed_back(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        override(complete_password_reset=_StubNoOp())
        response = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "a-token", "new_password": "a-very-distinctive-passphrase"},
        )
        assert "a-very-distinctive-passphrase" not in response.text


class TestEmailVerification:
    def test_confirming_needs_no_session(self, client: TestClient, override: OverrideFn) -> None:
        """The link is followed from a mail client, which may not be the
        browser holding the session. Requiring one would strand every user who
        reads mail on a different device."""
        override(verify_email=_StubNoOp())
        response = client.post("/api/v1/auth/verify-email/confirm", json={"token": "a-token"})
        assert response.status_code == 204

    def test_confirming_does_not_issue_a_session(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        """An emailed link must never be a login credential."""
        override(verify_email=_StubNoOp())
        response = client.post("/api/v1/auth/verify-email/confirm", json={"token": "a-token"})
        assert "set-cookie" not in {k.lower() for k in response.headers}

    def test_an_invalid_token_is_a_400(self, client: TestClient, override: OverrideFn) -> None:
        override(
            verify_email=_StubRaising(
                BadRequestError("This verification link is invalid or has expired.")
            )
        )
        response = client.post("/api/v1/auth/verify-email/confirm", json={"token": "wrong"})
        assert response.status_code == 400

    def test_resending_requires_authentication(self, client: TestClient) -> None:
        response = client.post("/api/v1/auth/verify-email/resend")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    def test_resending_is_scoped_to_the_authenticated_user(
        self, client: TestClient, override: OverrideFn
    ) -> None:
        """The user id comes from the session, never from the request body.

        Accepting one in the body would let anybody mail somebody else at
        will -- an IDOR with an outbound-email amplifier attached.
        """
        user = _user()
        stub = _StubNoOp()
        override(authenticate_access_token=_StubAuthenticateAccessToken(user))
        override(request_email_verification=stub)

        client.cookies.set("orbit_access", "stub-access-token")
        response = client.post(
            "/api/v1/auth/verify-email/resend",
            # A different id in the body must be ignored entirely.
            json={"user_id": str(uuid.uuid4())},
            headers={"origin": "http://testserver"},
        )
        client.cookies.clear()

        assert response.status_code == 202
        assert stub.calls[0]["user_id"] == user.id
