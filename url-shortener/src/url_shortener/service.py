"""Use cases: minify and expand. No I/O besides the repository."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Protocol

from url_shortener.codes import CodeGenerator, generate_code
from url_shortener.config import Settings
from url_shortener.errors import (
    ClaimConflictError,
    CodeGenerationError,
    LinkExpiredError,
    LinkNotFoundError,
)
from url_shortener.models import Link
from url_shortener.urls import parse_long_url, parse_short_url

# One attempt collides with probability live_links / 62**7, about 0.03% even
# with a billion live links, so five attempts are plenty. The bound guarantees
# termination if the code space is (mis)configured to be tiny.
MAX_CLAIM_ATTEMPTS = 5

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


class LinkRepository(Protocol):
    """The storage this service needs, defined here by its consumer.

    The service depends on this interface only; ``MongoLinkRepository``
    satisfies it structurally and imports nothing from this module. Every
    implementation must pass the shared contract tests in
    ``tests/repository_contract.py``.

    Lookups return active and expired links alike: expiry is the service's
    rule, not storage's. Storage may delete expired links after a retention
    period, after which lookups return None.
    """

    def find_by_code(self, code: str) -> Link | None:
        """The link holding ``code`` (case-sensitive), or None."""

    def find_by_key(self, key: str) -> Link | None:
        """The link for the canonical URL ``key``, or None."""

    def claim(self, *, key: str, url: str, code: str, now: datetime, expires_at: datetime) -> None:
        """Atomically store a link binding ``key`` to ``code``.

        A link for ``key`` that expired at or before ``now`` is replaced, which
        retires its old code. Raises ``ClaimConflictError`` and changes nothing
        if ``key`` has an active link, or if ``code`` belongs to another key's
        link, active or expired.
        """


def utc_now() -> datetime:
    """Current UTC time truncated to milliseconds.

    BSON dates have millisecond precision. Truncating here means the value we
    compare against is exactly the value MongoDB stores, so a link is never
    "expired" in memory but "active" in the database, or the reverse.
    """
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


class ShortenerService:
    def __init__(
        self,
        repository: LinkRepository,
        settings: Settings,
        *,
        clock: Clock = utc_now,
        code_generator: CodeGenerator | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._clock = clock
        self._generate = code_generator or partial(generate_code, settings.code_length)

    def minify(self, raw_url: str) -> str:
        """Return the short URL for ``raw_url``, reusing its link while it is valid."""
        long_url = parse_long_url(raw_url, base_url=self._settings.base_url)
        ttl = timedelta(seconds=self._settings.ttl_seconds)

        for attempt in range(1, MAX_CLAIM_ATTEMPTS + 1):
            now = self._clock()
            # Fast path, and the common case for a repeated URL: no write at all.
            existing = self._repository.find_by_key(long_url.key)
            if existing is not None and existing.is_active(now):
                logger.debug(
                    "Reusing code %s for %s, valid until %s",
                    existing.code,
                    long_url.key,
                    existing.expires_at.isoformat(),
                )
                return self._short_url(existing.code)

            code = self._generate()
            try:
                self._repository.claim(
                    key=long_url.key,
                    url=long_url.original,
                    code=code,
                    now=now,
                    expires_at=now + ttl,
                )
            except ClaimConflictError:
                # Either another process created an active link for this URL
                # between our read and our write (the next iteration's read
                # returns it), or the random code is taken (we draw a new one).
                logger.debug(
                    "Claim of code %s conflicted (attempt %d of %d), retrying",
                    code,
                    attempt,
                    MAX_CLAIM_ATTEMPTS,
                )
                continue
            logger.info(
                "Created code %s for %s, valid until %s",
                code,
                long_url.key,
                (now + ttl).isoformat(),
            )
            return self._short_url(code)

        raise CodeGenerationError(
            f"Could not allocate a short code after {MAX_CLAIM_ATTEMPTS} attempts; "
            "consider increasing SHORTENER_CODE_LENGTH."
        )

    def expand(self, raw_short_url: str) -> str:
        """Return the URL behind ``raw_short_url`` if its link exists and is valid."""
        code = parse_short_url(raw_short_url, base_url=self._settings.base_url)
        link = self._repository.find_by_code(code)
        if link is None:
            raise LinkNotFoundError(self._short_url(code))
        if not link.is_active(self._clock()):
            raise LinkExpiredError(self._short_url(code), link.expires_at)
        logger.debug("Code %s expands to %s", code, link.url)
        return link.url

    def _short_url(self, code: str) -> str:
        return f"{self._settings.base_url}/{code}"
