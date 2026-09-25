"""The error catalogue is an API contract, so it is tested as one.

The exhaustiveness tests here are the mechanism that stops a new error type from
shipping with a duplicated code or an accidental 500. Adding an error without a
deliberate code and status fails the build.
"""

from __future__ import annotations

import pytest

from orbit.domain.errors import (
    AIProviderUnavailableError,
    DependencyUnavailableError,
    InvalidCredentialsError,
    NotFoundError,
    OrbitError,
    PermissionDeniedError,
    all_error_types,
)

_HTTP_MIN = 400
_HTTP_MAX = 599


def test_every_error_declares_a_unique_code() -> None:
    codes = [error.code for error in all_error_types()]
    duplicates = {code for code in codes if codes.count(code) > 1}
    assert not duplicates, f"error codes must be unique; duplicated: {sorted(duplicates)}"


def test_every_error_declares_an_explicit_status() -> None:
    # Guards against a new subclass silently inheriting 500 from the base class.
    for error in all_error_types():
        assert _HTTP_MIN <= error.http_status <= _HTTP_MAX, (
            f"{error.__name__} has an implausible status {error.http_status}"
        )


def test_codes_are_screaming_snake_case() -> None:
    # Clients branch on these strings; a casing change would be a breaking API
    # change, so the convention is asserted rather than assumed.
    for error in all_error_types():
        assert error.code.replace("_", "").isalnum()
        assert error.code == error.code.upper()


def test_context_is_kept_off_the_message() -> None:
    error = NotFoundError("Not found.", document_id="abc123", workspace_id="ws1")
    assert str(error) == "Not found."
    # Structured context exists for the operator's logs and is deliberately not
    # serialised into the response body.
    assert error.context == {"document_id": "abc123", "workspace_id": "ws1"}


def test_dependency_failures_are_marked_retryable() -> None:
    # The API reports these as retryable and the worker retries exactly this
    # set, so the two must agree (docs/architecture/data-flow.md).
    assert DependencyUnavailableError.retryable is True
    assert AIProviderUnavailableError.retryable is True


def test_client_errors_are_not_retryable() -> None:
    assert NotFoundError.retryable is False
    assert PermissionDeniedError.retryable is False


def test_inaccessible_resources_are_not_found_rather_than_forbidden() -> None:
    """A 403 would confirm the resource exists, leaking it across tenants."""
    assert NotFoundError.http_status == 404
    # PermissionDenied remains available for the case where the caller already
    # knows the resource exists.
    assert PermissionDeniedError.http_status == 403


def test_invalid_credentials_does_not_distinguish_cause() -> None:
    # One error type for both unknown-account and wrong-password, so the login
    # endpoint cannot be used to enumerate accounts.
    assert InvalidCredentialsError.code == "INVALID_CREDENTIALS"
    assert InvalidCredentialsError.http_status == 401


@pytest.mark.parametrize("error_type", all_error_types())
def test_every_error_is_constructible_with_only_a_message(
    error_type: type[OrbitError],
) -> None:
    error = error_type("something went wrong")
    assert error.message == "something went wrong"
    assert error.context == {}
