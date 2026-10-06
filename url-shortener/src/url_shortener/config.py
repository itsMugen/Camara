"""Settings, read from environment variables.

The variables are listed in the README ("Configuration") and in
``.env.example``. Every value is validated up front so a typo fails with a
clear message instead of a stack trace halfway through a database call.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from url_shortener.codes import MAX_CODE_LENGTH, MIN_CODE_LENGTH
from url_shortener.errors import ConfigurationError, InvalidUrlError
from url_shortener.urls import validate_base_url

ENV_PREFIX = "SHORTENER_"
# MongoDB stores a TTL index's expireAfterSeconds as a 32-bit integer. The
# same bound (about 68 years) keeps the link lifetime far from the limits of
# datetime arithmetic, which would otherwise fail with an OverflowError.
MAX_SECONDS = 2**31 - 1
# pymongo rejects any *TimeoutMS option of a billion milliseconds or more.
MAX_TIMEOUT_MS = 10**9 - 1
# Characters MongoDB forbids in database names (on any platform), and the
# maximum length in bytes.
DATABASE_NAME_FORBIDDEN = frozenset('/\\. "$*<>:|?\0')
MAX_DATABASE_NAME_BYTES = 63


@dataclass(frozen=True)
class Settings:
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database: str = "url_shortener"
    base_url: str = "https://myurlshortener.com"
    ttl_seconds: int = 3600
    """N: how long a short URL stays valid after it is created."""
    code_length: int = 7
    expired_retention_seconds: int = 86400
    """How long expired links are kept so ``--expand`` can say "expired"
    rather than "not found". MongoDB's TTL monitor deletes them afterwards."""
    mongo_timeout_ms: int = 3000
    """Fail fast when MongoDB is unreachable instead of hanging for 30 s."""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        env = os.environ if env is None else env
        defaults = cls()

        def text(name: str, default: str) -> str:
            value = env.get(ENV_PREFIX + name, "").strip()
            return value or default

        def integer(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
            raw = env.get(ENV_PREFIX + name, "").strip()
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError:
                raise ConfigurationError(
                    f"{ENV_PREFIX}{name} must be an integer, got {raw!r}."
                ) from None
            if value < minimum or (maximum is not None and value > maximum):
                bounds = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
                raise ConfigurationError(f"{ENV_PREFIX}{name} must be {bounds}, got {value}.")
            return value

        settings = cls(
            mongo_uri=text("MONGO_URI", defaults.mongo_uri),
            mongo_database=text("MONGO_DATABASE", defaults.mongo_database),
            base_url=text("BASE_URL", defaults.base_url).rstrip("/"),
            ttl_seconds=integer("TTL_SECONDS", defaults.ttl_seconds, 1, MAX_SECONDS),
            code_length=integer(
                "CODE_LENGTH", defaults.code_length, MIN_CODE_LENGTH, MAX_CODE_LENGTH
            ),
            expired_retention_seconds=integer(
                "EXPIRED_RETENTION_SECONDS", defaults.expired_retention_seconds, 0, MAX_SECONDS
            ),
            mongo_timeout_ms=integer(
                "MONGO_TIMEOUT_MS", defaults.mongo_timeout_ms, 1, MAX_TIMEOUT_MS
            ),
        )
        _validate_base_url(settings.base_url)
        _validate_database_name(settings.mongo_database)
        return settings


def _validate_base_url(base_url: str) -> None:
    try:
        validate_base_url(base_url)
    except InvalidUrlError as exc:
        raise ConfigurationError(f"{ENV_PREFIX}BASE_URL is invalid: {exc}") from exc


def _validate_database_name(name: str) -> None:
    forbidden = sorted(DATABASE_NAME_FORBIDDEN.intersection(name))
    if forbidden:
        raise ConfigurationError(
            f"{ENV_PREFIX}MONGO_DATABASE must not contain {', '.join(map(repr, forbidden))}."
        )
    if len(name.encode()) > MAX_DATABASE_NAME_BYTES:
        raise ConfigurationError(
            f"{ENV_PREFIX}MONGO_DATABASE must be at most {MAX_DATABASE_NAME_BYTES} bytes long."
        )
