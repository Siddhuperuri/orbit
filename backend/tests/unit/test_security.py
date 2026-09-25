"""Security behaviour, exercised end to end through real use cases.

Deliberately *not* written with stubs. Every use case here is the production
class, wired to the in-memory `UnitOfWork` fake, so an attack path is tested
against the code that would actually run rather than against a double that
agrees with the test. A stub that returns `NotFoundError` proves nothing about
whether the repository would have returned the row.

The scenarios below are the ones an attacker actually tries, and each is named
for what it attempts rather than for the function it calls:

* unauthenticated access
* expired sessions
* invalid credentials
* malformed and forged tokens
* cross-user document access (IDOR / BOLA)
* cross-workspace access
* privilege escalation
* brute force

`tests/api/*` covers the HTTP mapping of these; this module covers the
decisions underneath it, where the mapping cannot lie about the outcome.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from orbit.application.access import ResolveAccessContext
from orbit.application.auth.account_mail import AccountLinkMailer
from orbit.application.auth.account_tokens import hash_account_token
from orbit.application.auth.authenticate_access_token import AuthenticateAccessToken
from orbit.application.auth.email_verification import RequestEmailVerification, VerifyEmail
from orbit.application.auth.login_user import LoginUser
from orbit.application.auth.logout_session import LogoutSession
from orbit.application.auth.password_reset import CompletePasswordReset, RequestPasswordReset
from orbit.application.auth.rate_limits import AuthRateLimitGuard, RateLimitPolicy
from orbit.application.auth.refresh_session import RefreshSession
from orbit.application.auth.register_user import RegisterUser
from orbit.application.auth.session import IssuedSession
from orbit.application.documents.delete_document import DeleteDocument
from orbit.application.documents.get_document import GetDocument
from orbit.application.documents.list_documents import ListDocuments
from orbit.application.documents.update_document import UpdateDocument
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.application.workspaces.delete_workspace import DeleteWorkspace
from orbit.application.workspaces.get_workspace import GetWorkspace
from orbit.application.workspaces.members import ChangeMemberRole, InviteMember, RemoveMember
from orbit.application.workspaces.rename_workspace import RenameWorkspace
from orbit.core.clock import FixedClock
from orbit.core.config import Settings
from orbit.domain.access import AccessContext, Role
from orbit.domain.documents import DocumentEdit
from orbit.domain.errors import (
    AuthenticationRequiredError,
    InvalidCredentialsError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
)
from orbit.domain.models.entities import Document, User, VersionContent
from orbit.domain.ports.audit import AuditAction, AuditEvent
from tests.conftest import build_settings
from tests.unit.fakes.in_memory_unit_of_work import FakeUnitOfWorkFactory
from tests.unit.fakes.security_doubles import (
    CapturingEmailSender,
    FailingEmailSender,
    InMemoryRateLimiter,
    RecordingAuditSink,
    UnavailableRateLimiter,
)

_SECRET = "test-secret-key-not-for-any-real-environment-0123456789"
_PASSWORD = "a-long-enough-passphrase"
_RESET_TEMPLATE = "https://orbit.test/reset?token={token}"
_VERIFY_TEMPLATE = "https://orbit.test/verify?token={token}"


@dataclass
class Harness:
    """Every use case an attack path might touch, sharing one fake database.

    Assembled as a fixture rather than per test because the interesting
    scenarios cross several use cases -- registering two users, having one
    create a workspace, then having the other try to read into it -- and
    building that per test would bury the attack in setup.
    """

    uow_factory: FakeUnitOfWorkFactory
    settings: Settings
    clock: FixedClock
    audit: RecordingAuditSink
    limiter: InMemoryRateLimiter
    email: CapturingEmailSender

    register: RegisterUser
    login: LoginUser
    logout: LogoutSession
    refresh: RefreshSession
    authenticate: AuthenticateAccessToken
    resolve: ResolveAccessContext

    request_reset: RequestPasswordReset
    complete_reset: CompletePasswordReset
    request_verification: RequestEmailVerification
    verify_email: VerifyEmail

    create_workspace: CreateWorkspace
    get_workspace: GetWorkspace
    delete_workspace: DeleteWorkspace
    rename_workspace: RenameWorkspace
    invite_member: InviteMember
    change_member_role: ChangeMemberRole
    remove_member: RemoveMember

    get_document: GetDocument
    list_documents: ListDocuments
    update_document: UpdateDocument
    delete_document: DeleteDocument

    async def sign_up(self, email: str) -> User:
        return await self.register.execute(email=email, password=_PASSWORD, full_name="Test Person")

    async def sign_in(self, email: str) -> IssuedSession:
        return await self.login.execute(email=email, password=_PASSWORD)

    async def context_for(self, user: User, workspace_id: uuid.UUID) -> AccessContext:
        return await self.resolve.execute(user_id=user.id, workspace_id=workspace_id)

    async def add_document(self, ctx: AccessContext, title: str) -> Document:
        async with self.uow_factory() as uow:
            document = await uow.documents.create(
                ctx,
                title=title,
                folder_id=None,
                content=VersionContent(
                    storage_key=f"w/{ctx.workspace_id}/{uuid.uuid4()}",
                    # Distinct per document: identical bytes are treated as
                    # a re-upload and deduplicated (ADR-0011), which is not
                    # the behaviour any test in this module is about.
                    content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
                    byte_size=11,
                    content_type="text/plain",
                    original_filename=f"{title}.txt",
                ),
            )
            await uow.commit()
        return document


@pytest.fixture
def harness() -> Harness:
    uow_factory = FakeUnitOfWorkFactory()
    settings = build_settings(secret_key=_SECRET, access_token_ttl_seconds=900)
    clock = FixedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    audit = RecordingAuditSink()
    limiter = InMemoryRateLimiter()
    email = CapturingEmailSender()

    def guard(scope: str) -> AuthRateLimitGuard:
        return AuthRateLimitGuard(
            limiter, RateLimitPolicy.for_account_email(settings, scope=scope), audit
        )

    return Harness(
        uow_factory=uow_factory,
        settings=settings,
        clock=clock,
        audit=audit,
        limiter=limiter,
        email=email,
        register=RegisterUser(uow_factory, audit, guard("register")),
        login=LoginUser(uow_factory, settings, limiter, audit, clock),
        logout=LogoutSession(uow_factory, audit),
        refresh=RefreshSession(uow_factory, settings, audit, clock),
        authenticate=AuthenticateAccessToken(uow_factory, secret_key=_SECRET, clock=clock),
        resolve=ResolveAccessContext(uow_factory),
        request_reset=RequestPasswordReset(
            uow_factory,
            AccountLinkMailer(email, _RESET_TEMPLATE),
            audit,
            guard("password_reset"),
            clock,
        ),
        complete_reset=CompletePasswordReset(uow_factory, audit, clock),
        request_verification=RequestEmailVerification(
            uow_factory,
            AccountLinkMailer(email, _VERIFY_TEMPLATE),
            audit,
            guard("email_verification"),
            clock,
        ),
        verify_email=VerifyEmail(uow_factory, audit, clock),
        create_workspace=CreateWorkspace(uow_factory),
        get_workspace=GetWorkspace(uow_factory),
        delete_workspace=DeleteWorkspace(uow_factory, audit),
        rename_workspace=RenameWorkspace(uow_factory),
        invite_member=InviteMember(uow_factory, audit),
        change_member_role=ChangeMemberRole(uow_factory, audit),
        remove_member=RemoveMember(uow_factory, audit),
        get_document=GetDocument(uow_factory),
        list_documents=ListDocuments(uow_factory),
        update_document=UpdateDocument(uow_factory),
        delete_document=DeleteDocument(uow_factory),
    )


def _token_from_url(url_body: str) -> str:
    """Pull the one-time token back out of the email body.

    Parsing the delivered message rather than reaching into the repository is
    deliberate: it proves the token the user actually receives is the token
    that redeems, which a direct read would not.
    """
    _, _, token = url_body.partition("token=")
    return token.split()[0].strip()


# ---------------------------------------------------------------------------
# Missing authentication
# ---------------------------------------------------------------------------


class TestMissingAuthentication:
    async def test_an_empty_token_is_rejected(self, harness: Harness) -> None:
        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute("")

    async def test_a_token_for_a_deleted_account_is_rejected(self, harness: Harness) -> None:
        """A valid signature is not sufficient; the account must still exist."""
        user = await harness.sign_up("ada@example.com")
        session = await harness.sign_in("ada@example.com")

        async with harness.uow_factory() as uow:
            await uow.users.soft_delete(user.id)
            await uow.commit()

        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute(session.access_token)

    async def test_a_token_for_a_deactivated_account_is_rejected(self, harness: Harness) -> None:
        user = await harness.sign_up("ada@example.com")
        session = await harness.sign_in("ada@example.com")

        async with harness.uow_factory() as uow:
            stored = harness.uow_factory.state.users[user.id]
            harness.uow_factory.state.users[user.id] = _deactivate(stored)
            await uow.commit()

        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute(session.access_token)


def _deactivate(user: User) -> User:
    return dataclasses.replace(user, is_active=False)


# ---------------------------------------------------------------------------
# Malformed and forged tokens
# ---------------------------------------------------------------------------


class TestMalformedTokens:
    @pytest.mark.parametrize(
        ("label", "token"),
        [
            ("empty", ""),
            ("not a jwt", "definitely-not-a-token"),
            ("two segments", "aaaa.bbbb"),
            ("garbage payload", "aaaa.bbbb.cccc"),
            ("sql injection attempt", "' OR 1=1 --"),
            ("null byte", "abc\x00def"),
            ("very long", "a" * 10_000),
        ],
    )
    async def test_garbage_is_rejected_as_unauthenticated_not_as_a_crash(
        self, harness: Harness, label: str, token: str
    ) -> None:
        """Every malformed input takes the same path as an expired one.

        The point is not only that these fail, but that they fail as
        `AuthenticationRequiredError` -- a 401 -- rather than escaping as an
        unhandled exception, which would be a 500 and, worse, a differing
        response that distinguishes malformed from merely wrong.
        """
        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute(token)

    async def test_a_token_signed_with_another_key_is_rejected(self, harness: Harness) -> None:
        """The signature is the whole security property; forging it must fail."""
        user = await harness.sign_up("ada@example.com")
        forged = jwt.encode(
            {
                "sub": str(user.id),
                "epoch": 0,
                "iat": int(harness.clock.now().timestamp()),
                "exp": int((harness.clock.now() + timedelta(minutes=15)).timestamp()),
            },
            "an-entirely-different-signing-key-0123456789abcd",
            algorithm="HS256",
        )
        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute(forged)

    async def test_an_unsigned_alg_none_token_is_rejected(self, harness: Harness) -> None:
        """The classic JWT bypass: claim `alg: none` and omit the signature.

        PyJWT is configured with an explicit algorithm allowlist, which is what
        makes this fail. The test exists because that configuration is easy to
        loosen accidentally and impossible to notice by reading a passing
        happy-path suite.
        """
        user = await harness.sign_up("ada@example.com")
        unsigned = jwt.encode(
            {"sub": str(user.id), "epoch": 0},
            key="",
            algorithm="none",
        )
        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute(unsigned)

    async def test_a_token_naming_a_nonexistent_user_is_rejected(self, harness: Harness) -> None:
        """A correctly signed token for an id that was never issued one.

        Reachable if a signing key ever leaks; the subject lookup is the
        second, independent check that stops it becoming access.
        """
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "epoch": 0,
                "iat": int(harness.clock.now().timestamp()),
                "exp": int((harness.clock.now() + timedelta(minutes=15)).timestamp()),
            },
            _SECRET,
            algorithm="HS256",
        )
        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute(forged)


# ---------------------------------------------------------------------------
# Expired and revoked sessions
# ---------------------------------------------------------------------------


class TestExpiredSessions:
    async def test_an_access_token_past_its_expiry_is_rejected(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        session = await harness.sign_in("ada@example.com")

        # One second past the 15-minute TTL, on the injected clock.
        harness.clock.advance(timedelta(seconds=harness.settings.access_token_ttl_seconds + 1))

        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute(session.access_token)

    async def test_a_token_from_before_a_password_change_is_rejected(
        self, harness: Harness
    ) -> None:
        """The token epoch, not the expiry, is what makes revocation immediate.

        Without it a stolen access token stays valid for its full lifetime
        after the victim changes their password -- during the exact incident
        the password change is responding to (ADR-0003).
        """
        user = await harness.sign_up("ada@example.com")
        session = await harness.sign_in("ada@example.com")

        # Still valid a moment ago.
        assert (await harness.authenticate.execute(session.access_token)).id == user.id

        async with harness.uow_factory() as uow:
            await uow.users.set_password(user.id, "$argon2id$irrelevant")
            await uow.commit()

        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute(session.access_token)

    async def test_an_expired_refresh_token_cannot_mint_a_new_session(
        self, harness: Harness
    ) -> None:
        await harness.sign_up("ada@example.com")
        session = await harness.sign_in("ada@example.com")

        harness.clock.advance(timedelta(seconds=harness.settings.refresh_token_ttl_seconds + 1))

        with pytest.raises(AuthenticationRequiredError):
            await harness.refresh.execute(refresh_token=session.refresh_token)

    async def test_a_refresh_token_replayed_after_rotation_kills_the_family(
        self, harness: Harness
    ) -> None:
        """Reuse detection: the second presentation revokes everything.

        A refresh token is single-use, so a second presentation means a copy
        exists. Revoking the whole family means the attacker's stolen token
        and the victim's live one both stop working -- which is correct: the
        session is compromised, and the victim signing in again is a far
        better outcome than a quiet coexistence.
        """
        await harness.sign_up("ada@example.com")
        first = await harness.sign_in("ada@example.com")
        second = await harness.refresh.execute(refresh_token=first.refresh_token)

        with pytest.raises(AuthenticationRequiredError):
            await harness.refresh.execute(refresh_token=first.refresh_token)

        # The legitimately rotated token is dead too.
        with pytest.raises(AuthenticationRequiredError):
            await harness.refresh.execute(refresh_token=second.refresh_token)

        # Exactly one reuse event, not two. The second rejection is a
        # *revoked* token being presented -- the expected aftermath of the
        # first incident, not a second one. Counting it would mean an
        # operator triaging alerts sees one compromise reported as many.
        assert harness.audit.count(AuditAction.REFRESH_TOKEN_REUSE_DETECTED) == 1

    async def test_logout_invalidates_the_refresh_token(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        session = await harness.sign_in("ada@example.com")

        await harness.logout.execute(refresh_token=session.refresh_token)

        with pytest.raises(AuthenticationRequiredError):
            await harness.refresh.execute(refresh_token=session.refresh_token)


# ---------------------------------------------------------------------------
# Invalid credentials
# ---------------------------------------------------------------------------


class TestInvalidCredentials:
    async def test_a_wrong_password_is_rejected(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        with pytest.raises(InvalidCredentialsError):
            await harness.login.execute(email="ada@example.com", password="not-the-password")

    async def test_an_unknown_address_and_a_wrong_password_are_indistinguishable(
        self, harness: Harness
    ) -> None:
        """Same error type, same message, for both.

        Any difference here -- text, code, or type -- makes the login endpoint
        an account-enumeration oracle available to anyone.
        """
        await harness.sign_up("ada@example.com")

        with pytest.raises(InvalidCredentialsError) as wrong_password:
            await harness.login.execute(email="ada@example.com", password="not-the-password")
        with pytest.raises(InvalidCredentialsError) as unknown_account:
            await harness.login.execute(email="nobody@example.com", password=_PASSWORD)

        assert wrong_password.value.message == unknown_account.value.message
        assert wrong_password.value.code == unknown_account.value.code

    async def test_a_deactivated_account_cannot_sign_in_with_the_right_password(
        self, harness: Harness
    ) -> None:
        user = await harness.sign_up("ada@example.com")
        harness.uow_factory.state.users[user.id] = _deactivate(
            harness.uow_factory.state.users[user.id]
        )

        with pytest.raises(InvalidCredentialsError):
            await harness.sign_in("ada@example.com")

    async def test_a_failed_login_is_audited_with_the_attempted_address(
        self, harness: Harness
    ) -> None:
        with pytest.raises(InvalidCredentialsError):
            await harness.login.execute(email="nobody@example.com", password="whatever")

        failures = [e for e in harness.audit.events if e.action is AuditAction.LOGIN_FAILED]
        assert len(failures) == 1
        assert failures[0].actor_email == "nobody@example.com"
        # The attempted password must never reach the audit record.
        assert "whatever" not in str(failures[0].metadata)


# ---------------------------------------------------------------------------
# Brute force
# ---------------------------------------------------------------------------


class TestBruteForce:
    async def test_repeated_failures_are_eventually_rate_limited(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        limit = harness.settings.login_rate_limit_per_account

        for _ in range(limit):
            with pytest.raises(InvalidCredentialsError):
                await harness.login.execute(email="ada@example.com", password="wrong")

        with pytest.raises(RateLimitedError) as rejected:
            await harness.login.execute(email="ada@example.com", password="wrong")
        assert rejected.value.retry_after_seconds is not None

    async def test_the_limit_applies_to_the_correct_password_too(self, harness: Harness) -> None:
        """Exhausting the budget locks out the real user for the window.

        That is the intended trade and it is worth stating plainly: the
        alternative -- letting a correct password through an exhausted budget
        -- would mean the limit stops only *incorrect* guesses, which is
        precisely the guess an attacker eventually makes.
        """
        await harness.sign_up("ada@example.com")
        for _ in range(harness.settings.login_rate_limit_per_account):
            with pytest.raises(InvalidCredentialsError):
                await harness.login.execute(email="ada@example.com", password="wrong")

        with pytest.raises(RateLimitedError):
            await harness.sign_in("ada@example.com")

    async def test_a_successful_login_clears_the_account_budget(self, harness: Harness) -> None:
        """A user who mistypes twice then succeeds starts fresh."""
        await harness.sign_up("ada@example.com")
        for _ in range(2):
            with pytest.raises(InvalidCredentialsError):
                await harness.login.execute(email="ada@example.com", password="wrong")

        await harness.sign_in("ada@example.com")

        # Every subsequent failure counts from zero again, so the budget is
        # not one typo away from exhaustion for the rest of the window.
        for _ in range(harness.settings.login_rate_limit_per_account):
            with pytest.raises(InvalidCredentialsError):
                await harness.login.execute(email="ada@example.com", password="wrong")

    async def test_exceeding_the_limit_is_audited(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        for _ in range(harness.settings.login_rate_limit_per_account):
            with pytest.raises(InvalidCredentialsError):
                await harness.login.execute(email="ada@example.com", password="wrong")
        with pytest.raises(RateLimitedError):
            await harness.login.execute(email="ada@example.com", password="wrong")

        assert harness.audit.count(AuditAction.RATE_LIMIT_EXCEEDED) == 1

    async def test_the_limit_is_checked_before_the_password_is_verified(
        self, harness: Harness
    ) -> None:
        """Rejection must not cost an Argon2id hash.

        If the limiter ran after verification, every rejected attempt would
        still burn the most expensive operation in the process -- turning the
        brute-force defence into a CPU-exhaustion amplifier.
        """
        await harness.sign_up("ada@example.com")
        for _ in range(harness.settings.login_rate_limit_per_account):
            with pytest.raises(InvalidCredentialsError):
                await harness.login.execute(email="ada@example.com", password="wrong")

        before = len(harness.audit.events)
        with pytest.raises(RateLimitedError):
            await harness.login.execute(email="ada@example.com", password="wrong")

        # Exactly one new event -- the rate-limit rejection. A LOGIN_FAILED
        # alongside it would mean verification ran anyway.
        added = harness.audit.events[before:]
        assert [e.action for e in added] == [AuditAction.RATE_LIMIT_EXCEEDED]


# ---------------------------------------------------------------------------
# Cross-workspace access
# ---------------------------------------------------------------------------


class TestCrossWorkspaceAccess:
    async def test_a_non_member_cannot_resolve_a_context_for_the_workspace(
        self, harness: Harness
    ) -> None:
        """`ResolveAccessContext` is the gate. Everything else assumes it held.

        Note the error: `NotFoundError`, not `PermissionDeniedError`. A 403
        would confirm the workspace exists, which leaks its existence to an
        outsider probing ids.
        """
        owner = await harness.sign_up("owner@example.com")
        outsider = await harness.sign_up("outsider@example.com")
        workspace = await harness.create_workspace.execute(
            name="Private", created_by_user_id=owner.id
        )

        with pytest.raises(NotFoundError):
            await harness.context_for(outsider, workspace.id)

    async def test_a_member_of_one_workspace_cannot_reach_another(self, harness: Harness) -> None:
        """Membership is per workspace, not a global "is a user" check."""
        ada = await harness.sign_up("ada@example.com")
        bob = await harness.sign_up("bob@example.com")
        ada_ws = await harness.create_workspace.execute(name="Ada", created_by_user_id=ada.id)
        await harness.create_workspace.execute(name="Bob", created_by_user_id=bob.id)

        with pytest.raises(NotFoundError):
            await harness.context_for(bob, ada_ws.id)

    async def test_an_invented_workspace_id_is_not_found(self, harness: Harness) -> None:
        ada = await harness.sign_up("ada@example.com")
        with pytest.raises(NotFoundError):
            await harness.context_for(ada, uuid.uuid4())

    async def test_removing_a_member_immediately_ends_their_access(self, harness: Harness) -> None:
        owner = await harness.sign_up("owner@example.com")
        guest = await harness.sign_up("guest@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        await harness.invite_member.execute(owner_ctx, user_id=guest.id, role=Role.MEMBER)

        # Access is real while the membership exists.
        assert await harness.context_for(guest, workspace.id)

        await harness.remove_member.execute(owner_ctx, user_id=guest.id)

        with pytest.raises(NotFoundError):
            await harness.context_for(guest, workspace.id)


# ---------------------------------------------------------------------------
# Cross-user document access (IDOR / BOLA)
# ---------------------------------------------------------------------------


class TestCrossUserDocumentAccess:
    """The attack is always the same: take a real id, present it with your own
    credentials, and see whether the server checks the two against each other.
    """

    async def test_a_document_id_from_another_workspace_is_not_found(
        self, harness: Harness
    ) -> None:
        ada = await harness.sign_up("ada@example.com")
        bob = await harness.sign_up("bob@example.com")
        ada_ws = await harness.create_workspace.execute(name="Ada", created_by_user_id=ada.id)
        bob_ws = await harness.create_workspace.execute(name="Bob", created_by_user_id=bob.id)

        ada_ctx = await harness.context_for(ada, ada_ws.id)
        secret = await harness.add_document(ada_ctx, "Ada's plans")

        # Bob's own, legitimate context -- and Ada's document id.
        bob_ctx = await harness.context_for(bob, bob_ws.id)
        with pytest.raises(NotFoundError):
            await harness.get_document.execute(bob_ctx, secret.id)

    async def test_the_same_document_is_readable_by_its_own_workspace(
        self, harness: Harness
    ) -> None:
        """The control for the test above: the id is real and does resolve.

        Without this, a repository that returned `None` for everything would
        pass the isolation test while being entirely broken.
        """
        ada = await harness.sign_up("ada@example.com")
        ada_ws = await harness.create_workspace.execute(name="Ada", created_by_user_id=ada.id)
        ada_ctx = await harness.context_for(ada, ada_ws.id)
        document = await harness.add_document(ada_ctx, "Ada's plans")

        found = await harness.get_document.execute(ada_ctx, document.id)
        assert found.id == document.id

    async def test_a_listing_never_includes_another_workspaces_documents(
        self, harness: Harness
    ) -> None:
        ada = await harness.sign_up("ada@example.com")
        bob = await harness.sign_up("bob@example.com")
        ada_ws = await harness.create_workspace.execute(name="Ada", created_by_user_id=ada.id)
        bob_ws = await harness.create_workspace.execute(name="Bob", created_by_user_id=bob.id)

        ada_ctx = await harness.context_for(ada, ada_ws.id)
        bob_ctx = await harness.context_for(bob, bob_ws.id)
        await harness.add_document(ada_ctx, "Ada one")
        await harness.add_document(ada_ctx, "Ada two")
        bob_document = await harness.add_document(bob_ctx, "Bob one")

        page = await harness.list_documents.execute(bob_ctx, limit=50)
        assert [item.id for item in page.items] == [bob_document.id]

    @pytest.mark.parametrize("operation", ["rename", "delete"])
    async def test_mutations_cannot_reach_another_workspaces_document(
        self, harness: Harness, operation: str
    ) -> None:
        """Reads are not the only IDOR surface; writes are the damaging half."""
        ada = await harness.sign_up("ada@example.com")
        bob = await harness.sign_up("bob@example.com")
        ada_ws = await harness.create_workspace.execute(name="Ada", created_by_user_id=ada.id)
        bob_ws = await harness.create_workspace.execute(name="Bob", created_by_user_id=bob.id)

        ada_ctx = await harness.context_for(ada, ada_ws.id)
        victim = await harness.add_document(ada_ctx, "Ada's plans")
        bob_ctx = await harness.context_for(bob, bob_ws.id)

        with pytest.raises(NotFoundError):
            if operation == "rename":
                await harness.update_document.execute(
                    bob_ctx,
                    victim.id,
                    edit=DocumentEdit(title="Owned"),
                    expected_version=victim.version,
                )
            else:
                await harness.delete_document.execute(bob_ctx, victim.id)

        # And the document is untouched.
        still_there = await harness.get_document.execute(ada_ctx, victim.id)
        assert still_there.title == "Ada's plans"

    async def test_a_forged_context_is_not_a_way_in(self, harness: Harness) -> None:
        """Hand-constructing an `AccessContext` is not the attack surface.

        A repository given a context trusts it -- that is the contract. What
        stops an attacker is that they cannot obtain one: the only producer is
        `ResolveAccessContext`, which reads the membership table. This test
        pins that boundary so nobody "fixes" the repository by adding a
        redundant check and concludes the gate is somewhere it is not.
        """
        ada = await harness.sign_up("ada@example.com")
        bob = await harness.sign_up("bob@example.com")
        ada_ws = await harness.create_workspace.execute(name="Ada", created_by_user_id=ada.id)

        forged = AccessContext(user_id=bob.id, workspace_id=ada_ws.id, role=Role.OWNER)

        # The context Bob could actually obtain: none.
        with pytest.raises(NotFoundError):
            await harness.resolve.execute(user_id=bob.id, workspace_id=ada_ws.id)

        # Ada, meanwhile, resolves hers legitimately.
        assert (await harness.context_for(ada, ada_ws.id)).role is Role.OWNER
        assert forged.workspace_id == ada_ws.id  # the forgery is inert without the gate


# ---------------------------------------------------------------------------
# Privilege escalation
# ---------------------------------------------------------------------------


class TestPrivilegeEscalation:
    async def test_a_viewer_cannot_modify_documents(self, harness: Harness) -> None:
        owner = await harness.sign_up("owner@example.com")
        viewer = await harness.sign_up("viewer@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        await harness.invite_member.execute(owner_ctx, user_id=viewer.id, role=Role.VIEWER)
        document = await harness.add_document(owner_ctx, "Notes")

        viewer_ctx = await harness.context_for(viewer, workspace.id)
        with pytest.raises(PermissionDeniedError):
            await harness.update_document.execute(
                viewer_ctx,
                document.id,
                edit=DocumentEdit(title="Renamed"),
                expected_version=document.version,
            )

    async def test_a_member_cannot_invite_other_members(self, harness: Harness) -> None:
        owner = await harness.sign_up("owner@example.com")
        member = await harness.sign_up("member@example.com")
        outsider = await harness.sign_up("outsider@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        await harness.invite_member.execute(owner_ctx, user_id=member.id, role=Role.MEMBER)

        member_ctx = await harness.context_for(member, workspace.id)
        with pytest.raises(PermissionDeniedError):
            await harness.invite_member.execute(member_ctx, user_id=outsider.id, role=Role.MEMBER)

    async def test_a_member_cannot_promote_themselves(self, harness: Harness) -> None:
        """The canonical escalation: change your own row to a higher role."""
        owner = await harness.sign_up("owner@example.com")
        member = await harness.sign_up("member@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        await harness.invite_member.execute(owner_ctx, user_id=member.id, role=Role.MEMBER)

        member_ctx = await harness.context_for(member, workspace.id)
        with pytest.raises(PermissionDeniedError):
            await harness.change_member_role.execute(member_ctx, user_id=member.id, role=Role.OWNER)

        # And the role really is unchanged, not merely un-returned.
        refreshed = await harness.context_for(member, workspace.id)
        assert refreshed.role is Role.MEMBER

    async def test_an_admin_cannot_delete_the_workspace(self, harness: Harness) -> None:
        """Admin manages people and settings; only an owner destroys the
        container itself.

        Renaming *is* an admin capability, so the pair below is the real
        boundary: the same principal succeeds at one and is refused the
        other, which a one-sided test could not show.
        """
        owner = await harness.sign_up("owner@example.com")
        admin = await harness.sign_up("admin@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        await harness.invite_member.execute(owner_ctx, user_id=admin.id, role=Role.ADMIN)

        admin_ctx = await harness.context_for(admin, workspace.id)
        await harness.rename_workspace.execute(
            admin_ctx, name="Renamed", expected_version=workspace.version
        )

        with pytest.raises(PermissionDeniedError):
            await harness.delete_workspace.execute(admin_ctx)

    async def test_a_viewer_cannot_remove_the_owner(self, harness: Harness) -> None:
        owner = await harness.sign_up("owner@example.com")
        viewer = await harness.sign_up("viewer@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        await harness.invite_member.execute(owner_ctx, user_id=viewer.id, role=Role.VIEWER)

        viewer_ctx = await harness.context_for(viewer, workspace.id)
        with pytest.raises(PermissionDeniedError):
            await harness.remove_member.execute(viewer_ctx, user_id=owner.id)


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


class TestPasswordReset:
    async def test_an_unknown_address_produces_no_observable_difference(
        self, harness: Harness
    ) -> None:
        """Neither call raises, and only the known address produces a message.

        The email count is the assertion that matters: the caller sees the
        same nothing either way, so the only way to tell the branches apart
        from inside the test is by the side effect the caller cannot see.
        """
        await harness.sign_up("ada@example.com")

        await harness.request_reset.execute(email="ada@example.com")
        await harness.request_reset.execute(email="nobody@example.com")

        assert len(harness.email.sent) == 1
        assert harness.email.sent[0].to == "ada@example.com"

    async def test_the_emailed_token_sets_a_new_password(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        await harness.request_reset.execute(email="ada@example.com")
        token = _token_from_url(harness.email.last_body())

        await harness.complete_reset.execute(token=token, new_password="a-brand-new-passphrase")

        with pytest.raises(InvalidCredentialsError):
            await harness.sign_in("ada@example.com")
        assert await harness.login.execute(
            email="ada@example.com", password="a-brand-new-passphrase"
        )

    async def test_a_token_cannot_be_redeemed_twice(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        await harness.request_reset.execute(email="ada@example.com")
        token = _token_from_url(harness.email.last_body())

        await harness.complete_reset.execute(token=token, new_password="first-new-passphrase")
        with pytest.raises(Exception, match="invalid or has expired"):
            await harness.complete_reset.execute(token=token, new_password="second-passphrase")

    async def test_an_expired_token_is_rejected(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        await harness.request_reset.execute(email="ada@example.com")
        token = _token_from_url(harness.email.last_body())

        harness.clock.advance(timedelta(hours=1))

        with pytest.raises(Exception, match="invalid or has expired"):
            await harness.complete_reset.execute(token=token, new_password="a-new-passphrase")

    async def test_requesting_a_second_reset_invalidates_the_first(self, harness: Harness) -> None:
        """An old message recovered from a mailbox must not still work."""
        await harness.sign_up("ada@example.com")
        await harness.request_reset.execute(email="ada@example.com")
        first = _token_from_url(harness.email.last_body())
        await harness.request_reset.execute(email="ada@example.com")
        second = _token_from_url(harness.email.last_body())

        assert first != second
        with pytest.raises(Exception, match="invalid or has expired"):
            await harness.complete_reset.execute(token=first, new_password="a-new-passphrase")
        await harness.complete_reset.execute(token=second, new_password="a-new-passphrase")

    async def test_a_verification_token_cannot_be_spent_as_a_reset(self, harness: Harness) -> None:
        """Purpose is part of the lookup, so cross-flow redemption cannot happen.

        Without the purpose predicate, an email-verification link -- which is
        long-lived and unauthenticated -- would be a password-reset link.
        """
        user = await harness.sign_up("ada@example.com")
        await harness.request_verification.execute(user_id=user.id)
        verification_token = _token_from_url(harness.email.last_body())

        with pytest.raises(Exception, match="invalid or has expired"):
            await harness.complete_reset.execute(
                token=verification_token, new_password="a-new-passphrase"
            )

    async def test_a_reset_kills_every_existing_session(self, harness: Harness) -> None:
        """Both halves: access tokens by epoch, refresh tokens by revocation.

        A refresh token carries no epoch, so bumping the epoch alone would
        leave a stolen one able to mint fresh access tokens straight through
        the reset that was supposed to stop it.
        """
        await harness.sign_up("ada@example.com")
        session = await harness.sign_in("ada@example.com")

        await harness.request_reset.execute(email="ada@example.com")
        token = _token_from_url(harness.email.last_body())
        await harness.complete_reset.execute(token=token, new_password="a-new-passphrase")

        with pytest.raises(AuthenticationRequiredError):
            await harness.authenticate.execute(session.access_token)
        with pytest.raises(AuthenticationRequiredError):
            await harness.refresh.execute(refresh_token=session.refresh_token)

    async def test_only_the_hash_of_the_token_is_stored(self, harness: Harness) -> None:
        """A database read must not yield anything redeemable."""
        await harness.sign_up("ada@example.com")
        await harness.request_reset.execute(email="ada@example.com")
        token = _token_from_url(harness.email.last_body())

        stored = list(harness.uow_factory.state.account_tokens.values())
        assert len(stored) == 1
        assert stored[0].token_hash != token
        assert stored[0].token_hash == hash_account_token(token)

    async def test_a_delivery_failure_does_not_surface_to_the_caller(
        self, harness: Harness
    ) -> None:
        """Otherwise the error itself is the enumeration oracle.

        The unknown-address branch cannot fail, so a propagated delivery error
        would mean "this address exists" to anyone watching.
        """
        failing = FailingEmailSender()
        request = RequestPasswordReset(
            harness.uow_factory,
            AccountLinkMailer(failing, _RESET_TEMPLATE),
            harness.audit,
            AuthRateLimitGuard(
                harness.limiter,
                RateLimitPolicy.for_account_email(harness.settings, scope="password_reset"),
                harness.audit,
            ),
            harness.clock,
        )
        await harness.sign_up("ada@example.com")

        # No exception escapes, and the send genuinely was attempted -- a test
        # that only checked for the absence of an exception would also pass if
        # the mailer had been skipped entirely.
        await request.execute(email="ada@example.com")
        assert failing.attempts == 1

    async def test_reset_requests_are_rate_limited(self, harness: Harness) -> None:
        """Each accepted request mails an address the caller chose."""
        await harness.sign_up("ada@example.com")
        for _ in range(harness.settings.account_email_rate_limit_per_account):
            await harness.request_reset.execute(email="ada@example.com")

        with pytest.raises(RateLimitedError):
            await harness.request_reset.execute(email="ada@example.com")


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------


class TestEmailVerification:
    async def test_a_new_account_starts_unverified(self, harness: Harness) -> None:
        user = await harness.sign_up("ada@example.com")
        assert user.email_verified_at is None

    async def test_the_emailed_token_marks_the_address_verified(self, harness: Harness) -> None:
        user = await harness.sign_up("ada@example.com")
        await harness.request_verification.execute(user_id=user.id)
        token = _token_from_url(harness.email.last_body())

        await harness.verify_email.execute(token=token)

        async with harness.uow_factory() as uow:
            refreshed = await uow.users.get(user.id)
        assert refreshed is not None
        assert refreshed.email_verified_at is not None
        assert harness.audit.count(AuditAction.EMAIL_VERIFIED) == 1

    async def test_verification_does_not_grant_a_session(self, harness: Harness) -> None:
        """It proves control of an address, nothing more.

        The endpoint is unauthenticated because the link is followed from a
        mail client; returning a session from it would make an emailed link a
        login credential.
        """
        user = await harness.sign_up("ada@example.com")
        await harness.request_verification.execute(user_id=user.id)
        token = _token_from_url(harness.email.last_body())

        await harness.verify_email.execute(token=token)

        # No session was minted: verification proves control of an address,
        # and an emailed link must never be a login credential.
        assert harness.uow_factory.state.refresh_tokens == {}

    async def test_a_token_cannot_be_redeemed_twice(self, harness: Harness) -> None:
        user = await harness.sign_up("ada@example.com")
        await harness.request_verification.execute(user_id=user.id)
        token = _token_from_url(harness.email.last_body())

        await harness.verify_email.execute(token=token)
        with pytest.raises(Exception, match="invalid or has expired"):
            await harness.verify_email.execute(token=token)

    async def test_a_reset_token_cannot_be_spent_as_a_verification(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        await harness.request_reset.execute(email="ada@example.com")
        reset_token = _token_from_url(harness.email.last_body())

        with pytest.raises(Exception, match="invalid or has expired"):
            await harness.verify_email.execute(token=reset_token)

    async def test_an_already_verified_address_is_not_mailed_again(self, harness: Harness) -> None:
        """Resending would issue a live token for an address needing no proof."""
        user = await harness.sign_up("ada@example.com")
        await harness.request_verification.execute(user_id=user.id)
        await harness.verify_email.execute(token=_token_from_url(harness.email.last_body()))

        sent_before = len(harness.email.sent)
        await harness.request_verification.execute(user_id=user.id)
        assert len(harness.email.sent) == sent_before

    async def test_an_unknown_user_id_is_a_silent_no_op(self, harness: Harness) -> None:
        await harness.request_verification.execute(user_id=uuid.uuid4())
        assert harness.email.sent == []


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    async def test_the_significant_events_are_all_recorded(self, harness: Harness) -> None:
        await harness.sign_up("ada@example.com")
        session = await harness.sign_in("ada@example.com")
        await harness.refresh.execute(refresh_token=session.refresh_token)
        await harness.logout.execute(refresh_token=session.refresh_token)

        recorded = set(harness.audit.actions())
        assert {
            AuditAction.USER_REGISTERED,
            AuditAction.LOGIN_SUCCEEDED,
            AuditAction.SESSION_REFRESHED,
            AuditAction.LOGOUT,
        } <= recorded

    async def test_no_audit_record_ever_carries_a_credential(self, harness: Harness) -> None:
        """The whole trail is scanned, not one field of one event.

        Audit records are retained far longer than logs, so a password or
        token landing in one is a durable leak rather than a transient one.
        """
        await harness.sign_up("ada@example.com")
        session = await harness.sign_in("ada@example.com")
        with pytest.raises(InvalidCredentialsError):
            await harness.login.execute(email="ada@example.com", password="wrong-password")
        await harness.request_reset.execute(email="ada@example.com")
        reset_token = _token_from_url(harness.email.last_body())

        haystack = " ".join(repr(event) for event in harness.audit.events)
        for secret in (_PASSWORD, "wrong-password", session.access_token, reset_token):
            assert secret not in haystack

    async def test_a_password_reset_request_is_audited_even_for_an_unknown_address(
        self, harness: Harness
    ) -> None:
        """Invisible to the caller, visible to an operator reviewing the log.

        This is where an enumeration sweep becomes detectable: the requests
        all look identical from outside, and identical from outside is exactly
        what makes the internal record the only place the pattern shows.
        """
        await harness.request_reset.execute(email="nobody@example.com")

        events = [
            e for e in harness.audit.events if e.action is AuditAction.PASSWORD_RESET_REQUESTED
        ]
        assert len(events) == 1
        assert events[0].metadata["outcome"] == "no_matching_account"
        assert events[0].actor_user_id is None


# ---------------------------------------------------------------------------
# Degraded dependencies
# ---------------------------------------------------------------------------


class TestRateLimiterOutage:
    async def test_authentication_still_works_when_the_limiter_backend_is_down(
        self, harness: Harness
    ) -> None:
        """Fail open, deliberately (infrastructure/cache/rate_limiter.py).

        Failing closed would turn a Redis outage into a total authentication
        outage -- nobody can sign in, including the operators fixing it.
        Argon2id still makes each attempt expensive, and `/readyz` reports
        Redis down, so the degraded window is bounded and visible.
        """
        login = LoginUser(
            harness.uow_factory,
            harness.settings,
            UnavailableRateLimiter(),
            harness.audit,
            harness.clock,
        )
        await harness.sign_up("ada@example.com")

        session = await login.execute(email="ada@example.com", password=_PASSWORD)
        assert session.user.email == "ada@example.com"


class TestTenancyAuditTrail:
    """Membership changes grant and revoke access to everything in a
    workspace, so they are what an incident review reconstructs -- and the
    `audit_logs` table carries no foreign keys, so the record survives the
    purge of whatever it describes.
    """

    async def test_adding_a_member_records_who_added_whom_at_what_role(
        self, harness: Harness
    ) -> None:
        owner = await harness.sign_up("owner@example.com")
        guest = await harness.sign_up("guest@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)

        await harness.invite_member.execute(owner_ctx, user_id=guest.id, role=Role.MEMBER)

        event = _only(harness, AuditAction.MEMBER_ADDED)
        assert event.actor_user_id == owner.id
        assert event.resource_id == guest.id
        assert event.workspace_id == workspace.id
        assert event.metadata["role"] == Role.MEMBER.value

    async def test_a_role_change_records_both_the_old_and_new_role(self, harness: Harness) -> None:
        """Only the pair distinguishes a promotion from a demotion when the
        trail is read months later."""
        owner = await harness.sign_up("owner@example.com")
        member = await harness.sign_up("member@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        await harness.invite_member.execute(owner_ctx, user_id=member.id, role=Role.MEMBER)

        await harness.change_member_role.execute(owner_ctx, user_id=member.id, role=Role.ADMIN)

        event = _only(harness, AuditAction.MEMBER_ROLE_CHANGED)
        assert event.metadata["from_role"] == Role.MEMBER.value
        assert event.metadata["to_role"] == Role.ADMIN.value

    async def test_removing_a_member_is_recorded(self, harness: Harness) -> None:
        owner = await harness.sign_up("owner@example.com")
        guest = await harness.sign_up("guest@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        await harness.invite_member.execute(owner_ctx, user_id=guest.id, role=Role.MEMBER)

        await harness.remove_member.execute(owner_ctx, user_id=guest.id)

        assert _only(harness, AuditAction.MEMBER_REMOVED).resource_id == guest.id

    async def test_deleting_a_workspace_is_recorded(self, harness: Harness) -> None:
        owner = await harness.sign_up("owner@example.com")
        workspace = await harness.create_workspace.execute(
            name="Doomed", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)

        await harness.delete_workspace.execute(owner_ctx)

        event = _only(harness, AuditAction.WORKSPACE_DELETED)
        assert event.actor_user_id == owner.id
        assert event.workspace_id == workspace.id

    async def test_a_refused_membership_change_records_no_change(self, harness: Harness) -> None:
        """The audit record must describe what happened, not what was tried.

        A `MEMBER_ROLE_CHANGED` written on a rejected attempt would read, later,
        as evidence that the escalation succeeded.
        """
        owner = await harness.sign_up("owner@example.com")
        member = await harness.sign_up("member@example.com")
        workspace = await harness.create_workspace.execute(
            name="Shared", created_by_user_id=owner.id
        )
        owner_ctx = await harness.context_for(owner, workspace.id)
        await harness.invite_member.execute(owner_ctx, user_id=member.id, role=Role.MEMBER)

        member_ctx = await harness.context_for(member, workspace.id)
        with pytest.raises(PermissionDeniedError):
            await harness.change_member_role.execute(member_ctx, user_id=member.id, role=Role.OWNER)

        assert harness.audit.count(AuditAction.MEMBER_ROLE_CHANGED) == 0


def _only(harness: Harness, action: AuditAction) -> AuditEvent:
    events = [event for event in harness.audit.events if event.action is action]
    assert len(events) == 1, f"expected exactly one {action}, got {len(events)}"
    return events[0]
