import logging

import mongomock
import pytest
from bson.codec_options import CodecOptions
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from url_shortener.errors import DatabaseUnavailableError, StorageError
from url_shortener.repository import COLLECTION_NAME, MongoLinkRepository
from url_shortener.service import ShortenerService

URL = "https://www.example.com/path?q=search"
DRIVER_MESSAGE = (
    "db:27017: [Errno 111] Connection refused (configured timeouts: socketTimeoutMS: "
    "3000.0ms, connectTimeoutMS: 3000.0ms), Timeout: 3.0s, Topology Description: "
    "<TopologyDescription id: 1, topology_type: Unknown, servers: [...]>"
)


class UnreachableCollection:
    codec_options = CodecOptions(tz_aware=True)

    def find_one(self, *args, **kwargs):
        raise ServerSelectionTimeoutError(DRIVER_MESSAGE)


def test_unreachable_server_is_reported_in_one_short_line():
    repository = MongoLinkRepository(UnreachableCollection(), expired_retention_seconds=0)
    with pytest.raises(DatabaseUnavailableError) as info:
        repository.find_by_code("abcd123")
    assert str(info.value) == "MongoDB is unreachable: db:27017: [Errno 111] Connection refused"


def test_full_driver_message_is_logged_for_debugging(caplog):
    caplog.set_level(logging.DEBUG, logger="url_shortener")
    repository = MongoLinkRepository(UnreachableCollection(), expired_retention_seconds=0)
    with pytest.raises(DatabaseUnavailableError):
        repository.find_by_code("abcd123")
    assert "Topology Description" in caplog.text


def test_unreachable_database_is_reported_by_address_without_credentials(settings):
    # Nothing listens on port 1, so this needs no database and fails within the timeout.
    repository = MongoLinkRepository.connect(
        "mongodb://admin:s3cret@127.0.0.1:1", "x", timeout_ms=200, expired_retention_seconds=0
    )
    try:
        with pytest.raises(DatabaseUnavailableError) as info:
            ShortenerService(repository, settings).minify(URL)
    finally:
        repository.close()
    assert str(info.value).startswith("MongoDB is unreachable: 127.0.0.1:1: ")
    assert "s3cret" not in str(info.value)
    assert "Topology" not in str(info.value)


def test_other_driver_errors_become_storage_errors(mongo_repository, monkeypatch):
    def fail(*args, **kwargs):
        raise OperationFailure("not authorized on url_shortener")

    monkeypatch.setattr(mongo_repository._collection, "find_one", fail)
    with pytest.raises(StorageError, match="MongoDB error: not authorized") as info:
        mongo_repository.find_by_code("abcdefg")
    assert not isinstance(info.value, DatabaseUnavailableError)


def test_document_missing_a_field_is_a_storage_error(mongo_repository):
    """A document edited by hand is reported, not raised as a KeyError traceback."""
    mongo_repository._collection.insert_one({"_id": "https://example.com/", "code": "abcdefg"})
    with pytest.raises(StorageError, match="missing the field 'url'"):
        mongo_repository.find_by_code("abcdefg")


def test_rejects_a_collection_that_returns_naive_datetimes():
    naive = mongomock.MongoClient().db[COLLECTION_NAME]
    with pytest.raises(ValueError, match="tz_aware"):
        MongoLinkRepository(naive, expired_retention_seconds=0)
