"""CSRF defence in depth (ADR-0009).

`SameSite=Lax` is the primary control and cannot be exercised by
`TestClient` (it does not simulate cross-site navigation); these tests cover
the second, independent layer this middleware adds: rejecting a mutating
request whose `Origin` disagrees with its own `Host` when an auth cookie is
present.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from orbit.api.middleware.csrf import CsrfOriginCheckMiddleware


def _minimal_app(*, trusted_origins: tuple[str, ...] = ()) -> FastAPI:
    """A tiny app with the middleware in isolation.

    Using the full `app` fixture would also require standing up auth
    dependencies for routes this middleware does not care about; a minimal
    app keeps these tests about the middleware alone.
    """
    app = FastAPI()
    app.add_middleware(CsrfOriginCheckMiddleware, trusted_origins=trusted_origins)

    @app.get("/thing")
    def _get() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.post("/thing")
    def _post() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.delete("/thing")
    def _delete() -> PlainTextResponse:
        return PlainTextResponse("ok")

    return app


class TestSafeMethodsAreAlwaysAllowed:
    def test_get_with_a_cross_site_origin_and_a_cookie_is_allowed(self) -> None:
        """Safe methods never mutate (enforced elsewhere by convention), so
        the middleware does not even inspect them."""
        client = TestClient(_minimal_app())
        client.cookies.set("orbit_access", "token")
        response = client.get("/thing", headers={"Origin": "https://evil.example"})
        assert response.status_code == 200


class TestMutatingRequestsWithoutACookieAreAllowed:
    def test_post_with_a_foreign_origin_but_no_cookie_is_allowed(self) -> None:
        """No ambient credential for a forged cross-site form to ride on --
        this is the Bearer-token API client case."""
        client = TestClient(_minimal_app())
        response = client.post("/thing", headers={"Origin": "https://evil.example"})
        assert response.status_code == 200


class TestMutatingRequestsWithACookie:
    def test_matching_origin_is_allowed(self) -> None:
        client = TestClient(_minimal_app(), base_url="http://testserver")
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing", headers={"Origin": "http://testserver"})
        assert response.status_code == 200

    def test_a_foreign_origin_is_rejected(self) -> None:
        client = TestClient(_minimal_app(), base_url="http://testserver")
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403

    def test_the_refresh_cookie_alone_is_also_protected(self) -> None:
        """Both cookie names carry a session; either one present is enough
        to treat the request as cookie-authenticated."""
        client = TestClient(_minimal_app(), base_url="http://testserver")
        client.cookies.set("orbit_refresh", "token")
        response = client.delete("/thing", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403

    def test_a_missing_origin_header_is_allowed(self) -> None:
        """Most same-site requests omit `Origin` entirely; `SameSite=Lax` is
        the control for that case, not this header check."""
        client = TestClient(_minimal_app(), base_url="http://testserver")
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing")
        assert response.status_code == 200

    def test_a_subdomain_is_still_a_foreign_origin(self) -> None:
        """Host-only cookies (no `Domain` attribute, per api/cookies.py)
        mean a sibling subdomain is not automatically trusted."""
        client = TestClient(_minimal_app(), base_url="http://testserver")
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing", headers={"Origin": "http://evil.testserver"})
        assert response.status_code == 403


class TestTrustedOrigins:
    """The development proxy case.

    A Next.js rewrite forwards `/api/*` with `Host` replaced by the backend's own,
    while the browser's `Origin` is the frontend's. Without a trusted-origin list
    every authenticated write from the frontend would be refused. The list is the
    operator's `ORBIT_CORS_ALLOWED_ORIGINS`, which is empty in production.
    """

    def test_a_listed_origin_is_allowed_even_though_host_differs(self) -> None:
        client = TestClient(
            _minimal_app(trusted_origins=("http://localhost:3000",)),
            base_url="http://testserver",  # what a rewriting proxy leaves in `Host`
        )
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing", headers={"Origin": "http://localhost:3000"})
        assert response.status_code == 200

    def test_an_unlisted_origin_is_still_rejected_when_a_list_exists(self) -> None:
        client = TestClient(
            _minimal_app(trusted_origins=("http://localhost:3000",)),
            base_url="http://testserver",
        )
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403

    def test_the_match_is_exact_not_by_host_or_prefix(self) -> None:
        """Scheme, host, and port all count: trusting `http://localhost:3000`
        must not trust another port, another scheme, or a lookalike host."""
        client = TestClient(
            _minimal_app(trusted_origins=("http://localhost:3000",)),
            base_url="http://testserver",
        )
        client.cookies.set("orbit_access", "token")
        for origin in (
            "https://localhost:3000",
            "http://localhost:3001",
            "http://localhost:3000.evil.example",
            "http://evil.localhost:3000",
        ):
            response = client.post("/thing", headers={"Origin": origin})
            assert response.status_code == 403, origin

    def test_a_trailing_slash_or_case_in_configuration_does_not_break_the_match(self) -> None:
        client = TestClient(
            _minimal_app(trusted_origins=("HTTP://LocalHost:3000/",)),
            base_url="http://testserver",
        )
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing", headers={"Origin": "http://localhost:3000"})
        assert response.status_code == 200

    def test_an_empty_list_changes_nothing(self) -> None:
        """The production configuration: only a matching `Host` is accepted."""
        client = TestClient(_minimal_app(trusted_origins=()), base_url="http://testserver")
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing", headers={"Origin": "http://localhost:3000"})
        assert response.status_code == 403


class TestRejectionUsesTheStandardEnvelope:
    def test_a_forged_origin_returns_a_parseable_error_body(self) -> None:
        """Every other failure in the API returns the same envelope shape.

        A bare 403 here would be the one response a client has to
        special-case, which is how error handling drifts into guesswork. The
        middleware runs outside the exception handlers, so it has to build the
        envelope itself.
        """
        client = TestClient(_minimal_app(), base_url="http://testserver")
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing", headers={"Origin": "https://evil.example"})

        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "CSRF_ORIGIN_MISMATCH"
        assert set(body["error"]) == {"code", "message", "request_id", "details"}

    def test_the_rejected_origin_is_not_echoed_back(self) -> None:
        """Reflecting attacker-supplied input into a response body is how a
        rejection becomes an injection point."""
        client = TestClient(_minimal_app(), base_url="http://testserver")
        client.cookies.set("orbit_access", "token")
        response = client.post("/thing", headers={"Origin": "https://evil.example"})
        assert "evil.example" not in response.text
