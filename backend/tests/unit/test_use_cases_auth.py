"""Auth use cases, against the in-memory fake `UnitOfWork`.

These exercise the dependency-injection contract directly: every use case
here is constructed with `FakeUnitOfWorkFactory`, the same shape the
SQL-backed factory presents, and none of them import anything from
`infrastructure` to do it -- swapping the implementation changed nothing
about how the use case is built or called.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from orbit.application.auth.authenticate_access_token import AuthenticateAccessToken
from orbit.application.auth.login_user import LoginUser
from orbit.application.auth.logout_session import LogoutSession
from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.auth.refresh_session import RefreshSession
from orbit.application.auth.register_user import RegisterUser
from orbit.application.auth.session import IssuedSession
from orbit.core.clock import FixedClock
from orbit.core.config import Settings
from orbit.core.tokens import hash_refresh_token
from orbit.domain.errors import (
    AuthenticationRequiredError,
    ConflictError,
    InvalidCredentialsError,
)
from tests.conftest import build_settings
from tests.unit.fakes.in_memory_unit_of_work import FakeUnitOfWorkFactory
from tests.unit.fakes.security_doubles import InMemoryRateLimiter, RecordingAuditSink

_SECRET = "test-secret-key-not-for-any-real-environment-0123456789"


# Login, refresh and logout also take a rate limiter and an audit sink. The
# tests in this module are about session mechanics, so they build those with
# throwaway doubles through these helpers rather than repeating the wiring --
# rate-limiting and audit behaviour have their own suite in test_security.py,
# where the doubles are held and asserted against.


def make_register(
    uow_factory: FakeUnitOfWorkFactory, settings: Settings | None = None
) -> RegisterUser:
    audit = RecordingAuditSink()
    resolved = settings or build_settings(secret_key=_SECRET)
    return RegisterUser(
        uow_factory,
        audit,
        AuthRateLimitGuard(
            InMemoryRateLimiter(),
            RateLimitPolicy.for_account_email(resolved, scope="register"),
            audit,
        ),
    )


def make_login(
    uow_factory: FakeUnitOfWorkFactory, settings: Settings, clock: FixedClock | None = None
) -> LoginUser:
    return LoginUser(uow_factory, settings, InMemoryRateLimiter(), RecordingAuditSink(), clock)


def make_refresh(
    uow_factory: FakeUnitOfWorkFactory, settings: Settings, clock: FixedClock | None = None
) -> RefreshSession:
    return RefreshSession(uow_factory, settings, RecordingAuditSink(), clock)


def make_logout(uow_factory: FakeUnitOfWorkFactory) -> LogoutSession:
    return LogoutSession(uow_factory, RecordingAuditSink())


@pytest.fixture
def uow_factory() -> FakeUnitOfWorkFactory:
    return FakeUnitOfWorkFactory()


@pytest.fixture
def settings() -> Settings:
    return build_settings(secret_key=_SECRET, access_token_ttl_seconds=900)


class TestRegisterUser:
    async def test_creates_an_account(self, uow_factory: FakeUnitOfWorkFactory) -> None:
        register = make_register(uow_factory)
        user = await register.execute(
            email="ada@example.com", password="a-long-passphrase", full_name="Ada Lovelace"
        )
        assert user.email == "ada@example.com"
        assert user.full_name == "Ada Lovelace"

    async def test_the_password_is_hashed_not_stored_raw(
        self, uow_factory: FakeUnitOfWorkFactory
    ) -> None:
        register = make_register(uow_factory)
        user = await register.execute(
            email="ada@example.com", password="a-long-passphrase", full_name="Ada"
        )
        stored_hash = uow_factory.state.password_hashes[user.id]
        assert stored_hash != "a-long-passphrase"
        assert stored_hash.startswith("$argon2id$")

    async def test_duplicate_email_is_a_conflict(self, uow_factory: FakeUnitOfWorkFactory) -> None:
        register = make_register(uow_factory)
        await register.execute(email="ada@example.com", password="x" * 12, full_name="Ada")
        with pytest.raises(ConflictError):
            await register.execute(email="ada@example.com", password="y" * 12, full_name="Ada2")

    async def test_a_rejected_registration_leaves_no_partial_write(
        self, uow_factory: FakeUnitOfWorkFactory
    ) -> None:
        """The transactional snapshot/rollback in the fake mirrors the real
        database: a failed use case must not leave a half-created account."""
        register = make_register(uow_factory)
        await register.execute(email="ada@example.com", password="x" * 12, full_name="Ada")
        with pytest.raises(ConflictError):
            await register.execute(email="ada@example.com", password="y" * 12, full_name="Ada2")
        assert len(uow_factory.state.users) == 1


class TestLoginUser:
    @pytest.fixture
    async def registered_email(self, uow_factory: FakeUnitOfWorkFactory) -> str:
        await make_register(uow_factory).execute(
            email="ada@example.com", password="correct-passphrase", full_name="Ada"
        )
        return "ada@example.com"

    async def test_succeeds_with_correct_credentials(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        settings: Settings,
        registered_email: str,
    ) -> None:
        login = make_login(uow_factory, settings)
        session = await login.execute(email=registered_email, password="correct-passphrase")
        assert session.user.email == registered_email
        assert session.access_token
        assert session.refresh_token

    async def test_persists_the_refresh_token_hashed(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        settings: Settings,
        registered_email: str,
    ) -> None:
        login = make_login(uow_factory, settings)
        session = await login.execute(email=registered_email, password="correct-passphrase")

        stored = await uow_factory().refresh_tokens.get_by_hash(
            hash_refresh_token(session.refresh_token)
        )
        assert stored is not None
        assert stored.user_id == session.user.id

    async def test_wrong_password_is_invalid_credentials(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        settings: Settings,
        registered_email: str,
    ) -> None:
        login = make_login(uow_factory, settings)
        with pytest.raises(InvalidCredentialsError):
            await login.execute(email=registered_email, password="totally-wrong")

    async def test_unknown_email_is_the_same_error_as_wrong_password(
        self, uow_factory: FakeUnitOfWorkFactory, settings: Settings
    ) -> None:
        """The two cases must be indistinguishable, or the endpoint becomes an
        account-enumeration oracle."""
        login = make_login(uow_factory, settings)
        with pytest.raises(InvalidCredentialsError) as unknown_email:
            await login.execute(email="nobody@example.com", password="whatever-12345")

        await make_register(uow_factory).execute(
            email="known@example.com", password="correct-passphrase", full_name="X"
        )
        with pytest.raises(InvalidCredentialsError) as wrong_password:
            await login.execute(email="known@example.com", password="whatever-12345")

        assert str(unknown_email.value) == str(wrong_password.value)

    async def test_a_deleted_account_cannot_log_in(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        settings: Settings,
        registered_email: str,
    ) -> None:
        user = await uow_factory().users.get_by_email(registered_email)
        assert user is not None
        await uow_factory().users.soft_delete(user.id)

        login = make_login(uow_factory, settings)
        with pytest.raises(InvalidCredentialsError):
            await login.execute(email=registered_email, password="correct-passphrase")


class TestRefreshSession:
    @pytest.fixture
    async def initial_session(
        self, uow_factory: FakeUnitOfWorkFactory, settings: Settings
    ) -> IssuedSession:
        await make_register(uow_factory).execute(
            email="ada@example.com", password="correct-passphrase", full_name="Ada"
        )
        login = make_login(uow_factory, settings)
        return await login.execute(email="ada@example.com", password="correct-passphrase")

    async def test_issues_a_new_pair_in_the_same_family(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        settings: Settings,
        initial_session: IssuedSession,
    ) -> None:
        refresh = make_refresh(uow_factory, settings)
        rotated = await refresh.execute(refresh_token=initial_session.refresh_token)

        assert rotated.refresh_token != initial_session.refresh_token
        assert rotated.user.id == initial_session.user.id

    async def test_the_old_token_no_longer_works(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        settings: Settings,
        initial_session: IssuedSession,
    ) -> None:
        refresh = make_refresh(uow_factory, settings)
        await refresh.execute(refresh_token=initial_session.refresh_token)

        with pytest.raises(AuthenticationRequiredError):
            await refresh.execute(refresh_token=initial_session.refresh_token)

    async def test_reuse_revokes_the_whole_family_including_the_new_token(
        self,
        uow_factory: FakeUnitOfWorkFactory,
        settings: Settings,
        initial_session: IssuedSession,
    ) -> None:
        """The core guarantee of ADR-0003: presenting a spent token is only
        possible if it was captured, so that event must burn the *entire*
        session, not just the one token that was reused."""
        refresh = make_refresh(uow_factory, settings)
        rotated = await refresh.execute(refresh_token=initial_session.refresh_token)

        # Reuse of the original (now-spent) token.
        with pytest.raises(AuthenticationRequiredError):
            await refresh.execute(refresh_token=initial_session.refresh_token)

        # The legitimately-rotated token must also be dead now.
        with pytest.raises(AuthenticationRequiredError):
            await refresh.execute(refresh_token=rotated.refresh_token)

    async def test_an_unknown_token_is_rejected(
        self, uow_factory: FakeUnitOfWorkFactory, settings: Settings
    ) -> None:
        refresh = make_refresh(uow_factory, settings)
        with pytest.raises(AuthenticationRequiredError):
            await refresh.execute(refresh_token="something-that-was-never-issued")

    async def test_an_expired_token_is_rejected(self, settings: Settings) -> None:
        uow_factory = FakeUnitOfWorkFactory()
        clock = FixedClock()
        await make_register(uow_factory).execute(
            email="ada@example.com", password="correct-passphrase", full_name="Ada"
        )
        login = make_login(uow_factory, settings, clock)
        session = await login.execute(email="ada@example.com", password="correct-passphrase")

        clock.advance(timedelta(seconds=settings.refresh_token_ttl_seconds + 1))

        refresh = make_refresh(uow_factory, settings, clock)
        with pytest.raises(AuthenticationRequiredError):
            await refresh.execute(refresh_token=session.refresh_token)


class TestLogoutSession:
    async def test_revokes_the_family_so_a_later_refresh_fails(
        self, uow_factory: FakeUnitOfWorkFactory, settings: Settings
    ) -> None:
        await make_register(uow_factory).execute(
            email="ada@example.com", password="correct-passphrase", full_name="Ada"
        )
        session = await make_login(uow_factory, settings).execute(
            email="ada@example.com", password="correct-passphrase"
        )

        await make_logout(uow_factory).execute(refresh_token=session.refresh_token)

        with pytest.raises(AuthenticationRequiredError):
            await make_refresh(uow_factory, settings).execute(refresh_token=session.refresh_token)

    async def test_is_idempotent(self, uow_factory: FakeUnitOfWorkFactory) -> None:
        """Logging out twice, or logging out a token nobody ever issued, must
        both succeed silently -- a logout that can fail is one a client has
        to retry-and-hope on."""
        logout = make_logout(uow_factory)
        await logout.execute(refresh_token="never-issued")
        await logout.execute(refresh_token="never-issued")  # does not raise


class TestAuthenticateAccessToken:
    async def test_resolves_the_user_from_a_valid_token(
        self, uow_factory: FakeUnitOfWorkFactory, settings: Settings
    ) -> None:
        await make_register(uow_factory).execute(
            email="ada@example.com", password="correct-passphrase", full_name="Ada"
        )
        session = await make_login(uow_factory, settings).execute(
            email="ada@example.com", password="correct-passphrase"
        )

        authenticate = AuthenticateAccessToken(uow_factory, secret_key=settings.secret_key)
        resolved = await authenticate.execute(session.access_token)
        assert resolved.id == session.user.id

    async def test_rejects_garbage(
        self, uow_factory: FakeUnitOfWorkFactory, settings: Settings
    ) -> None:
        authenticate = AuthenticateAccessToken(uow_factory, secret_key=settings.secret_key)
        with pytest.raises(AuthenticationRequiredError):
            await authenticate.execute("not-a-real-token")

    async def test_a_password_change_invalidates_outstanding_tokens_immediately(
        self, uow_factory: FakeUnitOfWorkFactory, settings: Settings
    ) -> None:
        """The mechanism behind ADR-0003's "takes effect immediately" claim:
        the token epoch is checked on every request, not merely at issuance."""
        await make_register(uow_factory).execute(
            email="ada@example.com", password="correct-passphrase", full_name="Ada"
        )
        session = await make_login(uow_factory, settings).execute(
            email="ada@example.com", password="correct-passphrase"
        )

        await uow_factory().users.set_password(session.user.id, "$argon2id$new-hash")

        authenticate = AuthenticateAccessToken(uow_factory, secret_key=settings.secret_key)
        with pytest.raises(AuthenticationRequiredError):
            await authenticate.execute(session.access_token)

    async def test_a_deleted_account_cannot_authenticate_with_an_old_token(
        self, uow_factory: FakeUnitOfWorkFactory, settings: Settings
    ) -> None:
        await make_register(uow_factory).execute(
            email="ada@example.com", password="correct-passphrase", full_name="Ada"
        )
        session = await make_login(uow_factory, settings).execute(
            email="ada@example.com", password="correct-passphrase"
        )
        await uow_factory().users.soft_delete(session.user.id)

        authenticate = AuthenticateAccessToken(uow_factory, secret_key=settings.secret_key)
        with pytest.raises(AuthenticationRequiredError):
            await authenticate.execute(session.access_token)
