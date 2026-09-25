"""Configuration validation.

Every case here represents a misconfiguration that would otherwise be silent
until it caused damage: a weak signing key, a wildcard CORS origin that breaks
credentialed requests, SQL echo leaking user data into production logs. The
process must refuse to start rather than run subtly wrong.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orbit.core.config import AIProvider, EmailProvider, Environment, LogFormat, Settings
from tests.conftest import build_settings


def test_defaults_are_safe_for_development() -> None:
    settings = build_settings()
    assert settings.env is Environment.TEST
    assert settings.ai_provider is AIProvider.FAKE, "must not require credentials by default"
    assert settings.cors_allowed_origins == []
    assert settings.db_echo is False


class TestSecretKey:
    def test_rejects_a_short_key(self) -> None:
        with pytest.raises(ValidationError, match="at least 32 characters"):
            build_settings(secret_key="too-short")

    def test_rejects_the_placeholder_in_production(self) -> None:
        with pytest.raises(ValidationError, match="placeholder"):
            build_settings(
                env=Environment.PRODUCTION,
                log_format=LogFormat.JSON,
                secret_key="REPLACE_ME_WITH_A_64_BYTE_URLSAFE_RANDOM_STRING",
            )

    def test_accepts_a_sufficiently_long_key(self) -> None:
        settings = build_settings(secret_key="x" * 32)
        assert len(settings.secret_key) == 32


class TestCors:
    def test_rejects_wildcard_origin(self) -> None:
        # Browsers reject `Access-Control-Allow-Origin: *` on credentialed
        # requests, so this would silently break authentication.
        with pytest.raises(ValidationError, match="may not contain"):
            build_settings(cors_allowed_origins=["*"])

    def test_rejects_any_origin_in_production(self) -> None:
        # ORBIT is single-origin in production (ADR-0009); nothing should be
        # cross-origin, so a populated allow-list means a misconfigured deploy.
        with pytest.raises(ValidationError, match="must be empty in production"):
            build_settings(
                env=Environment.PRODUCTION,
                log_format=LogFormat.JSON,
                cors_allowed_origins=["https://app.example.com"],
            )

    def test_allows_explicit_origins_in_development(self) -> None:
        settings = build_settings(
            env=Environment.DEVELOPMENT, cors_allowed_origins=["http://localhost:3000"]
        )
        assert settings.cors_allowed_origins == ["http://localhost:3000"]

    def test_parses_the_comma_separated_form(self) -> None:
        """The form an environment variable can actually express.

        pydantic-settings JSON-decodes list fields by default, which made the
        exact value documented in .env.example unparseable and prevented the
        process from starting at all.
        """
        settings = build_settings(
            env=Environment.DEVELOPMENT,
            cors_allowed_origins="http://localhost:3000, http://127.0.0.1:3000",
        )
        assert settings.cors_allowed_origins == [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]

    def test_parses_a_single_origin_string(self) -> None:
        settings = build_settings(
            env=Environment.DEVELOPMENT, cors_allowed_origins="http://localhost:3000"
        )
        assert settings.cors_allowed_origins == ["http://localhost:3000"]

    def test_empty_string_yields_no_origins(self) -> None:
        settings = build_settings(env=Environment.DEVELOPMENT, cors_allowed_origins="")
        assert settings.cors_allowed_origins == []

    def test_tolerates_the_json_array_form(self) -> None:
        # Some deployment tooling emits JSON for list-valued variables.
        settings = build_settings(
            env=Environment.DEVELOPMENT,
            cors_allowed_origins='["http://localhost:3000"]',
        )
        assert settings.cors_allowed_origins == ["http://localhost:3000"]

    def test_rejects_wildcard_supplied_as_a_string(self) -> None:
        with pytest.raises(ValidationError, match="may not contain"):
            build_settings(env=Environment.DEVELOPMENT, cors_allowed_origins="*")


class TestAIProvider:
    def test_openai_requires_an_api_key(self) -> None:
        with pytest.raises(ValidationError, match="ORBIT_OPENAI_API_KEY"):
            build_settings(ai_provider=AIProvider.OPENAI, openai_api_key=None)

    def test_openai_accepted_when_fully_configured(self) -> None:
        settings = build_settings(**_OPENAI)
        assert settings.ai_provider is AIProvider.OPENAI

    @pytest.mark.parametrize(
        ("missing", "variable"),
        [
            ("openai_api_key", "ORBIT_OPENAI_API_KEY"),
            ("openai_base_url", "ORBIT_OPENAI_BASE_URL"),
            ("embedding_model", "ORBIT_EMBEDDING_MODEL"),
        ],
    )
    def test_openai_hardcodes_no_key_endpoint_or_model(self, missing: str, variable: str) -> None:
        with pytest.raises(ValidationError, match=variable):
            build_settings(**{**_OPENAI, missing: None})

    def test_a_blank_model_is_as_good_as_missing(self) -> None:
        with pytest.raises(ValidationError, match="ORBIT_EMBEDDING_MODEL"):
            build_settings(**{**_OPENAI, "embedding_model": "   "})

    def test_the_api_key_never_appears_in_a_repr(self) -> None:
        settings = build_settings(**_OPENAI)
        assert "sk-test-secret" not in repr(settings)
        assert "sk-test-secret" not in str(settings.model_dump())
        assert settings.openai_api_key is not None
        assert settings.openai_api_key.get_secret_value() == "sk-test-secret"

    def test_production_refuses_a_plaintext_embedding_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="https"):
            build_settings(
                **{**_OPENAI, "openai_base_url": "http://gateway.internal/v1"},
                env=Environment.PRODUCTION,
                log_format=LogFormat.JSON,
            )

    def test_fake_provider_needs_no_credentials(self) -> None:
        settings = build_settings(ai_provider=AIProvider.FAKE, openai_api_key=None)
        assert settings.ai_provider is AIProvider.FAKE


class TestEmbeddingDimensions:
    def test_the_width_is_required_and_never_defaulted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORBIT_EMBEDDING_DIMENSIONS", raising=False)
        everything_else: dict[str, object] = {
            "database_url": "postgresql+asyncpg://u:p@localhost/db",
            "redis_url": "redis://localhost:6379/0",
            "celery_broker_url": "redis://localhost:6379/1",
            "celery_result_backend": "redis://localhost:6379/2",
            "s3_bucket": "b",
            "s3_access_key_id": "a",
            "s3_secret_access_key": "s",
            "secret_key": "x" * 64,
        }
        with pytest.raises(ValidationError, match="embedding_dimensions"):
            Settings(_env_file=None, **everything_else)  # type: ignore[arg-type]

    @pytest.mark.parametrize("dimensions", [0, 2001])
    def test_a_width_hnsw_cannot_index_is_refused(self, dimensions: int) -> None:
        with pytest.raises(ValidationError):
            build_settings(embedding_dimensions=dimensions)


_OPENAI: dict[str, object] = {
    "ai_provider": AIProvider.OPENAI,
    "openai_api_key": "sk-test-secret",
    "openai_base_url": "https://gateway.example.test/v1",
    "embedding_model": "text-embedding-3-small",
}


class TestProductionHardening:
    def test_rejects_sql_echo(self) -> None:
        # Echoed SQL includes bound parameters, which means user data in logs.
        with pytest.raises(ValidationError, match="ORBIT_DB_ECHO"):
            build_settings(env=Environment.PRODUCTION, log_format=LogFormat.JSON, db_echo=True)

    def test_requires_json_logs(self) -> None:
        with pytest.raises(ValidationError, match="must be 'json' in production"):
            build_settings(env=Environment.PRODUCTION, log_format=LogFormat.CONSOLE)

    def test_rejects_the_console_email_sender_in_production(self) -> None:
        # The console sender writes one-time account links into the log
        # stream, which turns log access into account takeover.
        with pytest.raises(ValidationError, match="forbidden in production"):
            build_settings(
                env=Environment.PRODUCTION,
                log_format=LogFormat.JSON,
                email_provider=EmailProvider.CONSOLE,
                password_reset_url_template="https://orbit.example/r?token={token}",
                email_verification_url_template="https://orbit.example/v?token={token}",
            )

    def test_rejects_plaintext_account_link_templates_in_production(self) -> None:
        with pytest.raises(ValidationError, match="must use https"):
            build_settings(
                env=Environment.PRODUCTION,
                log_format=LogFormat.JSON,
                password_reset_url_template="http://orbit.example/r?token={token}",
                email_verification_url_template="https://orbit.example/v?token={token}",
            )

    def test_accepts_a_correct_production_configuration(self) -> None:
        settings = build_settings(
            env=Environment.PRODUCTION,
            log_format=LogFormat.JSON,
            db_echo=False,
            cors_allowed_origins=[],
            password_reset_url_template="https://orbit.example/reset?token={token}",
            email_verification_url_template="https://orbit.example/verify?token={token}",
        )
        assert settings.is_production is True


class TestAccountLinkTemplates:
    """A template with no `{token}` mails a link that cannot possibly work.

    Caught at startup rather than at the moment a locked-out user needs it --
    the worst possible time to discover the configuration is wrong.
    """

    @pytest.mark.parametrize(
        "field",
        ["password_reset_url_template", "email_verification_url_template"],
    )
    def test_a_template_without_the_placeholder_is_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError, match=r"must contain the '\{token\}' placeholder"):
            build_settings(**{field: "https://orbit.example/reset"})


class TestBounds:
    @pytest.mark.parametrize("ttl", [59, 3601])
    def test_access_token_ttl_is_bounded(self, ttl: int) -> None:
        # Too short is unusable; too long defeats the point of a short-lived
        # token, which is the only thing bounding a leak (ADR-0003).
        with pytest.raises(ValidationError):
            build_settings(access_token_ttl_seconds=ttl)

    def test_upload_limit_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            build_settings(max_upload_bytes=10)

    def test_pool_size_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            build_settings(db_pool_size=0)
