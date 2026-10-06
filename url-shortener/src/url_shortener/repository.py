"""MongoDB persistence.

Data model: one document per *canonical long URL*, in the ``links`` collection::

    {
      "_id":        "https://www.example.com/path?q=search",   # canonical key
      "url":        "https://WWW.example.com/path?q=search",   # as submitted
      "code":       "fstp4Qa",                                 # unique index
      "created_at": ISODate(...),
      "expires_at": ISODate(...)                               # TTL index
    }

Keying documents by the long URL makes the rule "the same URL returns the
same short URL while it is valid" a property of the database rather than of
application code: ``_id`` is unique, so two concurrent ``--minify`` calls for
one URL can never both create a link (see ``claim``). A standalone MongoDB has
no multi-document transactions, so the design deliberately needs only
single-document atomic operations.

The service enforces expiry on every read by comparing ``expires_at`` with the
current time; this module returns expired links like any other. The TTL index
only reclaims disk space: MongoDB's TTL monitor runs about
once a minute and gives no timing guarantee, so it can never be relied on for
correctness.
"""

import logging
import re
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Self

import dns.exception
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.errors import ConfigurationError as MongoConfigurationError
from pymongo.errors import (
    DuplicateKeyError,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)

from url_shortener.errors import (
    ClaimConflictError,
    ConfigurationError,
    DatabaseUnavailableError,
    StorageError,
)
from url_shortener.models import Link

COLLECTION_NAME = "links"
CODE_INDEX = "code_unique"
TTL_INDEX = "expires_at_ttl"
# Server error codes raised when an index exists with different options.
INDEX_OPTIONS_CONFLICT = 85
INDEX_KEY_SPECS_CONFLICT = 86

logger = logging.getLogger(__name__)


@contextmanager
def _driver_errors() -> Iterator[None]:
    """Turn driver errors into domain errors so callers never see pymongo.

    Applied as a decorator to every method that talks to the server: objects
    made by ``@contextmanager`` are also ``ContextDecorator``s.
    """
    try:
        yield
    except ServerSelectionTimeoutError as exc:
        # The user sees the first line; the topology dump is logged for -vv.
        logger.debug("Server selection failed: %s", exc)
        raise DatabaseUnavailableError(f"MongoDB is unreachable: {_first_reason(exc)}") from exc
    except PyMongoError as exc:
        raise StorageError(f"MongoDB error: {exc}") from exc


class MongoLinkRepository:
    def __init__(
        self, collection: Collection[dict[str, Any]], *, expired_retention_seconds: int
    ) -> None:
        if not collection.codec_options.tz_aware:
            # Naive datetimes from the driver cannot be compared with the
            # service's aware clock; the failure would be a TypeError on the
            # first lookup, far from its cause.
            raise ValueError("The collection must come from a MongoClient(tz_aware=True).")
        self._collection = collection
        self._retention = expired_retention_seconds
        self._indexes_ready = False

    @classmethod
    def connect(
        cls, uri: str, database: str, *, timeout_ms: int, expired_retention_seconds: int
    ) -> Self:
        """Create a repository without waiting for the server.

        The driver starts connecting in the background, so an unreachable
        server is reported by the first query, not here. A connection string
        the driver cannot parse or refuses is a configuration error (exit 2),
        not an unavailable database; the one exception is a ``mongodb+srv://``
        name that DNS cannot resolve, which is the network's fault.
        """
        try:
            with warnings.catch_warnings():
                # The driver only *warns* about a URI option it does not
                # understand ("?readPreference=bogus") and then ignores it.
                # Silently running with a dropped option is worse than failing.
                warnings.filterwarnings("error", category=UserWarning, module=r"pymongo\.")
                client: MongoClient[dict[str, Any]] = MongoClient(
                    uri,
                    tz_aware=True,
                    tzinfo=UTC,
                    serverSelectionTimeoutMS=timeout_ms,
                    connectTimeoutMS=timeout_ms,
                    appname="url-shortener",
                )
        except MongoConfigurationError as exc:
            if isinstance(exc.__cause__, dns.exception.DNSException):
                raise StorageError(f"MongoDB error: {exc}") from exc
            raise ConfigurationError(f"SHORTENER_MONGO_URI is invalid: {exc}") from exc
        except (ValueError, TypeError, UserWarning) as exc:  # bad syntax, port or option
            raise ConfigurationError(f"SHORTENER_MONGO_URI is invalid: {exc}") from exc
        # The database name was validated with the rest of the settings.
        return cls(
            client[database][COLLECTION_NAME], expired_retention_seconds=expired_retention_seconds
        )

    def close(self) -> None:
        self._collection.database.client.close()

    @_driver_errors()
    def ensure_indexes(self) -> None:
        """Create the indexes; safe to call on every run (createIndexes is idempotent).

        If the retention period was changed since the TTL index was created,
        the index is updated in place with ``collMod`` instead of failing.
        """
        if self._indexes_ready:
            return
        logger.debug("Ensuring indexes on %s", self._collection.full_name)
        self._collection.create_index([("code", ASCENDING)], name=CODE_INDEX, unique=True)
        try:
            self._collection.create_index(
                [("expires_at", ASCENDING)], name=TTL_INDEX, expireAfterSeconds=self._retention
            )
        except OperationFailure as exc:
            if exc.code not in (INDEX_OPTIONS_CONFLICT, INDEX_KEY_SPECS_CONFLICT):
                raise
            logger.info("Changing expired link retention to %d seconds", self._retention)
            self._collection.database.command(
                "collMod",
                self._collection.name,
                index={"name": TTL_INDEX, "expireAfterSeconds": self._retention},
            )
        self._indexes_ready = True

    @_driver_errors()
    def find_by_code(self, code: str) -> Link | None:
        return _to_link(self._collection.find_one({"code": code}))

    @_driver_errors()
    def find_by_key(self, key: str) -> Link | None:
        return _to_link(self._collection.find_one({"_id": key}))

    @_driver_errors()
    def claim(self, *, key: str, url: str, code: str, now: datetime, expires_at: datetime) -> None:
        """Atomically bind ``code`` to ``key`` unless an active link already exists.

        The filter matches the document only if it is missing or expired:

        * missing: the upsert inserts a new document;
        * expired: the document is overwritten with the new code, which
          retires the old code;
        * active: the filter matches nothing, so the upsert tries to insert a
          second document with the same ``_id``, MongoDB rejects it with a
          duplicate key error and ``ClaimConflictError`` is raised.

        ``ClaimConflictError`` is also raised when ``code`` is already used by
        another URL. The caller tells the two cases apart by reading the
        document back. (A document whose ``expires_at`` was removed by hand
        never matches the filter and so can never be replaced; every claim
        for its key conflicts.)

        Indexes are created before the first write: the unique index on
        ``code`` is what makes code collisions impossible. Reads do not need
        it, so ``--expand`` (and any invalid input) costs no extra round trip.
        """
        self.ensure_indexes()
        try:
            self._collection.update_one(
                {"_id": key, "expires_at": {"$lte": now}},
                {
                    "$set": {
                        "url": url,
                        "code": code,
                        "created_at": now,
                        "expires_at": expires_at,
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError as exc:
            raise ClaimConflictError(str(exc)) from exc


def _first_reason(exc: ServerSelectionTimeoutError) -> str:
    """The first line of the driver's message, without its topology dump.

    pymongo appends the timeouts and a description of every server it tried,
    which is useful in a log but unreadable as a one-line CLI error.
    """
    reason = str(exc).partition(", Timeout: ")[0]
    return re.sub(r"\s*\(configured timeouts: [^)]*\)", "", reason)


def _to_link(document: Mapping[str, Any] | None) -> Link | None:
    if document is None:
        return None
    try:
        return Link(
            code=document["code"],
            url=document["url"],
            key=document["_id"],
            created_at=document["created_at"],
            expires_at=document["expires_at"],
        )
    except KeyError as exc:  # edited by hand, or written by another program
        raise StorageError(
            f"MongoDB document {document.get('_id')!r} is missing the field {exc}."
        ) from exc
