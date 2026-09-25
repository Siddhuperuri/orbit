"""Which address ORBIT believes a request came from.

This is a security decision, not plumbing. The client address is the key for
the per-IP half of the brute-force limit and a field in every audit record, so
believing a client-supplied header would hand an attacker a fresh rate-limit
key on every request -- defeating the limit against precisely the distributed
attacker it exists to stop -- and let them write arbitrary addresses into the
audit trail.

The protection is the *default*, so these tests pin the default rather than
the mechanism. See ADR-0017.
"""

from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request

from orbit.api.deps import get_client_ip
from tests.conftest import build_settings


def _request(forwarded_for: str | None, *, trust_proxy_headers: bool = False) -> Request:
    """A bare ASGI request carrying the header under test.

    Built by hand rather than through `TestClient` because the subject is the
    dependency's own decision; going through a client would let the transport
    decide what the peer address is.
    """
    headers: list[tuple[bytes, bytes]] = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": headers,
        "client": ("127.0.0.1", 51234),
        "app": SimpleNamespace(
            state=SimpleNamespace(settings=build_settings(trust_proxy_headers=trust_proxy_headers))
        ),
    }
    return Request(scope)


class TestForwardedForIsNotTrustedByDefault:
    def test_the_header_is_ignored_when_the_flag_is_off(self) -> None:
        """The socket peer address wins, even though a header was sent."""
        assert get_client_ip(_request("203.0.113.9")) == "127.0.0.1"

    def test_a_spoofed_header_cannot_change_the_rate_limit_key(self) -> None:
        """Two requests with different forged headers must look identical.

        This is the property that matters: if they differed, an attacker would
        get an unlimited supply of fresh per-IP buckets by rotating one header.
        """
        first = get_client_ip(_request("203.0.113.9"))
        second = get_client_ip(_request("198.51.100.4"))
        assert first == second == "127.0.0.1"


class TestForwardedForIsUsedWhenExplicitlyTrusted:
    def test_the_left_most_entry_is_taken(self) -> None:
        """The original client, assuming the edge proxy overwrites the header
        rather than appending to it -- which is the deployment requirement the
        flag asserts."""
        request = _request("203.0.113.9, 70.41.3.18", trust_proxy_headers=True)
        assert get_client_ip(request) == "203.0.113.9"

    def test_the_peer_address_is_used_when_no_header_is_present(self) -> None:
        assert get_client_ip(_request(None, trust_proxy_headers=True)) == "127.0.0.1"
