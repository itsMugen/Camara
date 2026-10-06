import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import mongomock
import pytest

from url_shortener.config import Settings
from url_shortener.errors import ClaimConflictError
from url_shortener.models import Link
from url_shortener.repository import COLLECTION_NAME, MongoLinkRepository
from url_shortener.service import ShortenerService

BASE_URL = "https://myurlshortener.com"
# mongomock emulates TTL indexes against the real wall clock, so fake times
# must lie in the future or documents would vanish as soon as they are written.
T0 = datetime(2100, 1, 1, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """A controllable clock, so expiry is tested without sleeping."""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class ScriptedCodes:
    """Returns predefined codes in order, to force collisions deterministically."""

    def __init__(self, *codes: str) -> None:
        self._codes: Iterator[str] = iter(codes)

    def __call__(self) -> str:
        return next(self._codes)


class InMemoryLinkRepository:
    """A second implementation of the service's ``LinkRepository`` interface.

    The service and CLI unit tests run against it, which shows the service
    does not depend on MongoDB. The contract tests in
    ``tests/repository_contract.py`` hold it to the same rules as
    ``MongoLinkRepository``, so the two cannot drift apart. A lock stands in
    for MongoDB's single-document atomicity. Expired links are never deleted
    (no retention period), which the interface allows.
    """

    def __init__(self) -> None:
        self._links: dict[str, Link] = {}  # by key, like the _id of the Mongo documents
        self._lock = threading.Lock()

    def find_by_code(self, code: str) -> Link | None:
        with self._lock:
            return next((link for link in self._links.values() if link.code == code), None)

    def find_by_key(self, key: str) -> Link | None:
        with self._lock:
            return self._links.get(key)

    def claim(self, *, key: str, url: str, code: str, now: datetime, expires_at: datetime) -> None:
        with self._lock:
            current = self._links.get(key)
            if current is not None and current.is_active(now):
                raise ClaimConflictError(f"{key} has an active link")
            if any(link.code == code and link.key != key for link in self._links.values()):
                raise ClaimConflictError(f"code {code} is taken")
            self._links[key] = Link(
                code=code, url=url, key=key, created_at=now, expires_at=expires_at
            )


@pytest.fixture
def settings() -> Settings:
    return Settings(base_url=BASE_URL, ttl_seconds=60, code_length=7)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def repository() -> InMemoryLinkRepository:
    return InMemoryLinkRepository()


@pytest.fixture
def mongo_repository() -> MongoLinkRepository:
    """The real MongoDB adapter, on mongomock."""
    collection = mongomock.MongoClient(tz_aware=True).db[COLLECTION_NAME]
    return MongoLinkRepository(collection, expired_retention_seconds=3600)


@pytest.fixture
def service(
    repository: InMemoryLinkRepository, settings: Settings, clock: FakeClock
) -> ShortenerService:
    return ShortenerService(repository, settings, clock=clock)
